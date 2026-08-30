package comparison

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/tvbt/tradingview-historical-backtest/internal/backtest"
	"github.com/tvbt/tradingview-historical-backtest/internal/calculation"
	"github.com/tvbt/tradingview-historical-backtest/internal/catalog"
	"github.com/tvbt/tradingview-historical-backtest/internal/jobs"
	"github.com/tvbt/tradingview-historical-backtest/internal/pythonclient"
	"github.com/tvbt/tradingview-historical-backtest/internal/storage"
)

const aggregatorVersion = "2.0.0"

var (
	ErrInvalidRequest   = errors.New("invalid strategy comparison request")
	ErrInvalidRange     = errors.New("invalid strategy comparison range")
	ErrRevisionMismatch = errors.New("data revision mismatch")
	ErrNotReady         = errors.New("strategy comparison is not completed")
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

type Item struct {
	Strategy   pythonclient.AlgorithmRef `json:"strategy"`
	Parameters map[string]any            `json:"parameters"`
}

type Request struct {
	DatasetID         string                `json:"dataset_id"`
	DataRevision      string                `json:"data_revision"`
	Strategies        []Item                `json:"strategies"`
	RiskOverlay       *backtest.RiskOverlay `json:"risk_overlay,omitempty"`
	Range             backtest.Range        `json:"range"`
	Execution         map[string]any        `json:"execution"`
	Capital           map[string]any        `json:"capital"`
	RandomSeed        int64                 `json:"random_seed"`
	MinimumTradeCount int                   `json:"minimum_trade_count"`
	TraceID           string                `json:"trace_id,omitempty"`
}

type preparedItem struct {
	Strategy       pythonclient.AlgorithmRef `json:"strategy"`
	Parameters     map[string]any            `json:"parameters"`
	Name           string                    `json:"name"`
	StrategyFamily string                    `json:"strategy_family"`
	RunID          string                    `json:"run_id"`
	RunSignature   string                    `json:"run_signature"`
}

type Result struct {
	AlgorithmID    string         `json:"algorithm_id"`
	Name           string         `json:"name"`
	StrategyFamily string         `json:"strategy_family"`
	Parameters     map[string]any `json:"parameters"`
	Status         string         `json:"status"`
	RunID          string         `json:"run_id,omitempty"`
	RunSignature   string         `json:"run_signature,omitempty"`
	Summary        map[string]any `json:"summary,omitempty"`
	Error          map[string]any `json:"error,omitempty"`
}

type ProgressDetail struct {
	TotalCount         int     `json:"total_count"`
	CompletedCount     int     `json:"completed_count"`
	FailedCount        int     `json:"failed_count"`
	CurrentAlgorithmID *string `json:"current_algorithm_id"`
}

type Submission struct {
	ComparisonID string
	Job          *jobs.Job
}

type Service struct {
	guard           *storage.PathGuard
	catalog         Catalog
	python          Python
	jobs            *jobs.Manager
	contractVersion string
	pollInterval    time.Duration
	mu              sync.RWMutex
	details         map[string]ProgressDetail
}

func NewService(guard *storage.PathGuard, catalogStore Catalog, python Python, manager *jobs.Manager, contractVersion string, pollInterval time.Duration) *Service {
	return &Service{guard: guard, catalog: catalogStore, python: python, jobs: manager, contractVersion: contractVersion, pollInterval: pollInterval, details: map[string]ProgressDetail{}}
}

func (s *Service) Submit(ctx context.Context, requestID, traceID string, request Request) (Submission, error) {
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
	barsPath, metaPath := datasetPaths(meta)
	if barsPath == "" || metaPath == "" {
		return Submission{}, ErrInvalidRequest
	}
	if _, err := s.guard.Resolve(barsPath); err != nil {
		return Submission{}, ErrInvalidRequest
	}
	if _, err := s.guard.Resolve(metaPath); err != nil {
		return Submission{}, ErrInvalidRequest
	}
	if len(request.Strategies) == 0 || len(request.Strategies) > 32 || request.MinimumTradeCount < 1 {
		return Submission{}, ErrInvalidRequest
	}
	capital, err := backtest.NormalizeCapital(request.Capital)
	if err != nil {
		return Submission{}, ErrInvalidRequest
	}
	execution, err := backtest.NormalizeExecution(request.Execution, capital, meta.Instrument.ContractMultiplier)
	if err != nil {
		return Submission{}, ErrInvalidRequest
	}
	request.Capital = capital
	request.Execution = execution
	definitions, err := s.python.Algorithms(ctx, requestID, traceID)
	if err != nil {
		return Submission{}, err
	}
	definitionByRef := make(map[pythonclient.AlgorithmRef]pythonclient.AlgorithmDefinition, len(definitions))
	for _, definition := range definitions {
		definitionByRef[definition.AlgorithmRef] = definition
	}
	riskOverlay, err := backtest.NormalizeRiskOverlay(definitions, request.RiskOverlay, request.DataRevision, meta.Coverage.FirstBarIndex, meta.Coverage.LastBarIndex)
	if err != nil {
		return Submission{}, ErrInvalidRequest
	}
	request.RiskOverlay = riskOverlay
	engineVersion := s.python.Health(ctx).Version()
	seen := map[string]bool{}
	prepared := make([]preparedItem, 0, len(request.Strategies))
	for _, item := range request.Strategies {
		definition, ok := definitionByRef[item.Strategy]
		if !ok || item.Strategy.Kind != "strategy" || !definition.ComparisonEligible || definition.ResearchRole != "formal_strategy" || seen[item.Strategy.AlgorithmID] {
			return Submission{}, ErrInvalidRequest
		}
		seen[item.Strategy.AlgorithmID] = true
		parameters, err := calculation.NormalizeParameters(definition.ParameterSchema, item.Parameters)
		if err != nil {
			return Submission{}, ErrInvalidRequest
		}
		runRequest := backtest.Request{DatasetID: request.DatasetID, DataRevision: request.DataRevision, Strategy: item.Strategy, Parameters: parameters, RiskOverlay: request.RiskOverlay, Range: request.Range, Execution: request.Execution, Capital: request.Capital, RandomSeed: request.RandomSeed}
		signature, err := backtest.Signature(runRequest, engineVersion)
		if err != nil {
			return Submission{}, err
		}
		prepared = append(prepared, preparedItem{Strategy: item.Strategy, Parameters: parameters, Name: definition.Name, StrategyFamily: definition.StrategyFamily, RunID: jobs.NewID(), RunSignature: signature})
	}
	comparisonSignature, err := Signature(request, prepared, engineVersion)
	if err != nil {
		return Submission{}, err
	}
	comparisonID := "comparison-" + strings.TrimPrefix(jobs.NewID(), "job-")
	s.mu.Lock()
	s.details[comparisonID] = ProgressDetail{TotalCount: len(prepared)}
	s.mu.Unlock()
	job := s.start(comparisonID, comparisonSignature, requestID, traceID, request, prepared, meta)
	return Submission{ComparisonID: comparisonID, Job: job}, nil
}

func (s *Service) start(comparisonID, comparisonSignature, requestID, traceID string, request Request, prepared []preparedItem, meta catalog.DatasetMeta) *jobs.Job {
	ref := "comparisons/" + comparisonID
	return s.jobs.SubmitID(comparisonID, "comparison", func(ctx context.Context, progress func(float64)) (string, error) {
		barsPath, metaPath := datasetPaths(meta)
		payload := map[string]any{
			"contract_version": s.contractVersion, "request_id": requestID, "trace_id": traceID,
			"job_id": comparisonID, "comparison_id": comparisonID,
			"comparison_signature": comparisonSignature,
			"dataset":              map[string]any{"dataset_id": meta.DatasetID, "data_revision": meta.DataRevision, "bars_path": barsPath, "meta_path": metaPath},
			"strategies":           prepared, "range": request.Range, "execution": request.Execution,
			"capital": request.Capital, "random_seed": request.RandomSeed,
			"minimum_trade_count": request.MinimumTradeCount, "output_path": ref,
		}
		if request.RiskOverlay != nil {
			payload["risk_overlay"] = request.RiskOverlay
		}
		if _, err := s.python.Submit(ctx, "comparison", requestID, traceID, payload); err != nil {
			return "", jobs.Fail("PYTHON_SUBMIT_FAILED", "Python strategy comparison could not be submitted", err)
		}
		ticker := time.NewTicker(s.pollInterval)
		defer ticker.Stop()
		for {
			select {
			case <-ctx.Done():
				cancelCtx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
				_ = s.python.Cancel(cancelCtx, comparisonID, requestID, traceID)
				cancel()
				return "", ctx.Err()
			case <-ticker.C:
				status, err := s.python.Job(ctx, comparisonID, requestID, traceID)
				if err != nil {
					return "", jobs.Fail("PYTHON_POLL_FAILED", "Python strategy comparison status could not be read", err)
				}
				progress(status.Progress)
				s.captureProgress(comparisonID, status.ProgressDetail)
				switch status.Status {
				case "completed":
					if !s.validComparison(ref, comparisonID) {
						return "", jobs.Fail("COMPARISON_COMMIT_INVALID", "Strategy comparison was not committed", nil)
					}
					if err := s.registerRuns(ref); err != nil {
						return "", jobs.Fail("COMPARISON_RUN_REGISTRATION_FAILED", "Completed strategy runs could not be registered", err)
					}
					return ref, nil
				case "failed":
					return "", jobs.Fail("PYTHON_COMPARISON_FAILED", "Python strategy comparison failed", nil)
				case "cancelled", "interrupted":
					return "", context.Canceled
				}
			}
		}
	})
}

// Signature covers every fact that can change a child result or its comparison tier.
func Signature(request Request, prepared []preparedItem, engineVersion string) (string, error) {
	strategies := make([]map[string]any, 0, len(prepared))
	for _, item := range prepared {
		strategies = append(strategies, map[string]any{"strategy": item.Strategy, "parameters": item.Parameters})
	}
	facts := map[string]any{
		"dataset_id": request.DatasetID, "data_revision": request.DataRevision,
		"range": request.Range, "strategies": strategies, "execution": request.Execution,
		"capital": request.Capital, "risk_overlay": request.RiskOverlay,
		"random_seed": request.RandomSeed, "minimum_trade_count": request.MinimumTradeCount,
		"engine_version": engineVersion, "aggregator_version": aggregatorVersion,
	}
	data, err := json.Marshal(facts)
	if err != nil {
		return "", err
	}
	digest := sha256.Sum256(data)
	return "sha256:" + fmt.Sprintf("%x", digest), nil
}

func (s *Service) captureProgress(comparisonID string, value map[string]any) {
	detail := ProgressDetail{}
	detail.TotalCount = int(number(value["total_count"]))
	detail.CompletedCount = int(number(value["completed_count"]))
	detail.FailedCount = int(number(value["failed_count"]))
	if current, ok := value["current_algorithm_id"].(string); ok && current != "" {
		detail.CurrentAlgorithmID = &current
	}
	s.mu.Lock()
	if detail.TotalCount > 0 {
		s.details[comparisonID] = detail
	}
	s.mu.Unlock()
}

func (s *Service) Status(comparisonID string) (*jobs.Job, ProgressDetail, map[string]any, bool) {
	job, ok := s.jobs.Get(comparisonID)
	if !ok || job.Kind != "comparison" {
		return nil, ProgressDetail{}, nil, false
	}
	s.mu.RLock()
	detail := s.details[comparisonID]
	s.mu.RUnlock()
	var manifest map[string]any
	if job.Status == jobs.Completed {
		manifest, _ = s.readObject(job.ResultRef, "comparison.json")
		if manifest != nil {
			detail.TotalCount = int(number(manifest["strategy_count"]))
			detail.CompletedCount = int(number(manifest["completed_count"])) + int(number(manifest["failed_count"]))
			detail.FailedCount = int(number(manifest["failed_count"]))
		}
	} else {
		journalRef := "comparisons/" + comparisonID + ".journal.json"
		if path, err := s.guard.Resolve(journalRef); err == nil {
			if data, readErr := os.ReadFile(path); readErr == nil {
				_ = json.Unmarshal(data, &manifest)
			}
		}
	}
	return job, detail, manifest, true
}

func (s *Service) Cancel(comparisonID string) (*jobs.Job, bool) { return s.jobs.Cancel(comparisonID) }

func (s *Service) Results(comparisonID string) ([]Result, error) {
	job, _, _, ok := s.Status(comparisonID)
	if !ok {
		return nil, catalog.ErrNotFound
	}
	if job.Status != jobs.Completed || job.ResultRef == "" {
		return nil, ErrNotReady
	}
	data, err := os.ReadFile(mustResolve(s.guard, job.ResultRef, "results.json"))
	if err != nil {
		return nil, err
	}
	var results []Result
	if err := json.Unmarshal(data, &results); err != nil {
		return nil, err
	}
	return results, nil
}

func (s *Service) List(datasetID string) ([]map[string]any, error) {
	directory, err := s.guard.Resolve("comparisons")
	if err != nil {
		return nil, err
	}
	entries, err := os.ReadDir(directory)
	if errors.Is(err, os.ErrNotExist) {
		return []map[string]any{}, nil
	}
	if err != nil {
		return nil, err
	}
	items := make([]map[string]any, 0, len(entries))
	for _, entry := range entries {
		if !entry.IsDir() {
			continue
		}
		ref := "comparisons/" + entry.Name()
		if _, err := os.Stat(mustResolve(s.guard, ref, "_SUCCESS")); err != nil {
			continue
		}
		manifest, err := s.readObject(ref, "comparison.json")
		if err != nil {
			continue
		}
		dataset, _ := manifest["dataset"].(map[string]any)
		if datasetID == "" || dataset["dataset_id"] == datasetID {
			items = append(items, manifest)
		}
	}
	sort.Slice(items, func(i, j int) bool { return stringValue(items[i]["created_at"]) > stringValue(items[j]["created_at"]) })
	return items, nil
}

func (s *Service) registerRuns(ref string) error {
	data, err := os.ReadFile(mustResolve(s.guard, ref, "results.json"))
	if err != nil {
		return err
	}
	var results []Result
	if err := json.Unmarshal(data, &results); err != nil {
		return err
	}
	for _, result := range results {
		if result.Status != "completed" || result.RunID == "" {
			continue
		}
		runRef := "runs/" + result.RunID
		if _, err := os.Stat(mustResolve(s.guard, runRef, "_SUCCESS")); err != nil {
			return err
		}
		s.jobs.RecordCompleted(result.RunID, "backtest", runRef)
		_, _ = s.jobs.SetMetadata(result.RunID, map[string]string{"run_signature": result.RunSignature})
	}
	return nil
}

func (s *Service) validComparison(ref, comparisonID string) bool {
	for _, name := range []string{"comparison.json", "results.json", "_SUCCESS"} {
		if info, err := os.Stat(mustResolve(s.guard, ref, name)); err != nil || !info.Mode().IsRegular() {
			return false
		}
	}
	manifest, err := s.readObject(ref, "comparison.json")
	if err != nil || manifest["comparison_id"] != comparisonID {
		return false
	}
	dependency, ok := manifest["shared_dependency"].(map[string]any)
	if !ok {
		return true
	}
	dependencyRef, _ := dependency["dependency_ref"].(string)
	if dependencyRef == "" {
		return false
	}
	directory, err := s.guard.Resolve(dependencyRef)
	if err != nil {
		return false
	}
	for _, name := range []string{"manifest.json", "runtime.pkl", "_SUCCESS"} {
		if info, statErr := os.Stat(filepath.Join(directory, name)); statErr != nil || !info.Mode().IsRegular() {
			return false
		}
	}
	data, err := os.ReadFile(filepath.Join(directory, "runtime.pkl"))
	if err != nil {
		return false
	}
	digest := sha256.Sum256(data)
	if dependency["content_hash"] != "sha256:"+fmt.Sprintf("%x", digest) {
		return false
	}
	identity, ok := dependency["identity"].(map[string]any)
	dataset, _ := manifest["dataset"].(map[string]any)
	algorithm, algorithmOK := identity["algorithm"].(map[string]any)
	_, parametersOK := identity["parameters"].(map[string]any)
	return ok && identity["dataset_id"] == dataset["dataset_id"] && identity["data_revision"] == dataset["data_revision"] &&
		identity["dependency_kind"] == "chan_causal_runtime" && algorithmOK && stringValue(algorithm["algorithm_version"]) != "" &&
		stringValue(algorithm["source_hash"]) != "" && parametersOK
}

func (s *Service) readObject(ref, name string) (map[string]any, error) {
	data, err := os.ReadFile(mustResolve(s.guard, ref, name))
	if err != nil {
		return nil, err
	}
	var value map[string]any
	err = json.Unmarshal(data, &value)
	return value, err
}

func datasetPaths(meta catalog.DatasetMeta) (string, string) {
	for _, file := range meta.Files {
		if file.Role == "bars" {
			return file.Path, strings.TrimSuffix(file.Path, "bars.parquet") + "meta.json"
		}
	}
	return "", ""
}

func mustResolve(guard *storage.PathGuard, ref, name string) string {
	path, err := guard.Resolve(filepath.ToSlash(filepath.Join(ref, name)))
	if err != nil {
		return ""
	}
	return path
}

func number(value any) int64 {
	switch typed := value.(type) {
	case float64:
		return int64(typed)
	case json.Number:
		result, _ := typed.Int64()
		return result
	case string:
		result, _ := strconv.ParseInt(typed, 10, 64)
		return result
	default:
		return 0
	}
}

func stringValue(value any) string { result, _ := value.(string); return result }
