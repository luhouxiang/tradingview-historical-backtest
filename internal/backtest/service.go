package backtest

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
	ErrInvalidRequest   = errors.New("invalid backtest request")
	ErrInvalidRange     = errors.New("invalid backtest range")
	ErrRevisionMismatch = errors.New("data revision mismatch")
	ErrNotReady         = errors.New("backtest is not completed")
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

type Range struct {
	WarmupFromBarIndex int64 `json:"warmup_from_bar_index"`
	FromBarIndex       int64 `json:"from_bar_index"`
	ToBarIndex         int64 `json:"to_bar_index"`
}

type Request struct {
	DatasetID    string                    `json:"dataset_id"`
	DataRevision string                    `json:"data_revision"`
	Strategy     pythonclient.AlgorithmRef `json:"strategy"`
	Parameters   map[string]any            `json:"parameters"`
	Range        Range                     `json:"range"`
	Execution    map[string]any            `json:"execution"`
	Capital      map[string]any            `json:"capital"`
	RandomSeed   int64                     `json:"random_seed"`
	TraceID      string                    `json:"trace_id,omitempty"`
}

type Submission struct {
	RunID        string
	RunSignature string
	Job          *jobs.Job
}

type Service struct {
	guard           *storage.PathGuard
	catalog         Catalog
	python          Python
	jobs            *jobs.Manager
	contractVersion string
	pollInterval    time.Duration
	mu              sync.Mutex
	signatures      map[string]string
	idempotency     map[string]string
}

func NewService(guard *storage.PathGuard, catalog Catalog, python Python, manager *jobs.Manager, contractVersion string, pollInterval time.Duration) *Service {
	return &Service{guard: guard, catalog: catalog, python: python, jobs: manager, contractVersion: contractVersion, pollInterval: pollInterval, signatures: map[string]string{}, idempotency: map[string]string{}}
}

func (s *Service) Submit(ctx context.Context, requestID, traceID, idempotencyKey string, request Request) (Submission, error) {
	meta, err := s.catalog.Get(request.DatasetID, "")
	if err != nil {
		return Submission{}, err
	}
	if meta.DataRevision != request.DataRevision {
		return Submission{}, ErrRevisionMismatch
	}
	if request.Range.WarmupFromBarIndex < 0 || request.Range.FromBarIndex < request.Range.WarmupFromBarIndex || request.Range.ToBarIndex < request.Range.FromBarIndex || request.Range.ToBarIndex > meta.Coverage.LastBarIndex {
		return Submission{}, ErrInvalidRange
	}
	definitions, err := s.python.Algorithms(ctx, requestID, traceID)
	if err != nil {
		return Submission{}, err
	}
	definition, ok := findDefinition(definitions, request.Strategy)
	if !ok || request.Strategy.Kind != "strategy" {
		return Submission{}, ErrInvalidRequest
	}
	parameters, err := calculation.NormalizeParameters(definition.ParameterSchema, request.Parameters)
	if err != nil || !validExecution(request.Execution) || !validCapital(request.Capital) {
		return Submission{}, ErrInvalidRequest
	}
	request.Parameters = parameters
	signature, err := Signature(request, s.python.Health(ctx).Version())
	if err != nil {
		return Submission{}, err
	}
	s.mu.Lock()
	if idempotencyKey != "" {
		if runID := s.idempotency[idempotencyKey]; runID != "" {
			job, exists := s.jobs.Get(runID)
			if exists {
				existing := Submission{RunID: runID, RunSignature: s.signatures[runID], Job: job}
				s.mu.Unlock()
				return existing, nil
			}
		}
	}
	runID := jobs.NewID()
	s.signatures[runID] = signature
	if idempotencyKey != "" {
		s.idempotency[idempotencyKey] = runID
	}
	job := s.start(runID, signature, requestID, traceID, request, meta)
	job, _ = s.jobs.SetMetadata(runID, map[string]string{"run_signature": signature})
	s.mu.Unlock()
	return Submission{RunID: runID, RunSignature: signature, Job: job}, nil
}

