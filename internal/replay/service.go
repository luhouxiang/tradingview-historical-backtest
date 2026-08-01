package replay

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/tvbt/tradingview-historical-backtest/internal/calculation"
	"github.com/tvbt/tradingview-historical-backtest/internal/catalog"
	"github.com/tvbt/tradingview-historical-backtest/internal/jobs"
	"github.com/tvbt/tradingview-historical-backtest/internal/pythonclient"
	"github.com/tvbt/tradingview-historical-backtest/internal/storage"
)

var (
	ErrInvalidRequest   = errors.New("invalid replay request")
	ErrRevisionMismatch = errors.New("data revision mismatch")
	ErrNotReady         = errors.New("replay is not completed")
	ErrInvalidRange     = errors.New("invalid replay range")
)

type Catalog interface {
	Get(datasetID, revision string) (catalog.DatasetMeta, error)
}

type Python interface {
	Algorithms(context.Context, string, string) ([]pythonclient.AlgorithmDefinition, error)
	Submit(context.Context, string, string, string, any) (pythonclient.JobStatus, error)
	Job(context.Context, string, string, string) (pythonclient.JobStatus, error)
	Cancel(context.Context, string, string, string) error
	Health(context.Context) pythonclient.Health
}

type Request struct {
	DatasetID          string                    `json:"dataset_id"`
	DataRevision       string                    `json:"data_revision"`
	Strategy           pythonclient.AlgorithmRef `json:"strategy"`
	Parameters         map[string]any            `json:"parameters"`
	FromBarIndex       int64                     `json:"from_bar_index"`
	ToBarIndex         int64                     `json:"to_bar_index"`
	WarmupFromBarIndex int64                     `json:"warmup_from_bar_index"`
}

type Submission struct {
	ReplayID string
	Job      *jobs.Job
	CacheKey string
	CacheHit bool
}

type Service struct {
	guard           *storage.PathGuard
	catalog         Catalog
	python          Python
	jobs            *jobs.Manager
	contractVersion string
	pollInterval    time.Duration

	mu       sync.Mutex
	flights  map[string]string
	cacheKey map[string]string
}

func NewService(guard *storage.PathGuard, store Catalog, python Python, manager *jobs.Manager, contractVersion string, pollInterval time.Duration) *Service {
	return &Service{guard: guard, catalog: store, python: python, jobs: manager, contractVersion: contractVersion, pollInterval: pollInterval, flights: map[string]string{}, cacheKey: map[string]string{}}
}

func (s *Service) Submit(ctx context.Context, requestID, traceID string, request Request) (Submission, error) {
	meta, err := s.catalog.Get(request.DatasetID, "")
	if err != nil {
		return Submission{}, err
	}
	if meta.DataRevision != request.DataRevision {
		return Submission{}, ErrRevisionMismatch
	}
	if request.WarmupFromBarIndex < 0 || request.FromBarIndex < request.WarmupFromBarIndex || request.ToBarIndex < request.FromBarIndex || request.ToBarIndex > meta.Coverage.LastBarIndex {
		return Submission{}, ErrInvalidRange
	}
	definitions, err := s.python.Algorithms(ctx, requestID, traceID)
	if err != nil {
		return Submission{}, err
	}
	definition, found := findDefinition(definitions, request.Strategy)
	if !found || request.Strategy.Kind != "chan" && request.Strategy.Kind != "strategy" {
		return Submission{}, ErrInvalidRequest
	}
	parameters, err := calculation.NormalizeParameters(definition.ParameterSchema, request.Parameters)
	if err != nil {
		return Submission{}, ErrInvalidRequest
	}
	request.Parameters = parameters
	key, err := CacheKey(meta.DataRevision, request.Strategy, parameters, request.FromBarIndex, request.ToBarIndex, request.WarmupFromBarIndex, s.python.Health(ctx).Version())
	if err != nil {
		return Submission{}, err
	}
	ref := "cache/replay/" + strings.TrimPrefix(key, "sha256:")
	if s.validCache(ref, key) {
		id := jobs.NewID()
		job := s.jobs.RecordCompleted(id, "replay", ref)
		job, _ = s.jobs.SetMetadata(id, map[string]string{"cache_key": key})
		s.mu.Lock()
		s.cacheKey[id] = key
		s.mu.Unlock()
		return Submission{ReplayID: id, Job: job, CacheKey: key, CacheHit: true}, nil
	}
	s.mu.Lock()
	if id := s.flights[key]; id != "" {
		if job, ok := s.jobs.Get(id); ok {
			s.mu.Unlock()
			return Submission{ReplayID: id, Job: job, CacheKey: key}, nil
		}
		delete(s.flights, key)
	}
	id := jobs.NewID()
	s.flights[key], s.cacheKey[id] = id, key
	job := s.start(id, requestID, traceID, request, meta, key, ref)
	job, _ = s.jobs.SetMetadata(id, map[string]string{"cache_key": key})
	s.mu.Unlock()
	return Submission{ReplayID: id, Job: job, CacheKey: key}, nil
}

