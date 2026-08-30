package optimization

import (
	"encoding/json"
	"testing"

	"github.com/tvbt/tradingview-historical-backtest/internal/backtest"
	"github.com/tvbt/tradingview-historical-backtest/internal/pythonclient"
)

func TestSearchValuesAndStudyKeyAreDeterministic(t *testing.T) {
	minimum, maximum, step := 10.0, 30.0, 10.0
	values, err := searchValues(SearchParameter{Name: "period", Type: "integer", Minimum: &minimum, Maximum: &maximum, Step: &step})
	if err != nil || len(values) != 3 || values[0] != int64(10) || values[2] != int64(30) {
		t.Fatalf("unexpected search values: %#v, %v", values, err)
	}
	request := Request{
		DataRevision:   "sha256:" + repeat("1", 64),
		Strategy:       pythonclient.AlgorithmRef{Kind: "strategy", AlgorithmID: "example", AlgorithmVersion: "1", SourceHash: "sha256:" + repeat("2", 64)},
		BaseParameters: map[string]any{"period": int64(20)},
		SearchSpace:    []SearchParameter{{Name: "period", Type: "integer", Candidates: []any{json.Number("10"), json.Number("20")}}},
		Objectives:     []Objective{{Metric: "total_return", Direction: "maximize"}},
		Search:         SearchConfig{Method: "random", Budget: 2, RandomSeed: 7},
		Ranges:         Ranges{Train: backtest.Range{FromBarIndex: 10, ToBarIndex: 20}, Validation: backtest.Range{FromBarIndex: 21, ToBarIndex: 30}},
		Execution: map[string]any{
			"semantic_version": backtest.ExecutionSemanticVersion, "signal_timing": "bar_close", "fill_timing": "next_bar_open",
			"commission": map[string]any{"mode": "fixed_per_contract", "amount_i64": 300, "money_scale": 100},
			"slippage":   map[string]any{"mode": "ticks", "value": 1}, "margin_ratio": .12, "intrabar_conflict_rule": "worst_case",
		},
		Capital: map[string]any{"initial_cash_i64": int64(1000), "currency": "CNY", "money_scale": int64(100)},
	}
	first, err := studyKey(request, "engine-1")
	if err != nil {
		t.Fatal(err)
	}
	second, _ := studyKey(request, "engine-1")
	if first != second || len(first) != 71 {
		t.Fatalf("study key is not reproducible: %q %q", first, second)
	}
	request.RiskOverlay = &backtest.RiskOverlay{
		Algorithm: pythonclient.AlgorithmRef{
			Kind: "risk_filter", AlgorithmID: "unified_risk_execution_overlay",
			AlgorithmVersion: "1.0.0", SourceHash: "sha256:" + repeat("3", 64),
		},
		Parameters: map[string]any{"leverage_allowed": false},
		Context: backtest.RiskContext{
			MarketStateRevision: "sha256:" + repeat("4", 64), SectorID: "metals",
		},
	}
	riskChanged, _ := studyKey(request, "engine-1")
	if riskChanged == first {
		t.Fatal("risk overlay did not change study key")
	}
	request.RiskOverlay = nil
	request.Search.RandomSeed++
	changed, _ := studyKey(request, "engine-1")
	if changed == first {
		t.Fatal("random seed did not change study key")
	}
}

func TestStudyValidationRejectsOverlappingRangesAndOversizedBudget(t *testing.T) {
	if validRange(backtest.Range{WarmupFromBarIndex: 0, FromBarIndex: 5, ToBarIndex: 11}, 10) {
		t.Fatal("range beyond dataset was accepted")
	}
	request := Request{Search: SearchConfig{Method: "grid", Budget: 101}}
	if err := validateStudy(request, map[string]any{}); err == nil {
		t.Fatal("oversized budget was accepted")
	}
}

func TestWalkForwardValidationAllowsOneFixedBaseCandidate(t *testing.T) {
	err := ValidateWalkForwardSearchConfiguration(
		map[string]any{}, nil,
		[]Objective{{Metric: "total_return", Direction: "maximize"}}, nil,
		SearchConfig{Method: "grid", Budget: 1, RandomSeed: 7},
		map[string]any{"type": "object", "additionalProperties": false, "properties": map[string]any{}},
	)
	if err != nil {
		t.Fatalf("fixed-parameter walk-forward configuration was rejected: %v", err)
	}
}

func repeat(value string, count int) string {
	result := ""
	for range count {
		result += value
	}
	return result
}
