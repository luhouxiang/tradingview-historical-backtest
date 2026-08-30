package research

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/tvbt/tradingview-historical-backtest/internal/backtest"
	"github.com/tvbt/tradingview-historical-backtest/internal/calculation"
	"github.com/tvbt/tradingview-historical-backtest/internal/catalog"
	"github.com/tvbt/tradingview-historical-backtest/internal/jobs"
	"github.com/tvbt/tradingview-historical-backtest/internal/optimization"
	"github.com/tvbt/tradingview-historical-backtest/internal/pythonclient"
	"github.com/tvbt/tradingview-historical-backtest/internal/storage"
)

const aggregatorVersion = "4.0.0"

var (
	ErrInvalidRequest    = errors.New("invalid research study request")
	ErrInvalidRange      = errors.New("invalid research study range")
	ErrRevisionMismatch  = errors.New("research dataset revision mismatch")
	ErrTimeframeMismatch = errors.New("research datasets must use one timeframe")
	ErrNotReady          = errors.New("research study is not completed")
	ErrNotResumable      = errors.New("research study is not resumable")
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

type DatasetRequest struct {
	DatasetID    string         `json:"dataset_id"`
	DataRevision string         `json:"data_revision"`
	Range        backtest.Range `json:"range"`
}

type Request struct {
	Datasets              []DatasetRequest             `json:"datasets"`
	Strategy              pythonclient.AlgorithmRef    `json:"strategy"`
	Parameters            map[string]any               `json:"parameters"`
	Execution             map[string]any               `json:"execution"`
	Capital               map[string]any               `json:"capital"`
	RandomSeed            int64                        `json:"random_seed"`
	WalkForward           *WalkForwardConfig           `json:"walk_forward,omitempty"`
	StressTest            *StressTestConfig            `json:"stress_test,omitempty"`
	StatisticalValidation *StatisticalValidationConfig `json:"statistical_validation,omitempty"`
	TraceID               string                       `json:"trace_id,omitempty"`
}

type StressTestConfig struct {
	SuiteVersion            string  `json:"suite_version"`
	VolumeParticipationRate float64 `json:"volume_participation_rate"`
}

type StatisticalValidationConfig struct {
	MethodVersion        string  `json:"method_version"`
	BlockSizeTradingDays int     `json:"block_size_trading_days"`
	Iterations           int     `json:"iterations"`
	ConfidenceLevel      float64 `json:"confidence_level"`
	RandomSeed           int64   `json:"random_seed"`
	HolmAlpha            float64 `json:"holm_alpha"`
}

type WalkForwardConfig struct {
	TrainTradingDays      int                            `json:"train_trading_days"`
	ValidationTradingDays int                            `json:"validation_trading_days"`
	StepTradingDays       int                            `json:"step_trading_days"`
	SearchSpace           []optimization.SearchParameter `json:"search_space"`
	Objectives            []optimization.Objective       `json:"objectives"`
	Constraints           []optimization.Constraint      `json:"constraints"`
	Search                optimization.SearchConfig      `json:"search"`
}

type preparedDataset struct {
	DatasetID         string         `json:"dataset_id"`
	DataRevision      string         `json:"data_revision"`
	Timeframe         string         `json:"timeframe"`
	IndependenceGroup string         `json:"independence_group"`
	TradingDayCount   int            `json:"trading_day_count"`
	BarsPath          string         `json:"bars_path"`
	MetaPath          string         `json:"meta_path"`
	Range             backtest.Range `json:"range"`
	RunID             string         `json:"run_id"`
	RunSignature      string         `json:"run_signature"`
	Execution         map[string]any `json:"execution"`
}

type Submission struct {
	StudyID string
	Job     *jobs.Job
}

type Service struct {
	guard           *storage.PathGuard
	catalog         Catalog
	python          Python
	jobs            *jobs.Manager
	contractVersion string
	pollInterval    time.Duration
	progressDetails sync.Map
}

func NewService(guard *storage.PathGuard, catalogStore Catalog, python Python, manager *jobs.Manager, contractVersion string, pollInterval time.Duration) *Service {
	return &Service{guard: guard, catalog: catalogStore, python: python, jobs: manager, contractVersion: contractVersion, pollInterval: pollInterval}
}

func (s *Service) Submit(ctx context.Context, requestID, traceID string, request Request) (Submission, error) {
	if len(request.Datasets) < 1 || len(request.Datasets) > 32 {
		return Submission{}, ErrInvalidRequest
	}
	capital, err := backtest.NormalizeCapital(request.Capital)
	if err != nil {
		return Submission{}, ErrInvalidRequest
	}
	request.Capital = capital
	definitions, err := s.python.Algorithms(ctx, requestID, traceID)
	if err != nil {
		return Submission{}, err
	}
	definition, ok := findDefinition(definitions, request.Strategy)
	if !ok || request.Strategy.Kind != "strategy" || !definition.ComparisonEligible || definition.ResearchRole != "formal_strategy" {
		return Submission{}, ErrInvalidRequest
	}
	parameters, err := calculation.NormalizeParameters(definition.ParameterSchema, request.Parameters)
	if err != nil {
		return Submission{}, ErrInvalidRequest
	}
	request.Parameters = parameters
	if request.WalkForward != nil {
		walk := request.WalkForward
		if walk.TrainTradingDays < 2 || walk.ValidationTradingDays < 1 || walk.StepTradingDays < walk.ValidationTradingDays || optimization.ValidateWalkForwardSearchConfiguration(parameters, walk.SearchSpace, walk.Objectives, walk.Constraints, walk.Search, definition.ParameterSchema) != nil {
			return Submission{}, ErrInvalidRequest
		}
	}
	if request.StressTest != nil && (request.WalkForward == nil || request.StressTest.SuiteVersion != "1.0.0" || request.StressTest.VolumeParticipationRate != 0.1) {
		return Submission{}, ErrInvalidRequest
	}
	if request.StatisticalValidation != nil && (request.WalkForward == nil || request.StatisticalValidation.MethodVersion != "1.0.0" || request.StatisticalValidation.BlockSizeTradingDays < 1 || request.StatisticalValidation.Iterations < 100 || request.StatisticalValidation.Iterations > 10_000 || request.StatisticalValidation.ConfidenceLevel != 0.95 || request.StatisticalValidation.HolmAlpha != 0.05) {
		return Submission{}, ErrInvalidRequest
	}
	engineVersion := s.python.Health(ctx).Version()
	prepared := make([]preparedDataset, 0, len(request.Datasets))
	seen := map[string]bool{}
	timeframe := ""
	for _, item := range request.Datasets {
		identity := item.DatasetID + "\x00" + item.DataRevision
		if item.DatasetID == "" || item.DataRevision == "" || seen[identity] {
			return Submission{}, ErrInvalidRequest
		}
		seen[identity] = true
		meta, err := s.catalog.Get(item.DatasetID, "")
		if err != nil {
			return Submission{}, err
		}
		if meta.DataRevision != item.DataRevision {
			return Submission{}, ErrRevisionMismatch
		}
		if timeframe == "" {
			timeframe = meta.Timeframe
		} else if meta.Timeframe != timeframe {
			return Submission{}, ErrTimeframeMismatch
		}
		if !validRange(item.Range, meta.Coverage.LastBarIndex) {
			return Submission{}, ErrInvalidRange
		}
		execution, err := backtest.NormalizeExecution(request.Execution, request.Capital, meta.Instrument.ContractMultiplier)
		if err != nil {
			return Submission{}, ErrInvalidRequest
		}
		if request.WalkForward != nil && item.Range.WarmupFromBarIndex != meta.Coverage.FirstBarIndex {
			return Submission{}, ErrInvalidRange
		}
		barsPath, metaPath := datasetPaths(meta)
		if barsPath == "" {
			return Submission{}, ErrInvalidRequest
		}
		if _, err := s.guard.Resolve(barsPath); err != nil {
			return Submission{}, ErrInvalidRequest
		}
		group := meta.IndependenceGroup
		if group == "" {
			group = meta.Instrument.Exchange + "." + meta.Instrument.Product
		}
		runID := "run-" + strings.TrimPrefix(jobs.NewID(), "job-")
		runRequest := backtest.Request{DatasetID: meta.DatasetID, DataRevision: meta.DataRevision, Strategy: request.Strategy, Parameters: parameters, Range: item.Range, Execution: execution, Capital: request.Capital, RandomSeed: request.RandomSeed}
		runSignature, err := backtest.Signature(runRequest, engineVersion)
		if err != nil {
			return Submission{}, err
		}
		prepared = append(prepared, preparedDataset{DatasetID: meta.DatasetID, DataRevision: meta.DataRevision, Timeframe: meta.Timeframe, IndependenceGroup: group, TradingDayCount: meta.Coverage.TradingDayCount, BarsPath: barsPath, MetaPath: metaPath, Range: item.Range, RunID: runID, RunSignature: runSignature, Execution: execution})
	}
	sort.Slice(prepared, func(i, j int) bool { return prepared[i].DatasetID < prepared[j].DatasetID })
	studyID := "research-" + strings.TrimPrefix(jobs.NewID(), "job-")
	signature, err := Signature(request, prepared, engineVersion)
	if err != nil {
		return Submission{}, err
	}
	payload := map[string]any{
		"contract_version": s.contractVersion, "request_id": requestID, "trace_id": traceID,
		"job_id": studyID, "research_study_id": studyID, "study_signature": signature,
		"datasets": prepared, "strategy": request.Strategy, "parameters": parameters,
		"execution": executionPolicy(prepared[0].Execution), "capital": request.Capital, "random_seed": request.RandomSeed,
		"output_path": "research-studies/" + studyID,
	}
	if request.WalkForward != nil {
		payload["walk_forward"] = request.WalkForward
	}
	if request.StressTest != nil {
		payload["stress_test"] = request.StressTest
	}
	if request.StatisticalValidation != nil {
		payload["statistical_validation"] = request.StatisticalValidation
	}
	job := s.start(studyID, studyID, requestID, traceID, payload)
	return Submission{StudyID: studyID, Job: job}, nil
}

func Signature(request Request, prepared []preparedDataset, engineVersion string) (string, error) {
	datasets := make([]map[string]any, 0, len(prepared))
	for _, item := range prepared {
		datasets = append(datasets, map[string]any{"dataset_id": item.DatasetID, "data_revision": item.DataRevision, "independence_group": item.IndependenceGroup, "range": item.Range, "execution": item.Execution})
	}
	facts := map[string]any{"datasets": datasets, "strategy": request.Strategy, "parameters": request.Parameters, "capital": request.Capital, "random_seed": request.RandomSeed, "walk_forward": request.WalkForward, "stress_test": request.StressTest, "statistical_validation": request.StatisticalValidation, "engine_version": engineVersion, "aggregator_version": aggregatorVersion}
	data, err := json.Marshal(facts)
	if err != nil {
		return "", err
	}
	digest := sha256.Sum256(data)
	return "sha256:" + fmt.Sprintf("%x", digest), nil
}

func executionPolicy(resolved map[string]any) map[string]any {
	policy := make(map[string]any, len(resolved)-1)
	for key, value := range resolved {
		if key != "contract_multiplier" {
			policy[key] = value
		}
	}
	policy["contract_multiplier_source"] = "per_dataset_instrument_config"
	return policy
}

func (s *Service) start(studyID, pythonJobID, requestID, traceID string, payload map[string]any) *jobs.Job {
	ref := "research-studies/" + studyID
	return s.jobs.SubmitID(studyID, "research", func(ctx context.Context, progress func(float64)) (string, error) {
		if _, err := s.python.Submit(ctx, "research", requestID, traceID, payload); err != nil {
			return "", jobs.Fail("PYTHON_SUBMIT_FAILED", "Python research study could not be submitted", err)
		}
		ticker := time.NewTicker(s.pollInterval)
		defer ticker.Stop()
		for {
			select {
			case <-ctx.Done():
				cancelCtx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
				_ = s.python.Cancel(cancelCtx, pythonJobID, requestID, traceID)
				cancel()
				return "", ctx.Err()
			case <-ticker.C:
				status, err := s.python.Job(ctx, pythonJobID, requestID, traceID)
				if err != nil {
					return "", jobs.Fail("PYTHON_POLL_FAILED", "Python research study status could not be read", err)
				}
				progress(status.Progress)
				if len(status.ProgressDetail) > 0 {
					s.progressDetails.Store(studyID, status.ProgressDetail)
				}
				switch status.Status {
				case "completed":
					if !s.valid(ref, studyID) {
						return "", jobs.Fail("RESEARCH_COMMIT_INVALID", "Research study was not committed", nil)
					}
					if err := s.registerRuns(ref); err != nil {
						return "", jobs.Fail("RESEARCH_RUN_REGISTRATION_FAILED", "Research child runs could not be registered", err)
					}
					return ref, nil
				case "failed":
					return "", jobs.Fail("PYTHON_RESEARCH_FAILED", "Python research study failed", nil)
				case "cancelled", "interrupted":
					return "", context.Canceled
				}
			}
		}
	})
}

func (s *Service) ProgressDetail(studyID string) map[string]any {
	value, ok := s.progressDetails.Load(studyID)
	if !ok {
		return nil
	}
	detail, ok := value.(map[string]any)
	if !ok {
		return nil
	}
	copy := make(map[string]any, len(detail))
	for key, item := range detail {
		copy[key] = item
	}
	return copy
}

func (s *Service) Status(studyID string) (*jobs.Job, map[string]any, bool) {
	job, ok := s.jobs.Get(studyID)
	if !ok || job.Kind != "research" {
		return nil, nil, false
	}
	var manifest map[string]any
	if job.Status == jobs.Completed {
		manifest, _ = s.readObject(job.ResultRef, "research-study.json")
	}
	return job, manifest, true
}

func (s *Service) Cancel(studyID string) (*jobs.Job, bool) {
	job, ok := s.jobs.Get(studyID)
	if !ok || job.Kind != "research" {
		return nil, false
	}
	return s.jobs.Cancel(studyID)
}

func (s *Service) Resume(studyID, requestID, traceID string) (*jobs.Job, error) {
	job, ok := s.jobs.Get(studyID)
	if !ok || job.Kind != "research" || (job.Status != jobs.Interrupted && job.Status != jobs.Cancelled && job.Status != jobs.Failed) {
		return nil, ErrNotResumable
	}
	journal, err := s.readObject("research-studies", studyID+".journal.json")
	if err != nil {
		return nil, ErrNotResumable
	}
	payload, ok := journal["payload"].(map[string]any)
	if !ok || payload["research_study_id"] != studyID {
		return nil, ErrNotResumable
	}
	pythonJobID := studyID + "-resume-" + strings.TrimPrefix(jobs.NewID(), "job-")
	payload["request_id"], payload["trace_id"], payload["job_id"] = requestID, traceID, pythonJobID
	return s.start(studyID, pythonJobID, requestID, traceID, payload), nil
}

func (s *Service) Results(studyID string) ([]map[string]any, map[string]any, error) {
	job, manifest, ok := s.Status(studyID)
	if !ok {
		return nil, nil, catalog.ErrNotFound
	}
	if job.Status != jobs.Completed || job.ResultRef == "" {
		return nil, nil, ErrNotReady
	}
	data, err := os.ReadFile(mustResolve(s.guard, job.ResultRef, "results.json"))
	if err != nil {
		return nil, nil, err
	}
	var results []map[string]any
	if err := json.Unmarshal(data, &results); err != nil {
		return nil, nil, err
	}
	aggregate, _ := manifest["aggregate"].(map[string]any)
	return results, aggregate, nil
}

func (s *Service) List() ([]map[string]any, error) {
	directory, err := s.guard.Resolve("research-studies")
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
	items := make([]map[string]any, 0)
	for _, entry := range entries {
		if !entry.IsDir() {
			continue
		}
		ref := "research-studies/" + entry.Name()
		if _, err := os.Stat(mustResolve(s.guard, ref, "_SUCCESS")); err != nil {
			continue
		}
		manifest, err := s.readObject(ref, "research-study.json")
		if err == nil {
			items = append(items, manifest)
		}
	}
	sort.Slice(items, func(i, j int) bool { return text(items[i]["created_at"]) > text(items[j]["created_at"]) })
	return items, nil
}

func (s *Service) valid(ref, studyID string) bool {
	for _, name := range []string{"research-study.json", "results.json", "_SUCCESS"} {
		if info, err := os.Stat(mustResolve(s.guard, ref, name)); err != nil || !info.Mode().IsRegular() {
			return false
		}
	}
	manifest, err := s.readObject(ref, "research-study.json")
	if err != nil || manifest["research_study_id"] != studyID {
		return false
	}
	if manifest["study_mode"] == "walk_forward" || manifest["study_mode"] == "walk_forward_stress" || manifest["study_mode"] == "walk_forward_certification" {
		artifacts, ok := manifest["artifacts"].(map[string]any)
		path := ""
		if ok {
			path = text(artifacts["out_of_sample_daily_returns"])
		}
		resolved, resolveErr := s.guard.Resolve(path)
		if path == "" || resolveErr != nil {
			return false
		}
		if info, statErr := os.Stat(resolved); statErr != nil || !info.Mode().IsRegular() {
			return false
		}
		if _, hasStress := manifest["stress_test"]; hasStress {
			stressPath := text(artifacts["stress_results"])
			stressResolved, stressResolveErr := s.guard.Resolve(stressPath)
			if stressPath == "" || stressResolveErr != nil {
				return false
			}
			if info, statErr := os.Stat(stressResolved); statErr != nil || !info.Mode().IsRegular() {
				return false
			}
		}
		if manifest["study_mode"] == "walk_forward_certification" {
			statisticsPath := text(artifacts["statistical_evidence"])
			statisticsResolved, statisticsResolveErr := s.guard.Resolve(statisticsPath)
			if statisticsPath == "" || statisticsResolveErr != nil {
				return false
			}
			if info, statErr := os.Stat(statisticsResolved); statErr != nil || !info.Mode().IsRegular() {
				return false
			}
		}
	}
	return true
}

func (s *Service) registerRuns(ref string) error {
	manifest, err := s.readObject(ref, "research-study.json")
	if err == nil {
		if childRuns, ok := manifest["child_runs"].([]any); ok {
			for _, raw := range childRuns {
				child, ok := raw.(map[string]any)
				if !ok || text(child["run_id"]) == "" {
					continue
				}
				if err := s.registerRun(text(child["run_id"]), text(child["run_signature"])); err != nil {
					return err
				}
			}
			return nil
		}
	}
	data, err := os.ReadFile(mustResolve(s.guard, ref, "results.json"))
	if err != nil {
		return err
	}
	var results []map[string]any
	if err := json.Unmarshal(data, &results); err != nil {
		return err
	}
	for _, result := range results {
		if result["status"] != "completed" || text(result["run_id"]) == "" {
			continue
		}
		if err := s.registerRun(text(result["run_id"]), text(result["run_signature"])); err != nil {
			return err
		}
	}
	return nil
}

func (s *Service) registerRun(runID, signature string) error {
	runRef := "runs/" + runID
	if _, err := os.Stat(mustResolve(s.guard, runRef, "_SUCCESS")); err != nil {
		return err
	}
	s.jobs.RecordCompleted(runID, "backtest", runRef)
	_, _ = s.jobs.SetMetadata(runID, map[string]string{"run_signature": signature})
	return nil
}

func (s *Service) readObject(ref, name string) (map[string]any, error) {
	data, err := os.ReadFile(mustResolve(s.guard, ref, name))
	if err != nil {
		return nil, err
	}
	value := map[string]any{}
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

func findDefinition(values []pythonclient.AlgorithmDefinition, ref pythonclient.AlgorithmRef) (pythonclient.AlgorithmDefinition, bool) {
	for _, value := range values {
		if value.AlgorithmRef == ref {
			return value, true
		}
	}
	return pythonclient.AlgorithmDefinition{}, false
}

func validRange(value backtest.Range, last int64) bool {
	return value.WarmupFromBarIndex >= 0 && value.FromBarIndex >= value.WarmupFromBarIndex && value.ToBarIndex >= value.FromBarIndex && value.ToBarIndex <= last
}

func text(value any) string { result, _ := value.(string); return result }
