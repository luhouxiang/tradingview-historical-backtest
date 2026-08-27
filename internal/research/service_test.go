package research

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/tvbt/tradingview-historical-backtest/internal/backtest"
	"github.com/tvbt/tradingview-historical-backtest/internal/catalog"
	"github.com/tvbt/tradingview-historical-backtest/internal/jobs"
	"github.com/tvbt/tradingview-historical-backtest/internal/optimization"
	"github.com/tvbt/tradingview-historical-backtest/internal/pythonclient"
	"github.com/tvbt/tradingview-historical-backtest/internal/storage"
)

type testCatalog map[string]catalog.DatasetMeta

func (value testCatalog) Get(id, _ string) (catalog.DatasetMeta, error) {
	meta, ok := value[id]
	if !ok {
		return catalog.DatasetMeta{}, catalog.ErrNotFound
	}
	return meta, nil
}

type testPython struct {
	definition pythonclient.AlgorithmDefinition
}

func (value testPython) Algorithms(context.Context, string, string) ([]pythonclient.AlgorithmDefinition, error) {
	return []pythonclient.AlgorithmDefinition{value.definition}, nil
}
func (testPython) Health(context.Context) pythonclient.Health {
	return pythonclient.Health{ContractVersion: "1.0.0"}
}
func (testPython) Submit(context.Context, string, string, string, any) (pythonclient.JobStatus, error) {
	return pythonclient.JobStatus{}, errors.New("stop after validation")
}
func (testPython) Job(context.Context, string, string, string) (pythonclient.JobStatus, error) {
	return pythonclient.JobStatus{}, nil
}
func (testPython) Cancel(context.Context, string, string, string) error { return nil }

type resumePython struct {
	mu        sync.Mutex
	submitted []string
}

func (*resumePython) Algorithms(context.Context, string, string) ([]pythonclient.AlgorithmDefinition, error) {
	return nil, nil
}
func (*resumePython) Health(context.Context) pythonclient.Health { return pythonclient.Health{} }
func (value *resumePython) Submit(_ context.Context, _ string, _ string, _ string, payload any) (pythonclient.JobStatus, error) {
	id := payload.(map[string]any)["job_id"].(string)
	value.mu.Lock()
	value.submitted = append(value.submitted, id)
	value.mu.Unlock()
	return pythonclient.JobStatus{JobID: id, Status: "queued"}, nil
}
func (*resumePython) Job(context.Context, string, string, string) (pythonclient.JobStatus, error) {
	return pythonclient.JobStatus{Status: "failed"}, nil
}
func (*resumePython) Cancel(context.Context, string, string, string) error { return nil }

func meta(id, timeframe, group, revision string) catalog.DatasetMeta {
	return catalog.DatasetMeta{
		DatasetID: id, DataRevision: revision, Timeframe: timeframe, IndependenceGroup: group,
		Coverage: catalog.CoverageMeta{LastBarIndex: 100, TradingDayCount: 600},
		Files:    []catalog.FileMeta{{Role: "bars", Path: "normalized/" + id + "/revision/bars.parquet"}},
	}
}

func request(definition pythonclient.AlgorithmDefinition, revision string) Request {
	rangeValue := backtest.Range{WarmupFromBarIndex: 0, FromBarIndex: 0, ToBarIndex: 100}
	return Request{
		Datasets: []DatasetRequest{{DatasetID: "A.5m", DataRevision: revision, Range: rangeValue}, {DatasetID: "B.5m", DataRevision: revision, Range: rangeValue}},
		Strategy: definition.AlgorithmRef, Parameters: map[string]any{},
		Execution: map[string]any{"signal_timing": "bar_close", "fill_timing": "next_bar_open", "commission": map[string]any{"mode": "fixed_per_contract"}, "slippage": map[string]any{"mode": "ticks", "value": 0}},
		Capital:   map[string]any{"initial_cash_i64": 1000, "currency": "CNY", "money_scale": 100}, RandomSeed: 7,
	}
}

