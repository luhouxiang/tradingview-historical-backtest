from __future__ import annotations

import pytest

from tvbt.backtest import _validated_execution


def execution() -> dict[str, object]:
    return {
        "semantic_version": "1.0.0",
        "signal_timing": "bar_close",
        "fill_timing": "next_bar_open",
        "commission": {"mode": "fixed_per_contract", "amount_i64": 300, "money_scale": 100},
        "slippage": {"mode": "ticks", "value": 1},
        "contract_multiplier": 20,
        "contract_multiplier_source": "instrument_config",
        "margin_ratio": 0.12,
        "intrabar_conflict_rule": "worst_case",
        "stress_scenario_id": "baseline",
        "cost_multiplier": 1.0,
        "additional_slippage_ticks": 0.0,
        "additional_delay_bars": 0,
        "fill_mode": "unlimited",
    }


def test_python_accepts_only_go_resolved_execution_semantics() -> None:
    value = execution()
    assert _validated_execution(value, 100)["contract_multiplier"] == 20

    value.pop("semantic_version")
    with pytest.raises(ValueError, match="incomplete"):
        _validated_execution(value, 100)

    value = execution()
    value["semantic_version"] = "2.0.0"
    with pytest.raises(ValueError, match="unsupported"):
        _validated_execution(value, 100)

    value = execution()
    value["contract_multiplier_source"] = "browser"
    with pytest.raises(ValueError, match="authoritative"):
        _validated_execution(value, 100)
