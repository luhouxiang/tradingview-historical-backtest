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
		Execution:      map[string]any{"signal_timing": "bar_close", "fill_timing": "next_bar_open"},
		Capital:        map[string]any{"initial_cash_i64": int64(1000), "currency": "CNY", "money_scale": int64(100)},
	}
	first, err := studyKey(request, "engine-1")
	if err != nil {
		t.Fatal(err)
	}
	second, _ := studyKey(request, "engine-1")
	if first != second || len(first) != 71 {
		t.Fatalf("study key is not reproducible: %q %q", first, second)
	}
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

func repeat(value string, count int) string {
	result := ""
	for range count {
		result += value
	}
	return result
}
