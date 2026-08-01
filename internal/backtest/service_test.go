package backtest

import (
	"testing"

	"github.com/tvbt/tradingview-historical-backtest/internal/pythonclient"
)

func TestRunSignatureIsReproducibleAndCoversExecutionFacts(t *testing.T) {
	request := Request{
		DataRevision: "sha256:" + repeat("1", 64),
		Strategy:     pythonclient.AlgorithmRef{Kind: "strategy", AlgorithmID: "example", AlgorithmVersion: "1", SourceHash: "sha256:" + repeat("2", 64)},
		Parameters:   map[string]any{"period": int64(20)},
		Range:        Range{WarmupFromBarIndex: 0, FromBarIndex: 100, ToBarIndex: 200},
		Execution:    map[string]any{"signal_timing": "bar_close", "fill_timing": "next_bar_open", "commission": map[string]any{"mode": "fixed_per_contract", "amount_i64": 3}, "slippage": map[string]any{"mode": "ticks", "value": 1}, "contract_multiplier": 20, "margin_ratio": .1},
		Capital:      map[string]any{"initial_cash_i64": int64(1000), "currency": "CNY", "money_scale": int64(100)}, RandomSeed: 7,
	}
	base, err := Signature(request, "engine-1")
	if err != nil {
		t.Fatal(err)
	}
	equal, _ := Signature(request, "engine-1")
	if base != equal {
		t.Fatal("same facts produced different run signatures")
	}
	request.Execution["fill_timing"] = "bar_close"
	changed, _ := Signature(request, "engine-1")
	if changed == base {
		t.Fatal("execution timing did not change run signature")
	}
}

func repeat(value string, count int) string {
	result := ""
	for range count {
		result += value
	}
	return result
}
