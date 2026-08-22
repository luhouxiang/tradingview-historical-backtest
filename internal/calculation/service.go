package calculation

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/tvbt/tradingview-historical-backtest/internal/catalog"
	"github.com/tvbt/tradingview-historical-backtest/internal/jobs"
	"github.com/tvbt/tradingview-historical-backtest/internal/pythonclient"
	"github.com/tvbt/tradingview-historical-backtest/internal/storage"
)

var (
	ErrInvalidRequest   = errors.New("invalid calculation request")
	ErrRevisionMismatch = errors.New("data revision mismatch")
	ErrNotReady         = errors.New("calculation is not completed")
	ErrInvalidRange     = errors.New("invalid result range")
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
	DatasetID       string                    `json:"dataset_id"`
	DataRevision    string                    `json:"data_revision"`
	Algorithm       pythonclient.AlgorithmRef `json:"algorithm"`
	Parameters      map[string]any            `json:"parameters"`
	CalculationMode string                    `json:"calculation_mode"`
	TraceID         string                    `json:"trace_id,omitempty"`
}

type submission struct {
	Job      *jobs.Job
	CacheHit bool
}

type Service struct {
	guard           *storage.PathGuard
	catalog         Catalog
	python          Python
	jobs            *jobs.Manager
	contractVersion string
	pollInterval    time.Duration

	mu      sync.Mutex
	flights map[string]string
	byJob   map[string]string
}

func NewService(guard *storage.PathGuard, catalogStore Catalog, python Python, manager *jobs.Manager, contractVersion string, pollInterval time.Duration) *Service {
	return &Service{
		guard: guard, catalog: catalogStore, python: python, jobs: manager,
		contractVersion: contractVersion, pollInterval: pollInterval,
		flights: make(map[string]string), byJob: make(map[string]string),
	}
}

func (s *Service) Algorithms(ctx context.Context, requestID, traceID string) ([]pythonclient.AlgorithmDefinition, error) {
	return s.python.Algorithms(ctx, requestID, traceID)
}

func (s *Service) Submit(ctx context.Context, requestID, traceID string, request Request) (submission, error) {
	meta, err := s.catalog.Get(request.DatasetID, "")
	if err != nil {
		return submission{}, err
	}
	if meta.DataRevision != request.DataRevision {
		return submission{}, ErrRevisionMismatch
	}
	definitions, err := s.python.Algorithms(ctx, requestID, traceID)
	if err != nil {
		return submission{}, fmt.Errorf("list algorithms: %w", err)
	}
	definition, ok := findDefinition(definitions, request.Algorithm)
	validMode := request.Algorithm.Kind == "indicator" && request.CalculationMode == "full_history" || request.Algorithm.Kind == "chan" && request.CalculationMode == "causal_events"
	if !ok || !validMode {
		return submission{}, ErrInvalidRequest
	}
	parameters, err := NormalizeParameters(definition.ParameterSchema, request.Parameters)
	if err != nil {
		return submission{}, err
	}
	request.Parameters = parameters
	engineVersion := s.python.Health(ctx).Version()
	cacheKey, err := CacheKey(meta.DataRevision, request.Algorithm, parameters, request.CalculationMode, engineVersion)
	if err != nil {
		return submission{}, err
	}
	cacheDirectory := "indicators"
	if request.Algorithm.Kind == "chan" {
		cacheDirectory = "chan"
	}
	resultRef := "cache/" + cacheDirectory + "/" + strings.TrimPrefix(cacheKey, "sha256:")
	if s.validCache(resultRef, cacheKey) {
		jobID := jobs.NewID()
		job := s.jobs.RecordCompleted(jobID, "calculation", resultRef)
		job, _ = s.jobs.SetMetadata(jobID, map[string]string{"cache_key": cacheKey})
		s.mu.Lock()
		s.byJob[jobID] = cacheKey
		s.mu.Unlock()
		return submission{Job: job, CacheHit: true}, nil
	}
	s.mu.Lock()
	if jobID := s.flights[cacheKey]; jobID != "" {
		job, exists := s.jobs.Get(jobID)
		if exists {
			s.mu.Unlock()
			return submission{Job: job}, nil
		}
		delete(s.flights, cacheKey)
	}
	jobID := jobs.NewID()
	s.flights[cacheKey] = jobID
	s.byJob[jobID] = cacheKey
	result := s.start(jobID, requestID, traceID, request, meta, cacheKey, resultRef)
	s.mu.Unlock()
	return result, nil
}

