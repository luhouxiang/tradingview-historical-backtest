package comparison

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/tvbt/tradingview-historical-backtest/internal/backtest"
	"github.com/tvbt/tradingview-historical-backtest/internal/catalog"
	"github.com/tvbt/tradingview-historical-backtest/internal/jobs"
	"github.com/tvbt/tradingview-historical-backtest/internal/pythonclient"
	"github.com/tvbt/tradingview-historical-backtest/internal/storage"
)

type fakeCatalog struct{ meta catalog.DatasetMeta }

func (f fakeCatalog) Get(string, string) (catalog.DatasetMeta, error) { return f.meta, nil }

type fakePython struct {
	guard       *storage.PathGuard
	definitions []pythonclient.AlgorithmDefinition
}

func TestSignatureStableAndSensitive(t *testing.T) {
	request := Request{DatasetID: "TEST.5m", DataRevision: "sha256:" + strings.Repeat("1", 64), Range: backtest.Range{WarmupFromBarIndex: 0, FromBarIndex: 1, ToBarIndex: 9}, Execution: map[string]any{"fill_timing": "next_bar_open"}, Capital: map[string]any{"initial_cash_i64": 1000}, RandomSeed: 7, MinimumTradeCount: 20}
	items := []preparedItem{{Strategy: pythonclient.AlgorithmRef{Kind: "strategy", AlgorithmID: "a", AlgorithmVersion: "1", SourceHash: "sha256:" + strings.Repeat("2", 64)}, Parameters: map[string]any{"x": 1}}}
	first, err := Signature(request, items, "engine-1")
	if err != nil {
		t.Fatal(err)
	}
	second, _ := Signature(request, items, "engine-1")
	if first != second {
		t.Fatalf("signature is unstable: %s != %s", first, second)
	}
	request.MinimumTradeCount++
	changed, _ := Signature(request, items, "engine-1")
	if first == changed {
		t.Fatal("tier-affecting field did not change signature")
	}
}

func (f *fakePython) Algorithms(context.Context, string, string) ([]pythonclient.AlgorithmDefinition, error) {
	return f.definitions, nil
}

func (f *fakePython) Health(context.Context) pythonclient.Health {
	return pythonclient.Health{ContractVersion: "1.0.0"}
}

func (f *fakePython) Submit(_ context.Context, kind, _, _ string, payload any) (pythonclient.JobStatus, error) {
	value := payload.(map[string]any)
	comparisonID := value["comparison_id"].(string)
	ref := value["output_path"].(string)
	directory, _ := f.guard.Resolve(ref)
	if err := os.MkdirAll(directory, 0o750); err != nil {
		return pythonclient.JobStatus{}, err
	}
	prepared := value["strategies"].([]preparedItem)
	results := make([]Result, 0, len(prepared))
	for _, item := range prepared {
		runRef := filepath.ToSlash(filepath.Join("runs", item.RunID))
		runDirectory, _ := f.guard.Resolve(runRef)
		if err := os.MkdirAll(runDirectory, 0o750); err != nil {
			return pythonclient.JobStatus{}, err
		}
		if err := os.WriteFile(filepath.Join(runDirectory, "_SUCCESS"), nil, 0o640); err != nil {
			return pythonclient.JobStatus{}, err
		}
		results = append(results, Result{AlgorithmID: item.Strategy.AlgorithmID, Name: item.Name, StrategyFamily: item.StrategyFamily, Parameters: item.Parameters, Status: "completed", RunID: item.RunID, RunSignature: item.RunSignature})
	}
	manifest := map[string]any{
		"schema_version": 1, "comparison_id": comparisonID, "trace_id": "trace-1",
		"dataset": map[string]any{"dataset_id": "TEST.5m", "data_revision": "sha256:" + repeat("1", 64)},
		"range":   value["range"], "execution": value["execution"], "capital": value["capital"],
		"random_seed": int64(7), "minimum_trade_count": 20, "strategy_count": len(prepared),
		"completed_count": len(prepared), "failed_count": 0, "created_at": "2026-08-22T00:00:00Z",
	}
	for name, document := range map[string]any{"comparison.json": manifest, "results.json": results} {
		data, _ := json.Marshal(document)
		if err := os.WriteFile(filepath.Join(directory, name), data, 0o640); err != nil {
			return pythonclient.JobStatus{}, err
		}
	}
	if err := os.WriteFile(filepath.Join(directory, "_SUCCESS"), nil, 0o640); err != nil {
		return pythonclient.JobStatus{}, err
	}
	return pythonclient.JobStatus{JobID: comparisonID, Status: "queued"}, nil
}

