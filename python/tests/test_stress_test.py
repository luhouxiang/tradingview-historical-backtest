from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest

from tvbt.backtest import _volume_limited_quantity
from tvbt.storage.path_guard import PathGuard
from tvbt.stress_test import run_stress_suite


def test_volume_participation_caps_partial_and_zero_volume_fills() -> None:
    assert _volume_limited_quantity(20, 100, 0.1) == 10
    assert _volume_limited_quantity(20, 0, 0.1) == 0


def test_stress_suite_freezes_fold_parameters_and_aggregates_fill_rate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[dict[str, Any]] = []

    def fake_run(
        payload: dict[str, Any],
        dataset: dict[str, Any],
        parameters: dict[str, Any],
        range_value: dict[str, Any],
        run_id: str,
        guard: PathGuard,
        cancelled: threading.Event,
        execution: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
        del payload, range_value, guard, cancelled
        assert execution is not None
        calls.append({"parameters": parameters, "execution": execution, "run_id": run_id})
        requested = 10
        filled = 1 if execution["fill_mode"] == "volume_cap_ioc" else 10
        returned = 0.01 - 0.004 * float(execution["cost_multiplier"] - 1)
        return (
            {
                "total_return": returned,
                "max_drawdown": 0.02,
                "trade_count": 2,
                "requested_quantity": requested,
                "filled_quantity": filled,
            },
            [{"trading_day": "2026-01-01", "daily_return": returned}],
            "sha256:" + "1" * 64,
        )

    monkeypatch.setattr("tvbt.stress_test._run", fake_run)
    payload = {
        "research_study_id": "research-1",
        "execution": {"fill_timing": "next_bar_open"},
        "stress_test": {"suite_version": "1.0.0", "volume_participation_rate": 0.1},
        "datasets": [{"dataset_id": "AO", "independence_group": "SHFE.AO"}],
    }
    results = [
        {
            "dataset_id": "AO",
            "folds": [
                {
                    "fold_index": 0,
                    "status": "completed",
                    "selected_parameters": {"threshold": 7},
                    "validation_range": {},
                }
            ],
        }
    ]
    aggregates, details, children = run_stress_suite(
        payload, results, PathGuard(tmp_path), threading.Event()
    )
    assert len(aggregates) == len(details) == len(children) == 7
    assert all(call["parameters"] == {"threshold": 7} for call in calls)
    assert aggregates[2]["scenario_id"] == "cost_2x"
    assert aggregates[-1]["fill_rate"] == pytest.approx(0.1)
    assert aggregates[-1]["fill_rate_degradation"] == pytest.approx(0.9)
    assert calls[-1]["execution"]["max_volume_participation_rate"] == pytest.approx(0.1)
    assert children[-1]["role"] == "stress"
