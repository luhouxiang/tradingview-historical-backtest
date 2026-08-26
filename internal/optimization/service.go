package optimization

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/tvbt/tradingview-historical-backtest/internal/backtest"
	"github.com/tvbt/tradingview-historical-backtest/internal/calculation"
	"github.com/tvbt/tradingview-historical-backtest/internal/catalog"
	"github.com/tvbt/tradingview-historical-backtest/internal/jobs"
	"github.com/tvbt/tradingview-historical-backtest/internal/pythonclient"
	"github.com/tvbt/tradingview-historical-backtest/internal/storage"
)

var (
	ErrInvalidRequest   = errors.New("invalid optimization request")
	ErrInvalidRange     = errors.New("invalid optimization range")
	ErrRevisionMismatch = errors.New("data revision mismatch")
	ErrNotReady         = errors.New("optimization study is not completed")
)

var supportedMetrics = map[string]bool{
	"total_return": true, "sharpe": true, "max_drawdown": true,
	"win_rate": true, "trade_count": true, "profit_factor": true,
	"expectancy_i64": true,
}

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

type SearchParameter struct {
	Name       string   `json:"name"`
	Type       string   `json:"type"`
	Minimum    *float64 `json:"minimum,omitempty"`
	Maximum    *float64 `json:"maximum,omitempty"`
	Step       *float64 `json:"step,omitempty"`
	Candidates []any    `json:"candidates,omitempty"`
}

type Objective struct {
	Metric    string `json:"metric"`
	Direction string `json:"direction"`
}

type Constraint struct {
	Metric   string  `json:"metric"`
	Operator string  `json:"operator"`
	Value    float64 `json:"value"`
}

type SearchConfig struct {
	Method     string `json:"method"`
	Budget     int    `json:"budget"`
	RandomSeed int64  `json:"random_seed"`
}

type Ranges struct {
	Train      backtest.Range `json:"train"`
	Validation backtest.Range `json:"validation"`
}

type Request struct {
	DatasetID      string                    `json:"dataset_id"`
	DataRevision   string                    `json:"data_revision"`
	Strategy       pythonclient.AlgorithmRef `json:"strategy"`
	BaseParameters map[string]any            `json:"base_parameters"`
	SearchSpace    []SearchParameter         `json:"search_space"`
	Objectives     []Objective               `json:"objectives"`
	Constraints    []Constraint              `json:"constraints"`
	Search         SearchConfig              `json:"search"`
	Ranges         Ranges                    `json:"ranges"`
	Execution      map[string]any            `json:"execution"`
	Capital        map[string]any            `json:"capital"`
	RiskOverlay    *backtest.RiskOverlay     `json:"risk_overlay,omitempty"`
	TraceID        string                    `json:"trace_id,omitempty"`
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
}

func NewService(guard *storage.PathGuard, catalogStore Catalog, python Python, manager *jobs.Manager, contractVersion string, pollInterval time.Duration) *Service {
	return &Service{guard: guard, catalog: catalogStore, python: python, jobs: manager, contractVersion: contractVersion, pollInterval: pollInterval}
}