func (s *Service) start(runID, signature, requestID, traceID string, request Request, meta catalog.DatasetMeta) *jobs.Job {
	ref := "runs/" + runID
	return s.jobs.SubmitID(runID, "backtest", func(ctx context.Context, progress func(float64)) (string, error) {
		barsPath, metaPath := datasetPaths(meta)
		payload := map[string]any{
			"contract_version": s.contractVersion, "request_id": requestID, "trace_id": traceID,
			"job_id": runID, "run_id": runID, "run_signature": signature,
			"dataset":   map[string]any{"dataset_id": meta.DatasetID, "data_revision": meta.DataRevision, "bars_path": barsPath, "meta_path": metaPath},
			"algorithm": request.Strategy, "parameters": request.Parameters, "range": request.Range,
			"execution": request.Execution, "capital": request.Capital, "random_seed": request.RandomSeed, "output_path": ref,
		}
		if _, err := s.python.Submit(ctx, "backtest", requestID, traceID, payload); err != nil {
			return "", jobs.Fail("PYTHON_SUBMIT_FAILED", "Python backtest could not be submitted", err)
		}
		progress(.1)
		ticker := time.NewTicker(s.pollInterval)
		defer ticker.Stop()
		for {
			select {
			case <-ctx.Done():
				cancelCtx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
				_ = s.python.Cancel(cancelCtx, runID, requestID, traceID)
				cancel()
				return "", ctx.Err()
			case <-ticker.C:
				status, err := s.python.Job(ctx, runID, requestID, traceID)
				if err != nil {
					return "", jobs.Fail("PYTHON_POLL_FAILED", "Python backtest status could not be read", err)
				}
				progress(.1 + status.Progress*.9)
				switch status.Status {
				case "completed":
					if !s.validRun(ref, runID, signature) {
						return "", jobs.Fail("RUN_COMMIT_INVALID", "Backtest run was not committed", nil)
					}
					return ref, nil
				case "failed":
					return "", jobs.Fail("PYTHON_BACKTEST_FAILED", "Python backtest failed", nil)
				case "cancelled", "interrupted":
					return "", context.Canceled
				}
			}
		}
	})
}

func (s *Service) Status(runID string) (*jobs.Job, string, map[string]any, bool) {
	job, ok := s.jobs.Get(runID)
	s.mu.Lock()
	signature := s.signatures[runID]
	s.mu.Unlock()
	if ok && signature == "" {
		signature = job.Metadata["run_signature"]
	}
	if ok && signature == "" && job.Status == jobs.Completed {
		if stored, err := s.readJSON(job.ResultRef, "run.json"); err == nil {
			signature, _ = stored["run_signature"].(string)
		}
	}
	if !ok || signature == "" {
		return nil, "", nil, false
	}
	var manifest map[string]any
	if job.Status == jobs.Completed {
		manifest, _ = s.readJSON(job.ResultRef, "run.json")
	}
	return job, signature, manifest, true
}

func (s *Service) Cancel(runID string) (*jobs.Job, string, bool) {
	job, ok := s.jobs.Cancel(runID)
	s.mu.Lock()
	signature := s.signatures[runID]
	s.mu.Unlock()
	if ok && signature == "" {
		signature = job.Metadata["run_signature"]
	}
	return job, signature, ok && signature != ""
}

func (s *Service) Summary(runID string) (map[string]any, error) {
	job, _, _, ok := s.Status(runID)
	if !ok {
		return nil, catalog.ErrNotFound
	}
	if job.Status != jobs.Completed {
		return nil, ErrNotReady
	}
	return s.readJSON(job.ResultRef, "summary.json")
}

func (s *Service) resultRef(runID string) (string, error) {
	job, _, _, ok := s.Status(runID)
	if !ok {
		return "", catalog.ErrNotFound
	}
	if job.Status != jobs.Completed || job.ResultRef == "" {
		return "", ErrNotReady
	}
	return job.ResultRef, nil
}

func (s *Service) readJSON(ref, name string) (map[string]any, error) {
	directory, err := s.guard.Resolve(ref)
	if err != nil {
		return nil, err
	}
	data, err := os.ReadFile(filepath.Join(directory, name))
	if err != nil {
		return nil, err
	}
	value := map[string]any{}
	err = json.Unmarshal(data, &value)
	return value, err
}

func (s *Service) validRun(ref, runID, signature string) bool {
	directory, err := s.guard.Resolve(ref)
	if err != nil {
		return false
	}
	for _, name := range []string{"run.json", "status.json", "summary.json", "indicator_values.parquet", "strategy_states.parquet", "stage_signals.parquet", "trade_signals.parquet", "chart_events.parquet", "orders.parquet", "fills.parquet", "trades.parquet", "positions.parquet", "equity.parquet", "log.ndjson", "_SUCCESS"} {
		if info, err := os.Stat(filepath.Join(directory, name)); err != nil || !info.Mode().IsRegular() {
			return false
		}
	}
	manifest, err := s.readJSON(ref, "run.json")
	return err == nil && manifest["run_id"] == runID && manifest["run_signature"] == signature
}

func Signature(request Request, engineVersion string) (string, error) {
	data, err := json.Marshal(map[string]any{"data_revision": request.DataRevision, "strategy": request.Strategy, "parameters": request.Parameters, "range": request.Range, "execution": request.Execution, "capital": request.Capital, "random_seed": request.RandomSeed, "engine_version": engineVersion})
	if err != nil {
		return "", err
	}
	digest := sha256.Sum256(data)
	return "sha256:" + hex.EncodeToString(digest[:]), nil
}

func validExecution(value map[string]any) bool {
	return value["signal_timing"] == "bar_close" && (value["fill_timing"] == "next_bar_open" || value["fill_timing"] == "bar_close") && value["commission"] != nil && value["slippage"] != nil
}

func validCapital(value map[string]any) bool {
	return value["initial_cash_i64"] != nil && value["money_scale"] != nil && value["currency"] != nil
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