func (f *fakePython) Job(context.Context, string, string, string) (pythonclient.JobStatus, error) {
	return pythonclient.JobStatus{Status: "completed", Progress: 1, ProgressDetail: map[string]any{"total_count": 1.0, "completed_count": 1.0, "failed_count": 0.0}}, nil
}

func (f *fakePython) Cancel(context.Context, string, string, string) error { return nil }

func TestComparisonCompletesAndRegistersFormalChildRuns(t *testing.T) {
	guard, err := storage.NewPathGuard(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	manager := jobs.NewManager()
	definition := pythonclient.AlgorithmDefinition{
		AlgorithmRef: pythonclient.AlgorithmRef{Kind: "strategy", AlgorithmID: "formal", AlgorithmVersion: "1.0.0", SourceHash: "sha256:" + repeat("2", 64)},
		Name:         "Formal", ComparisonEligible: true, ResearchRole: "formal_strategy", StrategyFamily: "test",
		ParameterSchema: map[string]any{"type": "object", "additionalProperties": false, "properties": map[string]any{"checkpoint_interval": map[string]any{"type": "integer", "default": 1024.0}}, "required": []any{"checkpoint_interval"}},
	}
	python := &fakePython{guard: guard, definitions: []pythonclient.AlgorithmDefinition{definition}}
	revision := "sha256:" + repeat("1", 64)
	meta := catalog.DatasetMeta{DatasetID: "TEST.5m", DataRevision: revision, Coverage: catalog.CoverageMeta{FirstBarIndex: 0, LastBarIndex: 100}, Files: []catalog.FileMeta{{Role: "bars", Path: "normalized/TEST/bars.parquet"}}}
	service := NewService(guard, fakeCatalog{meta}, python, manager, "1.0.0", time.Millisecond)
	request := Request{
		DatasetID: "TEST.5m", DataRevision: revision,
		Strategies: []Item{{Strategy: definition.AlgorithmRef, Parameters: map[string]any{}}},
		Range:      backtest.Range{WarmupFromBarIndex: 0, FromBarIndex: 0, ToBarIndex: 100},
		Execution:  map[string]any{"signal_timing": "bar_close", "fill_timing": "next_bar_open", "commission": map[string]any{"mode": "fixed_per_contract"}, "slippage": map[string]any{"mode": "ticks", "value": 0}},
		Capital:    map[string]any{"initial_cash_i64": 1000, "currency": "CNY", "money_scale": 100},
		RandomSeed: 7, MinimumTradeCount: 20,
	}
	submission, err := service.Submit(context.Background(), "request-1", "trace-1", request)
	if err != nil {
		t.Fatal(err)
	}
	deadline := time.Now().Add(time.Second)
	for time.Now().Before(deadline) {
		job, _, _, _ := service.Status(submission.ComparisonID)
		if job.Status == jobs.Completed {
			break
		}
		time.Sleep(time.Millisecond)
	}
	results, err := service.Results(submission.ComparisonID)
	if err != nil || len(results) != 1 {
		t.Fatalf("unexpected comparison results: %#v, %v", results, err)
	}
	child, ok := manager.Get(results[0].RunID)
	if !ok || child.Status != jobs.Completed || child.Kind != "backtest" {
		t.Fatalf("completed child run was not registered: %#v", child)
	}
	items, err := service.List("TEST.5m")
	if err != nil || len(items) != 1 {
		t.Fatalf("completed comparison was not restorable: %#v, %v", items, err)
	}
}

func repeat(value string, count int) string {
	result := ""
	for range count {
		result += value
	}
	return result
}