func (s *Service) Submit(ctx context.Context, requestID, traceID string, request Request) (Submission, error) {
	meta, err := s.catalog.Get(request.DatasetID, "")
	if err != nil {
		return Submission{}, err
	}
	if meta.DataRevision != request.DataRevision {
		return Submission{}, ErrRevisionMismatch
	}
	if !validRange(request.Ranges.Train, meta.Coverage.LastBarIndex) || !validRange(request.Ranges.Validation, meta.Coverage.LastBarIndex) || request.Ranges.Train.ToBarIndex >= request.Ranges.Validation.FromBarIndex {
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
	parameters, err := calculation.NormalizeParameters(definition.ParameterSchema, request.BaseParameters)
	if err != nil {
		return Submission{}, ErrInvalidRequest
	}
	request.BaseParameters = parameters
	riskOverlay, err := backtest.NormalizeRiskOverlay(definitions, request.RiskOverlay, request.DataRevision, meta.Coverage.FirstBarIndex, meta.Coverage.LastBarIndex)
	if err != nil {
		return Submission{}, ErrInvalidRequest
	}
	request.RiskOverlay = riskOverlay
	if err := validateStudy(request, definition.ParameterSchema); err != nil {
		return Submission{}, err
	}
	studyID := jobs.NewID()
	job := s.start(studyID, requestID, traceID, request, meta)
	return Submission{StudyID: studyID, Job: job}, nil
}

func (s *Service) start(studyID, requestID, traceID string, request Request, meta catalog.DatasetMeta) *jobs.Job {
	ref := "studies/" + studyID
	return s.jobs.SubmitID(studyID, "optimization", func(ctx context.Context, progress func(float64)) (string, error) {
		barsPath, metaPath := datasetPaths(meta)
		cacheKey, err := studyKey(request, s.python.Health(ctx).Version())
		if err != nil {
			return "", jobs.Fail("STUDY_KEY_FAILED", "Optimization facts could not be encoded", err)
		}
		payload := map[string]any{
			"contract_version": s.contractVersion, "request_id": requestID, "trace_id": traceID,
			"job_id": studyID, "study_id": studyID, "cache_key": cacheKey,
			"dataset":   map[string]any{"dataset_id": meta.DatasetID, "data_revision": meta.DataRevision, "bars_path": barsPath, "meta_path": metaPath},
			"algorithm": request.Strategy, "base_parameters": request.BaseParameters,
			"parameters":   request.BaseParameters,
			"search_space": request.SearchSpace, "objectives": request.Objectives,
			"constraints": request.Constraints, "search": request.Search, "ranges": request.Ranges,
			"execution": request.Execution, "capital": request.Capital,
			"calculation_mode": "causal_events", "output_path": ref,
		}
		if request.RiskOverlay != nil {
			payload["risk_overlay"] = request.RiskOverlay
		}
		if _, err := s.python.Submit(ctx, "optimization", requestID, traceID, payload); err != nil {
			return "", jobs.Fail("PYTHON_SUBMIT_FAILED", "Python optimization could not be submitted", err)
		}
		progress(.05)
		ticker := time.NewTicker(s.pollInterval)
		defer ticker.Stop()
		for {
			select {
			case <-ctx.Done():
				cancelCtx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
				_ = s.python.Cancel(cancelCtx, studyID, requestID, traceID)
				cancel()
				return "", ctx.Err()
			case <-ticker.C:
				status, err := s.python.Job(ctx, studyID, requestID, traceID)
				if err != nil {
					return "", jobs.Fail("PYTHON_POLL_FAILED", "Python optimization status could not be read", err)
				}
				progress(.05 + status.Progress*.95)
				switch status.Status {
				case "completed":
					if !s.validStudy(ref, studyID) {
						return "", jobs.Fail("STUDY_COMMIT_INVALID", "Optimization study was not committed", nil)
					}
					return ref, nil
				case "failed":
					return "", jobs.Fail("PYTHON_OPTIMIZATION_FAILED", "Python optimization failed", nil)
				case "cancelled", "interrupted":
					return "", context.Canceled
				}
			}
		}
	})
}

func (s *Service) Status(studyID string) (*jobs.Job, map[string]any, bool) {
	job, ok := s.jobs.Get(studyID)
	if !ok || job.Kind != "optimization" {
		return nil, nil, false
	}
	var manifest map[string]any
	if job.Status == jobs.Completed {
		manifest, _ = s.readObject(job.ResultRef, "study.json")
	}
	return job, manifest, true
}

func (s *Service) Cancel(studyID string) (*jobs.Job, bool) {
	job, ok := s.jobs.Get(studyID)
	if !ok || job.Kind != "optimization" {
		return nil, false
	}
	return s.jobs.Cancel(studyID)
}

func (s *Service) Evaluations(studyID string) ([]map[string]any, map[string]any, error) {
	job, _, ok := s.Status(studyID)
	if !ok {
		return nil, nil, catalog.ErrNotFound
	}
	if job.Status != jobs.Completed || job.ResultRef == "" {
		return nil, nil, ErrNotReady
	}
	directory, err := s.guard.Resolve(job.ResultRef)
	if err != nil {
		return nil, nil, err
	}
	data, err := os.ReadFile(filepath.Join(directory, "evaluations.json"))
	if err != nil {
		return nil, nil, err
	}
	var evaluations []map[string]any
	if err := json.Unmarshal(data, &evaluations); err != nil {
		return nil, nil, err
	}
	stability, err := s.readObject(job.ResultRef, "stability.json")
	return evaluations, stability, err
}

func (s *Service) readObject(ref, name string) (map[string]any, error) {
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

func (s *Service) validStudy(ref, studyID string) bool {
	directory, err := s.guard.Resolve(ref)
	if err != nil {
		return false
	}
	for _, name := range []string{"study.json", "evaluations.json", "stability.json", "log.ndjson", "_SUCCESS"} {
		if info, err := os.Stat(filepath.Join(directory, name)); err != nil || !info.Mode().IsRegular() {
			return false
		}
	}
	manifest, err := s.readObject(ref, "study.json")
	return err == nil && manifest["study_id"] == studyID
}

func validateStudy(request Request, schema map[string]any) error {
	if err := ValidateSearchConfiguration(request.BaseParameters, request.SearchSpace, request.Objectives, request.Constraints, request.Search, schema); err != nil {
		return err
	}
	if !validExecution(request.Execution) || !validCapital(request.Capital) {
		return ErrInvalidRequest
	}
	return nil
}

// ValidateSearchConfiguration validates the reusable deterministic finite-search
// contract without depending on a particular train/validation range.
func ValidateSearchConfiguration(baseParameters map[string]any, searchSpace []SearchParameter, objectives []Objective, constraints []Constraint, search SearchConfig, schema map[string]any) error {
	return validateSearchConfiguration(baseParameters, searchSpace, objectives, constraints, search, schema, false)
}

// ValidateWalkForwardSearchConfiguration permits an empty search space, which
// represents one fixed base-parameter candidate for strategies without tunable semantics.
func ValidateWalkForwardSearchConfiguration(baseParameters map[string]any, searchSpace []SearchParameter, objectives []Objective, constraints []Constraint, search SearchConfig, schema map[string]any) error {
	return validateSearchConfiguration(baseParameters, searchSpace, objectives, constraints, search, schema, true)
}

func validateSearchConfiguration(baseParameters map[string]any, searchSpace []SearchParameter, objectives []Objective, constraints []Constraint, search SearchConfig, schema map[string]any, allowEmpty bool) error {
	if (!allowEmpty && len(searchSpace) == 0) || len(objectives) == 0 || search.Budget < 1 || search.Budget > 100 || (search.Method != "grid" && search.Method != "random") {
		return ErrInvalidRequest
	}
	for _, objective := range objectives {
		if !supportedMetrics[objective.Metric] || (objective.Direction != "maximize" && objective.Direction != "minimize") {
			return ErrInvalidRequest
		}
	}
	for _, constraint := range constraints {
		if !supportedMetrics[constraint.Metric] || (constraint.Operator != ">=" && constraint.Operator != "<=") || math.IsNaN(constraint.Value) || math.IsInf(constraint.Value, 0) {
			return ErrInvalidRequest
		}
	}
	seen := map[string]bool{}
	combinations := int64(1)
	for _, space := range searchSpace {
		if space.Name == "" || seen[space.Name] {
			return ErrInvalidRequest
		}
		seen[space.Name] = true
		values, err := searchValues(space)
		if err != nil {
			return err
		}
		combinations *= int64(len(values))
		if combinations > 100_000 {
			return ErrInvalidRequest
		}
		for _, candidate := range values {
			parameters := cloneMap(baseParameters)
			parameters[space.Name] = candidate
			if _, err := calculation.NormalizeParameters(schema, parameters); err != nil {
				return ErrInvalidRequest
			}
		}
	}
	return nil
}

func searchValues(space SearchParameter) ([]any, error) {
	if space.Type != "integer" && space.Type != "number" && space.Type != "boolean" && space.Type != "string" {
		return nil, ErrInvalidRequest
	}
	if len(space.Candidates) > 0 {
		result := make([]any, 0, len(space.Candidates))
		seen := map[string]bool{}
		for _, value := range space.Candidates {
			if !candidateType(value, space.Type) {
				return nil, ErrInvalidRequest
			}
			encoded, _ := json.Marshal(value)
			if seen[string(encoded)] {
				return nil, ErrInvalidRequest
			}
			seen[string(encoded)] = true
			result = append(result, value)
		}
		return result, nil
	}
	if (space.Type != "integer" && space.Type != "number") || space.Minimum == nil || space.Maximum == nil || space.Step == nil || *space.Step <= 0 || *space.Maximum < *space.Minimum {
		return nil, ErrInvalidRequest
	}
	count := int(math.Floor((*space.Maximum-*space.Minimum)/(*space.Step)+1e-12)) + 1
	if count < 1 || count > 10_000 {
		return nil, ErrInvalidRequest
	}
	result := make([]any, count)
	for index := range count {
		value := *space.Minimum + float64(index)**space.Step
		if space.Type == "integer" {
			if value != math.Trunc(value) {
				return nil, ErrInvalidRequest
			}
			result[index] = int64(value)
		} else {
			result[index] = value
		}
	}
	return result, nil
}

func candidateType(value any, kind string) bool {
	switch kind {
	case "integer":
		switch number := value.(type) {
		case json.Number:
			_, err := number.Int64()
			return err == nil
		case float64:
			return number == math.Trunc(number)
		case int, int64:
			return true
		}
	case "number":
		_, ok := numeric(value)
		return ok
	case "boolean":
		_, ok := value.(bool)
		return ok
	case "string":
		_, ok := value.(string)
		return ok
	}
	return false
}

func numeric(value any) (float64, bool) {
	switch number := value.(type) {
	case json.Number:
		result, err := number.Float64()
		return result, err == nil
	case float64:
		return number, !math.IsNaN(number) && !math.IsInf(number, 0)
	case int:
		return float64(number), true
	case int64:
		return float64(number), true
	default:
		return 0, false
	}
}

func validRange(value backtest.Range, last int64) bool {
	return value.WarmupFromBarIndex >= 0 && value.FromBarIndex >= value.WarmupFromBarIndex && value.ToBarIndex >= value.FromBarIndex && value.ToBarIndex <= last
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

func studyKey(request Request, engineVersion string) (string, error) {
	data, err := json.Marshal(map[string]any{
		"data_revision": request.DataRevision, "strategy": request.Strategy,
		"base_parameters": request.BaseParameters, "search_space": request.SearchSpace,
		"objectives": request.Objectives, "constraints": request.Constraints,
		"search": request.Search, "ranges": request.Ranges, "execution": request.Execution,
		"capital": request.Capital, "risk_overlay": request.RiskOverlay, "engine_version": engineVersion,
	})
	if err != nil {
		return "", fmt.Errorf("encode study key: %w", err)
	}
	digest := sha256.Sum256(data)
	return "sha256:" + hex.EncodeToString(digest[:]), nil
}

func cloneMap(source map[string]any) map[string]any {
	result := make(map[string]any, len(source))
	for key, value := range source {
		result[key] = value
	}
	return result
}