func TestSubmitRejectsMixedTimeframesAndRevisionDrift(t *testing.T) {
	guard, err := storage.NewPathGuard(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	revision := "sha256:" + strings.Repeat("1", 64)
	definition := pythonclient.AlgorithmDefinition{
		AlgorithmRef:       pythonclient.AlgorithmRef{Kind: "strategy", AlgorithmID: "formal", AlgorithmVersion: "1", SourceHash: "sha256:" + strings.Repeat("2", 64)},
		ComparisonEligible: true, ResearchRole: "formal_strategy",
		ParameterSchema: map[string]any{"type": "object", "additionalProperties": false, "properties": map[string]any{}},
	}
	manager := jobs.NewManager()
	service := NewService(guard, testCatalog{"A.5m": meta("A.5m", "5m", "SHFE.AO", revision), "B.5m": meta("B.5m", "15m", "DCE.I", revision)}, testPython{definition}, manager, "1.0.0", time.Millisecond)
	_, err = service.Submit(context.Background(), "request", "trace", request(definition, revision))
	if !errors.Is(err, ErrTimeframeMismatch) {
		t.Fatalf("expected timeframe mismatch, got %v", err)
	}

	values := testCatalog{"A.5m": meta("A.5m", "5m", "SHFE.AO", revision), "B.5m": meta("B.5m", "5m", "DCE.I", "sha256:"+strings.Repeat("3", 64))}
	service = NewService(guard, values, testPython{definition}, manager, "1.0.0", time.Millisecond)
	_, err = service.Submit(context.Background(), "request", "trace", request(definition, revision))
	if !errors.Is(err, ErrRevisionMismatch) {
		t.Fatalf("expected revision mismatch, got %v", err)
	}

	first := meta("A.5m", "5m", "SHFE.AO", revision)
	first.Coverage.FirstBarIndex = 1
	second := meta("B.5m", "5m", "DCE.I", revision)
	second.Coverage.FirstBarIndex = 1
	service = NewService(guard, testCatalog{"A.5m": first, "B.5m": second}, testPython{definition}, manager, "1.0.0", time.Millisecond)
	walkRequest := request(definition, revision)
	walkRequest.WalkForward = &WalkForwardConfig{
		TrainTradingDays: 252, ValidationTradingDays: 63, StepTradingDays: 63,
		Objectives: []optimization.Objective{{Metric: "total_return", Direction: "maximize"}},
		Search:     optimization.SearchConfig{Method: "grid", Budget: 1, RandomSeed: 7},
	}
	_, err = service.Submit(context.Background(), "request", "trace", walkRequest)
	if !errors.Is(err, ErrInvalidRange) {
		t.Fatalf("expected dataset-start warmup rejection, got %v", err)
	}
}

func TestSubmitAllowsOneDatasetAsExploratoryStudy(t *testing.T) {
	guard, err := storage.NewPathGuard(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	revision := "sha256:" + strings.Repeat("1", 64)
	definition := pythonclient.AlgorithmDefinition{
		AlgorithmRef:       pythonclient.AlgorithmRef{Kind: "strategy", AlgorithmID: "formal", AlgorithmVersion: "1", SourceHash: "sha256:" + strings.Repeat("2", 64)},
		ComparisonEligible: true, ResearchRole: "formal_strategy",
		ParameterSchema: map[string]any{"type": "object", "additionalProperties": false, "properties": map[string]any{}},
	}
	manager := jobs.NewManager()
	service := NewService(
		guard,
		testCatalog{"A.5m": meta("A.5m", "5m", "SHFE.AO", revision)},
		testPython{definition},
		manager,
		"1.0.0",
		time.Millisecond,
	)
	requestValue := request(definition, revision)
	requestValue.Datasets = requestValue.Datasets[:1]

	submission, err := service.Submit(context.Background(), "request", "trace", requestValue)
	if err != nil {
		t.Fatalf("single-dataset exploratory study was rejected: %v", err)
	}
	if submission.StudyID == "" || submission.Job.Kind != "research" {
		t.Fatalf("unexpected submission: %#v", submission)
	}
}

func TestSignatureIncludesIndependenceGroup(t *testing.T) {
	definition := pythonclient.AlgorithmRef{Kind: "strategy", AlgorithmID: "formal", AlgorithmVersion: "1", SourceHash: "sha256:" + strings.Repeat("2", 64)}
	requestValue := Request{Strategy: definition, Parameters: map[string]any{}, Execution: map[string]any{}, Capital: map[string]any{}, RandomSeed: 7}
	items := []preparedDataset{{DatasetID: "A", DataRevision: "r", IndependenceGroup: "SHFE.AO"}}
	first, _ := Signature(requestValue, items, "engine")
	items[0].IndependenceGroup = "DCE.I"
	second, _ := Signature(requestValue, items, "engine")
	if first == second {
		t.Fatal("independence group did not affect study signature")
	}
}

func TestSignatureIncludesWalkForwardConfiguration(t *testing.T) {
	requestValue := Request{
		Strategy:   pythonclient.AlgorithmRef{Kind: "strategy", AlgorithmID: "formal"},
		Parameters: map[string]any{}, Execution: map[string]any{}, Capital: map[string]any{},
		WalkForward: &WalkForwardConfig{TrainTradingDays: 252, ValidationTradingDays: 63, StepTradingDays: 63},
	}
	first, _ := Signature(requestValue, []preparedDataset{{DatasetID: "A"}}, "engine")
	requestValue.WalkForward.StepTradingDays = 126
	second, _ := Signature(requestValue, []preparedDataset{{DatasetID: "A"}}, "engine")
	if first == second {
		t.Fatal("walk-forward configuration did not affect study signature")
	}
}

func TestSignatureIncludesStressConfiguration(t *testing.T) {
	requestValue := Request{
		Strategy:   pythonclient.AlgorithmRef{Kind: "strategy", AlgorithmID: "formal"},
		Parameters: map[string]any{}, Execution: map[string]any{}, Capital: map[string]any{},
		WalkForward: &WalkForwardConfig{TrainTradingDays: 252, ValidationTradingDays: 63, StepTradingDays: 63},
		StressTest:  &StressTestConfig{SuiteVersion: "1.0.0", VolumeParticipationRate: 0.1},
	}
	first, _ := Signature(requestValue, []preparedDataset{{DatasetID: "A"}}, "engine")
	requestValue.StressTest.SuiteVersion = "2.0.0"
	second, _ := Signature(requestValue, []preparedDataset{{DatasetID: "A"}}, "engine")
	if first == second {
		t.Fatal("stress configuration did not affect study signature")
	}
}

func TestSignatureIncludesStatisticalValidationConfiguration(t *testing.T) {
	requestValue := Request{
		Strategy:   pythonclient.AlgorithmRef{Kind: "strategy", AlgorithmID: "formal"},
		Parameters: map[string]any{}, Execution: map[string]any{}, Capital: map[string]any{},
		WalkForward: &WalkForwardConfig{TrainTradingDays: 252, ValidationTradingDays: 63, StepTradingDays: 63},
		StatisticalValidation: &StatisticalValidationConfig{
			MethodVersion: "1.0.0", BlockSizeTradingDays: 5, Iterations: 2000,
			ConfidenceLevel: 0.95, RandomSeed: 7, HolmAlpha: 0.05,
		},
	}
	first, _ := Signature(requestValue, []preparedDataset{{DatasetID: "A"}}, "engine")
	requestValue.StatisticalValidation.RandomSeed = 8
	second, _ := Signature(requestValue, []preparedDataset{{DatasetID: "A"}}, "engine")
	if first == second {
		t.Fatal("statistical validation configuration did not affect study signature")
	}
}

func TestResumeKeepsStudyIdentityAndUsesFreshPythonJobID(t *testing.T) {
	guard, err := storage.NewPathGuard(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	manager := jobs.NewManager()
	studyID := "research-resume"
	manager.SubmitID(studyID, "research", func(context.Context, func(float64)) (string, error) {
		return "", errors.New("interrupted")
	})
	deadline := time.Now().Add(time.Second)
	for time.Now().Before(deadline) {
		job, _ := manager.Get(studyID)
		if job.Status == jobs.Failed {
			break
		}
		time.Sleep(time.Millisecond)
	}
	directory, _ := guard.Resolve("research-studies")
	if err := os.MkdirAll(directory, 0o750); err != nil {
		t.Fatal(err)
	}
	journal := map[string]any{"research_study_id": studyID, "payload": map[string]any{"research_study_id": studyID, "job_id": studyID}}
	data, _ := json.Marshal(journal)
	if err := os.WriteFile(filepath.Join(directory, studyID+".journal.json"), data, 0o640); err != nil {
		t.Fatal(err)
	}
	python := &resumePython{}
	service := NewService(guard, testCatalog{}, python, manager, "1.0.0", time.Millisecond)
	job, err := service.Resume(studyID, "request", "trace")
	if err != nil {
		t.Fatal(err)
	}
	if job.ID != studyID {
		t.Fatalf("study identity changed to %s", job.ID)
	}
	deadline = time.Now().Add(time.Second)
	for time.Now().Before(deadline) {
		python.mu.Lock()
		count := len(python.submitted)
		python.mu.Unlock()
		if count > 0 {
			break
		}
		time.Sleep(time.Millisecond)
	}
	python.mu.Lock()
	submitted := append([]string(nil), python.submitted...)
	python.mu.Unlock()
	if len(submitted) != 1 || submitted[0] == studyID || !strings.HasPrefix(submitted[0], studyID+"-resume-") {
		t.Fatalf("unexpected Python job IDs: %#v", submitted)
	}
}

func TestRegisterRunsUsesWalkForwardManifestChildren(t *testing.T) {
	guard, err := storage.NewPathGuard(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	runID := "run-validation"
	runDirectory, _ := guard.Resolve("runs/" + runID)
	if err := os.MkdirAll(runDirectory, 0o750); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(runDirectory, "_SUCCESS"), nil, 0o640); err != nil {
		t.Fatal(err)
	}
	studyDirectory, _ := guard.Resolve("research-studies/research-test")
	if err := os.MkdirAll(studyDirectory, 0o750); err != nil {
		t.Fatal(err)
	}
	signature := "sha256:" + strings.Repeat("4", 64)
	manifest := map[string]any{"child_runs": []map[string]any{{"run_id": runID, "run_signature": signature}}}
	data, _ := json.Marshal(manifest)
	if err := os.WriteFile(filepath.Join(studyDirectory, "research-study.json"), data, 0o640); err != nil {
		t.Fatal(err)
	}
	manager := jobs.NewManager()
	service := NewService(guard, testCatalog{}, &resumePython{}, manager, "1.0.0", time.Millisecond)
	if err := service.registerRuns("research-studies/research-test"); err != nil {
		t.Fatal(err)
	}
	job, ok := manager.Get(runID)
	if !ok || job.Kind != "backtest" || job.Metadata["run_signature"] != signature {
		t.Fatalf("walk-forward child was not registered: %#v", job)
	}
}