func (s *Service) start(id, requestID, traceID string, request Request, meta catalog.DatasetMeta, key, ref string) *jobs.Job {
	return s.jobs.SubmitID(id, "replay", func(ctx context.Context, progress func(float64)) (string, error) {
		defer func() { s.mu.Lock(); delete(s.flights, key); s.mu.Unlock() }()
		barsPath, metaPath := datasetPaths(meta)
		payload := map[string]any{
			"contract_version": s.contractVersion, "request_id": requestID, "trace_id": traceID, "job_id": id,
			"dataset":   map[string]any{"dataset_id": meta.DatasetID, "data_revision": meta.DataRevision, "bars_path": barsPath, "meta_path": metaPath},
			"algorithm": request.Strategy, "parameters": request.Parameters, "cache_key": key, "output_path": ref,
			"range": map[string]int64{"from_bar_index": request.FromBarIndex, "to_bar_index": request.ToBarIndex, "warmup_from_bar_index": request.WarmupFromBarIndex},
		}
		if _, err := s.python.Submit(ctx, "replay", requestID, traceID, payload); err != nil {
			return "", jobs.Fail("PYTHON_SUBMIT_FAILED", "Python replay could not be submitted", err)
		}
		progress(.1)
		ticker := time.NewTicker(s.pollInterval)
		defer ticker.Stop()
		for {
			select {
			case <-ctx.Done():
				cancelCtx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
				_ = s.python.Cancel(cancelCtx, id, requestID, traceID)
				cancel()
				return "", ctx.Err()
			case <-ticker.C:
				status, err := s.python.Job(ctx, id, requestID, traceID)
				if err != nil {
					return "", jobs.Fail("PYTHON_POLL_FAILED", "Python replay status could not be read", err)
				}
				progress(.1 + status.Progress*.9)
				switch status.Status {
				case "completed":
					if !s.validCache(ref, key) {
						return "", jobs.Fail("CACHE_COMMIT_INVALID", "Replay cache was not committed", nil)
					}
					return ref, nil
				case "failed":
					return "", jobs.Fail("PYTHON_REPLAY_FAILED", "Python replay failed", nil)
				case "cancelled", "interrupted":
					return "", context.Canceled
				}
			}
		}
	})
}

func (s *Service) Status(id string) (*jobs.Job, string, bool) {
	job, ok := s.jobs.Get(id)
	s.mu.Lock()
	key := s.cacheKey[id]
	s.mu.Unlock()
	if ok && key == "" {
		key = job.Metadata["cache_key"]
	}
	return job, key, ok && key != ""
}

func (s *Service) Events(id string, from, to int64) (EventResponse, error) {
	if from < 0 || to < from {
		return EventResponse{}, ErrInvalidRange
	}
	job, key, ok := s.Status(id)
	if !ok {
		return EventResponse{}, catalog.ErrNotFound
	}
	if job.Status != jobs.Completed || job.ResultRef == "" {
		return EventResponse{}, ErrNotReady
	}
	return readEvents(s.guard, id, key, job.ResultRef, from, to)
}

func (s *Service) validCache(ref, key string) bool {
	directory, err := s.guard.Resolve(ref)
	if err != nil {
		return false
	}
	if _, err := os.Stat(filepath.Join(directory, "_SUCCESS")); err != nil {
		return false
	}
	data, err := os.ReadFile(filepath.Join(directory, "manifest.json"))
	if err != nil {
		return false
	}
	var manifest struct {
		CacheKey string `json:"cache_key"`
	}
	if json.Unmarshal(data, &manifest) != nil || manifest.CacheKey != key {
		return false
	}
	info, err := os.Stat(filepath.Join(directory, "events.parquet"))
	return err == nil && info.Mode().IsRegular()
}

func findDefinition(values []pythonclient.AlgorithmDefinition, ref pythonclient.AlgorithmRef) (pythonclient.AlgorithmDefinition, bool) {
	for _, value := range values {
		if value.AlgorithmRef == ref {
			return value, true
		}
	}
	return pythonclient.AlgorithmDefinition{}, false
}

func datasetPaths(meta catalog.DatasetMeta) (string, string) {
	for _, file := range meta.Files {
		if file.Role == "bars" {
			return file.Path, strings.TrimSuffix(file.Path, "bars.parquet") + "meta.json"
		}
	}
	return "", ""
}

func CacheKey(revision string, strategy pythonclient.AlgorithmRef, parameters map[string]any, from, to, warmup int64, engineVersion string) (string, error) {
	data, err := json.Marshal(map[string]any{"data_revision": revision, "strategy": strategy, "parameters": parameters, "from_bar_index": from, "to_bar_index": to, "warmup_from_bar_index": warmup, "engine_version": engineVersion})
	if err != nil {
		return "", err
	}
	digest := sha256.Sum256(data)
	return "sha256:" + hex.EncodeToString(digest[:]), nil
}
