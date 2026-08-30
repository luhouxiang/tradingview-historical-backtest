package backtest

import (
	"encoding/json"
	"errors"
	"math"
	"strings"
)

const ExecutionSemanticVersion = "1.0.0"

var ErrInvalidExecution = errors.New("invalid execution configuration")

var executionKeys = map[string]bool{
	"semantic_version": true, "signal_timing": true, "fill_timing": true,
	"commission": true, "slippage": true, "contract_multiplier": true,
	"contract_multiplier_source": true, "margin_ratio": true,
	"intrabar_conflict_rule": true, "stress_scenario_id": true,
	"cost_multiplier": true, "additional_slippage_ticks": true,
	"additional_delay_bars": true, "max_volume_participation_rate": true,
	"fill_mode": true,
}

func NormalizeExecution(value, capital map[string]any, contractMultiplier int64) (map[string]any, error) {
	if contractMultiplier < 1 || value["semantic_version"] != ExecutionSemanticVersion || value["signal_timing"] != "bar_close" {
		return nil, ErrInvalidExecution
	}
	for key := range value {
		if !executionKeys[key] {
			return nil, ErrInvalidExecution
		}
	}
	fillTiming, _ := value["fill_timing"].(string)
	if fillTiming != "next_bar_open" && fillTiming != "bar_close" {
		return nil, ErrInvalidExecution
	}
	conflictRule, _ := value["intrabar_conflict_rule"].(string)
	if conflictRule != "stop_first" && conflictRule != "target_first" && conflictRule != "worst_case" {
		return nil, ErrInvalidExecution
	}
	if source, exists := value["contract_multiplier_source"]; exists && source != "instrument_config" {
		return nil, ErrInvalidExecution
	}
	if supplied, exists := value["contract_multiplier"]; exists {
		number, ok := executionNumber(supplied)
		if !ok || number != float64(contractMultiplier) {
			return nil, ErrInvalidExecution
		}
	}
	marginRatio, ok := executionNumber(value["margin_ratio"])
	if !ok || marginRatio <= 0 || marginRatio > 1 {
		return nil, ErrInvalidExecution
	}
	moneyScale, ok := executionInteger(capital["money_scale"])
	if !ok || moneyScale < 1 {
		return nil, ErrInvalidExecution
	}
	commission, ok := normalizeCommission(value["commission"], moneyScale)
	if !ok {
		return nil, ErrInvalidExecution
	}
	slippage, ok := normalizeSlippage(value["slippage"])
	if !ok {
		return nil, ErrInvalidExecution
	}
	costMultiplier := 1.0
	if raw, exists := value["cost_multiplier"]; exists {
		costMultiplier, ok = executionNumber(raw)
		if !ok || costMultiplier < 0 {
			return nil, ErrInvalidExecution
		}
	}
	additionalSlippage := 0.0
	if raw, exists := value["additional_slippage_ticks"]; exists {
		additionalSlippage, ok = executionNumber(raw)
		if !ok || additionalSlippage < 0 {
			return nil, ErrInvalidExecution
		}
	}
	additionalDelay := int64(0)
	if raw, exists := value["additional_delay_bars"]; exists {
		additionalDelay, ok = executionInteger(raw)
		if !ok || additionalDelay < 0 {
			return nil, ErrInvalidExecution
		}
	}
	fillMode := "unlimited"
	if raw, exists := value["fill_mode"]; exists {
		fillMode, ok = raw.(string)
		if !ok || fillMode != "unlimited" && fillMode != "volume_cap_ioc" {
			return nil, ErrInvalidExecution
		}
	}
	result := map[string]any{
		"semantic_version": ExecutionSemanticVersion, "signal_timing": "bar_close",
		"fill_timing": fillTiming, "commission": commission, "slippage": slippage,
		"contract_multiplier": contractMultiplier, "contract_multiplier_source": "instrument_config",
		"margin_ratio": marginRatio, "intrabar_conflict_rule": conflictRule,
		"stress_scenario_id": "baseline", "cost_multiplier": costMultiplier,
		"additional_slippage_ticks": additionalSlippage, "additional_delay_bars": additionalDelay,
		"fill_mode": fillMode,
	}
	if raw, exists := value["stress_scenario_id"]; exists {
		text, valid := raw.(string)
		if !valid || strings.TrimSpace(text) == "" {
			return nil, ErrInvalidExecution
		}
		result["stress_scenario_id"] = text
	}
	if fillMode == "volume_cap_ioc" {
		rate, valid := executionNumber(value["max_volume_participation_rate"])
		if !valid || rate <= 0 || rate > 1 {
			return nil, ErrInvalidExecution
		}
		result["max_volume_participation_rate"] = rate
	} else if _, exists := value["max_volume_participation_rate"]; exists {
		return nil, ErrInvalidExecution
	}
	return result, nil
}

func NormalizeCapital(value map[string]any) (map[string]any, error) {
	if len(value) != 3 {
		return nil, ErrInvalidExecution
	}
	cash, cashOK := executionInteger(value["initial_cash_i64"])
	scale, scaleOK := executionInteger(value["money_scale"])
	currency, currencyOK := value["currency"].(string)
	currency = strings.ToUpper(strings.TrimSpace(currency))
	if !cashOK || cash < 0 || !scaleOK || scale < 1 || !currencyOK || len(currency) != 3 {
		return nil, ErrInvalidExecution
	}
	return map[string]any{"initial_cash_i64": cash, "currency": currency, "money_scale": scale}, nil
}

func normalizeCommission(raw any, moneyScale int64) (map[string]any, bool) {
	value, ok := raw.(map[string]any)
	if !ok {
		return nil, false
	}
	mode, _ := value["mode"].(string)
	switch mode {
	case "fixed_per_contract":
		if len(value) != 3 {
			return nil, false
		}
		amount, amountOK := executionInteger(value["amount_i64"])
		scale, scaleOK := executionInteger(value["money_scale"])
		if !amountOK || amount < 0 || !scaleOK || scale != moneyScale {
			return nil, false
		}
		return map[string]any{"mode": mode, "amount_i64": amount, "money_scale": scale}, true
	case "proportional":
		if len(value) != 2 {
			return nil, false
		}
		rate, valid := executionNumber(value["rate"])
		if !valid || rate < 0 || rate > 1 {
			return nil, false
		}
		return map[string]any{"mode": mode, "rate": rate}, true
	default:
		return nil, false
	}
}

func normalizeSlippage(raw any) (map[string]any, bool) {
	value, ok := raw.(map[string]any)
	if !ok || len(value) != 2 {
		return nil, false
	}
	mode, _ := value["mode"].(string)
	number, valid := executionNumber(value["value"])
	if (mode != "ticks" && mode != "bps") || !valid || number < 0 {
		return nil, false
	}
	return map[string]any{"mode": mode, "value": number}, true
}

func executionNumber(value any) (float64, bool) {
	switch number := value.(type) {
	case json.Number:
		result, err := number.Float64()
		return result, err == nil && !math.IsNaN(result) && !math.IsInf(result, 0)
	case float64:
		return number, !math.IsNaN(number) && !math.IsInf(number, 0)
	case float32:
		return float64(number), !math.IsNaN(float64(number)) && !math.IsInf(float64(number), 0)
	case int:
		return float64(number), true
	case int64:
		return float64(number), true
	case int32:
		return float64(number), true
	default:
		return 0, false
	}
}

func executionInteger(value any) (int64, bool) {
	number, ok := executionNumber(value)
	if !ok || number != math.Trunc(number) || number > math.MaxInt64 || number < math.MinInt64 {
		return 0, false
	}
	return int64(number), true
}