func (s *Service) start(jobID, requestID, traceID string, request Request, meta catalog.DatasetMeta, cacheKey, resultRef string) submission {
	work := func(ctx context.Context, progress func(float64)) (string, error) {
		defer func() {
			s.mu.Lock()
			delete(s.flights, cacheKey)
			s.mu.Unlock()
		}()
		barsPath, metaPath := datasetPaths(meta)
		payload := map[string]any{
			"contract_version": s.contractVersion, "request_id": requestID, "trace_id": traceID,
			"job_id": jobID, "dataset": map[string]any{"dataset_id": meta.DatasetID, "data_revision": meta.DataRevision, "bars_path": barsPath, "meta_path": metaPath},
			"algorithm": request.Algorithm, "parameters": request.Parameters,
			"calculation_mode": request.CalculationMode, "cache_key": cacheKey, "output_path": resultRef,
		}
		if _, err := s.python.Submit(ctx, "calculation", requestID, traceID, payload); err != nil {
			return "", jobs.Fail("PYTHON_SUBMIT_FAILED", "Python calculation could not be submitted", err)
		}
		progress(0.1)
		ticker := time.NewTicker(s.pollInterval)
		defer ticker.Stop()
		for {
			select {
			case <-ctx.Done():
				cancelCtx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
				_ = s.python.Cancel(cancelCtx, jobID, requestID, traceID)
				cancel()
				return "", ctx.Err()
			case <-ticker.C:
				status, err := s.python.Job(ctx, jobID, requestID, traceID)
				if err != nil {
					return "", jobs.Fail("PYTHON_POLL_FAILED", "Python calculation status could not be read", err)
				}
				progress(0.1 + status.Progress*0.9)
				switch status.Status {
				case "completed":
					if !s.validCache(resultRef, cacheKey) {
						return "", jobs.Fail("CACHE_COMMIT_INVALID", "Calculation cache was not committed", nil)
					}
					return resultRef, nil
				case "failed":
					return "", jobs.Fail("PYTHON_CALCULATION_FAILED", "Python calculation failed", nil)
				case "cancelled", "interrupted":
					return "", context.Canceled
				}
			}
		}
	}
	job := s.jobs.SubmitID(jobID, "calculation", work)
	job, _ = s.jobs.SetMetadata(jobID, map[string]string{"cache_key": cacheKey})
	return submission{Job: job}
}

func (s *Service) Job(id string) (*jobs.Job, bool)    { return s.jobs.Get(id) }
func (s *Service) Cancel(id string) (*jobs.Job, bool) { return s.jobs.Cancel(id) }

func (s *Service) Results(jobID string, from, to int64) (Results, error) {
	if from < 0 || to < from || to-from+1 > 5000 {
		return Results{}, ErrInvalidRange
	}
	job, ok := s.jobs.Get(jobID)
	if !ok {
		return Results{}, catalog.ErrNotFound
	}
	if job.Status != jobs.Completed || job.ResultRef == "" {
		return Results{}, ErrNotReady
	}
	s.mu.Lock()
	cacheKey := s.byJob[jobID]
	s.mu.Unlock()
	if cacheKey == "" {
		cacheKey = job.Metadata["cache_key"]
	}
	if cacheKey == "" {
		cacheKey = "sha256:" + filepath.Base(job.ResultRef)
	}
	return readResults(s.guard, jobID, cacheKey, job.ResultRef, from, to)
}

func (s *Service) validCache(resultRef, cacheKey string) bool {
	path, err := s.guard.Resolve(resultRef)
	if err != nil {
		return false
	}
	if _, err := os.Stat(filepath.Join(path, "_SUCCESS")); err != nil {
		return false
	}
	data, err := os.ReadFile(filepath.Join(path, "manifest.json"))
	if err != nil {
		return false
	}
	var manifest struct {
		CacheKey  string                    `json:"cache_key"`
		Algorithm pythonclient.AlgorithmRef `json:"algorithm"`
	}
	if json.Unmarshal(data, &manifest) != nil || manifest.CacheKey != cacheKey {
		return false
	}
	files := []string{"values.parquet"}
	if manifest.Algorithm.Kind == "chan" {
		files = []string{"processed_bars.parquet", "fractals.parquet", "bi.parquet", "bi_states.parquet", "segments.parquet", "zhongshu.parquet", "segment_zhongshu.parquet", "level_centers.parquet", "level_movements.parquet", "movement_states.parquet", "center_monitors.parquet", "divergences.parquet", "trade_points.parquet", "events.parquet"}
	} else if manifest.Algorithm.Kind != "indicator" {
		return false
	}
	for _, name := range files {
		if info, err := os.Stat(filepath.Join(path, name)); err != nil || !info.Mode().IsRegular() {
			return false
		}
	}
	return true
}

func findDefinition(definitions []pythonclient.AlgorithmDefinition, ref pythonclient.AlgorithmRef) (pythonclient.AlgorithmDefinition, bool) {
	for _, definition := range definitions {
		if definition.Kind == ref.Kind && definition.AlgorithmID == ref.AlgorithmID && definition.AlgorithmVersion == ref.AlgorithmVersion && definition.SourceHash == ref.SourceHash {
			return definition, true
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

func CacheKey(dataRevision string, algorithm pythonclient.AlgorithmRef, parameters map[string]any, mode, engineVersion string) (string, error) {
	payload := map[string]any{
		"data_revision": dataRevision, "algorithm_kind": algorithm.Kind,
		"algorithm_id": algorithm.AlgorithmID, "algorithm_version": algorithm.AlgorithmVersion,
		"source_hash": algorithm.SourceHash, "parameters": parameters,
		"calculation_mode": mode, "engine_version": engineVersion,
	}
	data, err := json.Marshal(payload)
	if err != nil {
		return "", err
	}
	digest := sha256.Sum256(data)
	return "sha256:" + hex.EncodeToString(digest[:]), nil
}
