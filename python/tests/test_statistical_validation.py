from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest

from tvbt.statistical_validation import (
    _numeric_neighbors,
    attempted_parameter_combinations,
    candidate_multiple_comparisons,
    certification,
    holm_adjust,
    moving_block_bootstrap,
    run_parameter_neighborhood,
)
from tvbt.storage.path_guard import PathGuard


def test_moving_block_bootstrap_is_seeded_and_uses_daily_blocks() -> None:
    values = [0.01] * 20
    first = moving_block_bootstrap(
        values, block_size=5, iterations=200, confidence_level=0.95, random_seed=7
    )
    second = moving_block_bootstrap(
        values, block_size=5, iterations=200, confidence_level=0.95, random_seed=7
    )
    assert first == second
    assert first["method"] == "moving_block_bootstrap"
    assert first["metrics"]["mean_daily_return"]["lower"] == pytest.approx(0.01)
    assert first["metrics"]["total_return"]["lower"] > 0


def test_moving_block_bootstrap_reports_insufficient_sample_reason() -> None:
    result = moving_block_bootstrap(
        [0.01, -0.01],
        block_size=5,
        iterations=2000,
        confidence_level=0.95,
        random_seed=7,
    )
    metric = result["metrics"]["mean_daily_return"]
    assert metric["lower"] is None
    assert metric["reason"] == "insufficient_oos_daily_returns_for_block_size"


def test_holm_adjustment_is_monotonic_and_warns_for_multiple_candidates() -> None:
    comparisons = [
        {"raw_p_value": 0.01},
        {"raw_p_value": 0.03},
        {"raw_p_value": 0.04},
    ]
    adjusted = holm_adjust(comparisons, 0.05)
    assert [item["holm_adjusted_p_value"] for item in adjusted] == pytest.approx([0.03, 0.06, 0.06])
    result = candidate_multiple_comparisons({"a": [0.01, 0.02], "b": [-0.01, -0.02]}, 0.05)
    assert result["multiple_comparison_warning"] is True
    assert result["comparison_count"] == 1


def test_attempted_combinations_include_all_training_candidates() -> None:
    results = [
        {
            "folds": [
                {
                    "training_ranking": [
                        {"parameters": {"period": 10}, "train_metrics": {}},
                        {"parameters": {"period": 20}},
                    ]
                },
                {"training_ranking": [{"parameters": {"period": 10}, "train_metrics": {}}]},
            ]
        }
    ]
    combinations = attempted_parameter_combinations(results)
    by_period = {item["parameters"]["period"]: item for item in combinations}
    assert by_period[10]["attempt_count"] == 2
    assert by_period[10]["completed_count"] == 2
    assert by_period[20]["attempt_count"] == 1
    assert by_period[20]["completed_count"] == 0


def test_numeric_neighbors_use_only_adjacent_declared_levels() -> None:
    neighbors = _numeric_neighbors(
        [
            {"name": "period", "type": "integer", "candidates": [5, 10, 20, 50]},
            {"name": "enabled", "type": "boolean", "candidates": [True, False]},
        ],
        {"period": 20, "enabled": True},
    )
    assert neighbors == [
        {"parameter_name": "period", "direction": "lower", "value": 10},
        {"parameter_name": "period", "direction": "upper", "value": 50},
    ]


def test_parameter_neighborhood_is_independence_group_equal_weighted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    outcomes = iter([True, False, True])

    def fake_run(*args: Any, **kwargs: Any) -> tuple[dict[str, Any], list[Any], str]:
        passed = next(outcomes)
        return (
            {
                "total_return": 0.1 if passed else -0.1,
                "expectancy_i64": 1 if passed else -1,
                "max_drawdown": 0.1,
            },
            [],
            "sha256:" + "1" * 64,
        )

    monkeypatch.setattr("tvbt.statistical_validation._run", fake_run)
    payload = {
        "research_study_id": "research-test",
        "walk_forward": {
            "search_space": [{"name": "period", "type": "integer", "candidates": [10, 20]}]
        },
        "datasets": [
            {"dataset_id": "AO", "independence_group": "SHFE.AO"},
            {"dataset_id": "AOL", "independence_group": "SHFE.AO"},
            {"dataset_id": "Y", "independence_group": "DCE.Y"},
        ],
    }
    results = [
        {
            "dataset_id": dataset["dataset_id"],
            "folds": [
                {
                    "fold_index": 0,
                    "status": "completed",
                    "selected_parameters": {"period": 10},
                    "validation_range": {},
                }
            ],
        }
        for dataset in payload["datasets"]
    ]
    progress_updates: list[tuple[float, dict[str, Any]]] = []
    summary, details, children = run_parameter_neighborhood(
        payload,
        results,
        PathGuard(tmp_path),
        threading.Event(),
        lambda value, detail: progress_updates.append((value, detail)),
    )
    # AO group first averages 1 and 0 to 0.5; DCE.Y contributes 1.0.
    assert summary["pass_rate"] == pytest.approx(0.75)
    assert summary["passed"] is True
    assert len(details) == len(children) == 3
    assert progress_updates[-1][0] == 1
    assert progress_updates[-1][1]["stage"] == "parameter_neighborhood"
    assert progress_updates[-1][1]["completed_count"] == 3
    assert progress_updates[-1][1]["total_count"] == 3


def test_certification_applies_all_research_and_reliable_gates() -> None:
    aggregate = {
        "minimum_completed_folds_per_group": 4,
        "certification_trade_count": 220,
        "total_return": 0.1,
        "out_of_sample_expectancy_i64": 10,
        "max_drawdown": 0.1,
        "eligible_independence_group_count": 3,
        "minimum_studied_trading_days_per_group": 504,
        "profitable_fold_ratio": 0.7,
        "stress_scenarios": [
            {"scenario_id": "cost_2x", "total_return": 0.01},
            {"scenario_id": "delay_1_bar", "total_return": 0.02},
        ],
    }
    statistical = {
        "bootstrap": {"metrics": {"mean_daily_return": {"lower": 0.0001}}},
        "parameter_neighborhood": {"pass_rate": 0.6},
    }
    result = certification(aggregate, statistical)
    assert result["tier"] == "reliable_candidate"
    assert result["reliable_candidate_is_historical_only"] is True
    aggregate["eligible_independence_group_count"] = 2
    downgraded = certification(aggregate, statistical)
    assert downgraded["tier"] == "research_candidate"
    assert "minimum_eligible_independence_groups_not_met" in downgraded["reasons"]
