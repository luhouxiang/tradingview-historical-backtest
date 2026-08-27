from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from tvbt.research import _aggregate, _aggregate_walk_forward, run_research_study
from tvbt.storage.path_guard import PathGuard


def _result(dataset_id: str, group: str, returns: list[float]) -> dict[str, Any]:
    return {
        "dataset_id": dataset_id,
        "data_revision": "sha256:" + "1" * 64,
        "independence_group": group,
        "trading_day_count": 600,
        "status": "completed",
        "summary": {"total_return": sum(returns), "max_drawdown": 0.1, "trade_count": 5},
        "daily_returns": [
            {"trading_day": f"2026-01-{index + 1:02d}", "daily_return": value}
            for index, value in enumerate(returns)
        ],
    }


def test_aggregate_deduplicates_correlated_datasets_before_group_weighting() -> None:
    aggregate = _aggregate(
        [
            _result("AO2609", "SHFE.AO", [0.10, 0.10]),
            _result("AOL9", "SHFE.AO", [-0.10, -0.10]),
            _result("I2609", "DCE.I", [0.20, 0.20]),
        ]
    )
    # AO2609 and AOL9 first net to zero as one AO group. AO and I are then
    # equally weighted, producing 10% per day rather than 6.67%.
    assert aggregate["total_return"] == pytest.approx(0.21)
    assert aggregate["independence_group_count"] == 2
    assert aggregate["eligible_independence_group_count"] == 0
    assert aggregate["data_status"] == "exploratory"


def test_walk_forward_fold_and_parameter_stability_are_group_equal_weighted() -> None:
    def item(dataset_id: str, group: str, returned: float, stability: float) -> dict[str, Any]:
        return {
            "dataset_id": dataset_id,
            "independence_group": group,
            "status": "completed",
            "oos_daily_returns": [{"trading_day": "2026-01-01", "daily_return": returned}],
            "walk_forward_summary": {
                "total_return": returned,
                "max_drawdown": 0.1,
                "parameter_stability": stability,
            },
            "folds": [
                {
                    "status": "completed",
                    "validation_metrics": {
                        "total_return": returned,
                        "max_drawdown": 0.1,
                        "trade_count": 1,
                    },
                }
            ],
        }

    aggregate = _aggregate_walk_forward(
        [
            item("AO2609", "SHFE.AO", 0.1, 0.2),
            item("AOL9", "SHFE.AO", -0.1, 0.8),
            item("I2609", "DCE.I", 0.2, 1.0),
        ]
    )
    assert aggregate["profitable_fold_ratio"] == pytest.approx(0.75)
    assert aggregate["parameter_stability"] == pytest.approx(0.75)


def test_walk_forward_data_gate_uses_studied_days_not_only_oos_days() -> None:
    days = [f"day-{index:03d}" for index in range(504)]
    results = []
    for index, group in enumerate(("SHFE.AO", "DCE.I", "CZCE.MA")):
        results.append(
            {
                "dataset_id": f"dataset-{index}",
                "independence_group": group,
                "status": "completed",
                "study_trading_days": days,
                "oos_daily_returns": [{"trading_day": "day-252", "daily_return": 0.01}],
                "walk_forward_summary": {"total_return": 0.01, "max_drawdown": 0.0},
                "folds": [],
            }
        )
    aggregate = _aggregate_walk_forward(results)
    assert aggregate["daily_return_count"] == 1
    assert aggregate["eligible_independence_group_count"] == 3
    assert aggregate["data_status"] == "certification_ready"


def test_walk_forward_certification_counts_correlated_datasets_once() -> None:
    def item(dataset_id: str, group: str, trade_count: int, expectancy: float) -> dict[str, Any]:
        fold = {
            "status": "completed",
            "validation_metrics": {
                "trade_count": trade_count,
                "expectancy_i64": expectancy,
                "total_return": 0.01,
                "max_drawdown": 0.01,
            },
        }
        return {
            "dataset_id": dataset_id,
            "independence_group": group,
            "status": "completed",
            "study_trading_days": [f"day-{index}" for index in range(504)],
            "oos_daily_returns": [{"trading_day": "day-503", "daily_return": 0.01}],
            "walk_forward_summary": {"total_return": 0.01, "max_drawdown": 0.01},
            "folds": [fold, fold, fold, fold],
        }

    aggregate = _aggregate_walk_forward(
        [
            item("AO", "SHFE.AO", 100, 10),
            item("AOL", "SHFE.AO", 200, 20),
            item("Y", "DCE.Y", 50, 30),
        ]
    )
    # SHFE.AO contributes the average of its correlated datasets: 600 trades.
    # DCE.Y contributes 200, for 800 group-equivalent trades rather than 1,400 raw.
    assert aggregate["out_of_sample_trade_count"] == 1400
    assert aggregate["certification_trade_count"] == pytest.approx(800)
    assert aggregate["out_of_sample_expectancy_i64"] == pytest.approx(20)
    assert aggregate["minimum_completed_folds_per_group"] == 4


def test_research_study_is_versioned_resumable_and_immutable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    guard = PathGuard(tmp_path)

    def fake_backtest(child: dict[str, Any], guard_value: PathGuard, _: threading.Event) -> str:
        output = guard_value.resolve(str(child["output_path"]))
        output.mkdir(parents=True)
        (output / "summary.json").write_text(
            json.dumps({"total_return": 0.1, "max_drawdown": 0.02, "trade_count": 3}),
            encoding="utf-8",
        )
        pq.write_table(
            pa.table(
                {
                    "trading_day": pa.array(["2026-01-01", "2026-01-02"]),
                    "daily_return": pa.array([0.01, 0.02], type=pa.float64()),
                }
            ),
            output / "daily_returns.parquet",
        )
        (output / "_SUCCESS").write_bytes(b"")
        return str(child["output_path"])

    monkeypatch.setattr("tvbt.research.run_backtest", fake_backtest)
    revision = "sha256:" + "1" * 64
    datasets = []
    for index, group in enumerate(("SHFE.AO", "DCE.I")):
        datasets.append(
            {
                "dataset_id": f"TEST{index}.5m",
                "data_revision": revision,
                "timeframe": "5m",
                "independence_group": group,
                "trading_day_count": 600,
                "bars_path": f"normalized/{index}/bars.parquet",
                "meta_path": f"normalized/{index}/meta.json",
                "range": {"warmup_from_bar_index": 0, "from_bar_index": 0, "to_bar_index": 10},
                "run_id": f"run-{index}",
                "run_signature": "sha256:" + f"{index + 2:064x}",
            }
        )
    payload = {
        "contract_version": "1.0.0",
        "request_id": "request-1",
        "trace_id": "trace-1",
        "job_id": "research-1",
        "research_study_id": "research-1",
        "study_signature": "sha256:" + "a" * 64,
        "datasets": datasets,
        "strategy": {"kind": "strategy", "algorithm_id": "formal"},
        "parameters": {},
        "execution": {},
        "capital": {},
        "random_seed": 7,
        "output_path": "research-studies/research-1",
    }
    assert run_research_study(payload, guard, threading.Event()) == "research-studies/research-1"
    output = guard.resolve("research-studies/research-1")
    manifest = json.loads((output / "research-study.json").read_text(encoding="utf-8"))
    assert manifest["datasets"][0]["data_revision"] == revision
    assert manifest["datasets"][0]["run_id"] == "run-0"
    assert manifest["study_mode"] == "fixed_parameters"
    assert len(manifest["child_runs"]) == 2
    assert manifest["aggregate"]["independence_group_count"] == 2
    assert not guard.resolve("research-studies/research-1.journal.json").exists()
    with pytest.raises(ValueError, match="already exists"):
        run_research_study(payload, guard, threading.Event())

    single_payload = {
        **payload,
        "job_id": "research-single",
        "research_study_id": "research-single",
        "study_signature": "sha256:" + "b" * 64,
        "datasets": datasets[:1],
        "output_path": "research-studies/research-single",
    }
    single_output = guard.resolve(run_research_study(single_payload, guard, threading.Event()))
    single_manifest = json.loads(
        (single_output / "research-study.json").read_text(encoding="utf-8")
    )
    assert len(single_manifest["datasets"]) == 1
    assert single_manifest["aggregate"]["independence_group_count"] == 1
    assert single_manifest["aggregate"]["data_status"] == "exploratory"


def test_research_rejects_mixed_timeframes(tmp_path: Path) -> None:
    payload = {
        "strategy": {},
        "parameters": {},
        "output_path": "research-studies/bad",
        "datasets": [
            {"dataset_id": "a", "data_revision": "r1", "timeframe": "5m", "range": {}},
            {"dataset_id": "b", "data_revision": "r2", "timeframe": "15m", "range": {}},
        ],
    }
    with pytest.raises(ValueError, match="one timeframe"):
        run_research_study(payload, PathGuard(tmp_path), threading.Event())


def test_walk_forward_research_persists_oos_daily_artifact_and_child_runs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    guard = PathGuard(tmp_path)

    def fake_walk(
        payload: dict[str, Any],
        dataset: dict[str, Any],
        dataset_index: int,
        guard_value: PathGuard,
        cancelled: threading.Event,
        progress: Any,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        del payload, guard_value, cancelled
        progress(1.0)
        run_id = f"run-wf-{dataset_index}"
        signature = "sha256:" + f"{dataset_index + 3:064x}"
        fold = {
            "dataset_id": dataset["dataset_id"],
            "independence_group": dataset["independence_group"],
            "fold_index": 0,
            "status": "completed",
            "train_trading_day_from": "2024-01-01",
            "train_trading_day_to": "2024-12-31",
            "validation_trading_day_from": "2025-01-01",
            "validation_trading_day_to": "2025-03-31",
            "train_range": {"warmup_from_bar_index": 0, "from_bar_index": 0, "to_bar_index": 251},
            "validation_range": {
                "warmup_from_bar_index": 0,
                "from_bar_index": 252,
                "to_bar_index": 314,
            },
            "selected_parameters": {"threshold": 1},
            "training_ranking": [
                {"evaluation_index": 0, "train_run_id": run_id, "train_run_signature": signature}
            ],
            "validation_metrics": {"total_return": 0.01, "max_drawdown": 0.02, "trade_count": 2},
            "validation_run_id": run_id,
            "validation_run_signature": signature,
            "parameter_changed": False,
            "changed_parameter_names": [],
        }
        return (
            {
                "dataset_id": dataset["dataset_id"],
                "data_revision": dataset["data_revision"],
                "independence_group": dataset["independence_group"],
                "trading_day_count": 600,
                "status": "completed",
                "folds": [fold],
                "walk_forward_summary": {"total_return": 0.01, "max_drawdown": 0.02},
                "oos_daily_returns": [{"trading_day": "2025-01-01", "daily_return": 0.01}],
            },
            [],
        )

    monkeypatch.setattr("tvbt.research.run_dataset_walk_forward", fake_walk)
    revision = "sha256:" + "1" * 64
    datasets = [
        {
            "dataset_id": f"TEST{index}.5m",
            "data_revision": revision,
            "timeframe": "5m",
            "independence_group": group,
            "trading_day_count": 600,
            "bars_path": f"normalized/{index}/bars.parquet",
            "meta_path": f"normalized/{index}/meta.json",
            "range": {"warmup_from_bar_index": 0, "from_bar_index": 0, "to_bar_index": 600},
            "run_id": f"unused-{index}",
            "run_signature": "sha256:" + "2" * 64,
        }
        for index, group in enumerate(("SHFE.AO", "DCE.I"))
    ]
    payload = {
        "contract_version": "1.0.0",
        "request_id": "request-wf",
        "trace_id": "trace-wf",
        "job_id": "research-wf",
        "research_study_id": "research-wf",
        "study_signature": "sha256:" + "a" * 64,
        "datasets": datasets,
        "strategy": {"kind": "strategy"},
        "parameters": {},
        "execution": {},
        "capital": {},
        "random_seed": 7,
        "walk_forward": {
            "train_trading_days": 252,
            "validation_trading_days": 63,
            "step_trading_days": 63,
            "search_space": [{"name": "threshold", "type": "integer", "candidates": [1]}],
            "objectives": [{"metric": "total_return", "direction": "maximize"}],
            "constraints": [],
            "search": {"method": "grid", "budget": 1, "random_seed": 7},
        },
        "output_path": "research-studies/research-wf",
    }
    output = guard.resolve(run_research_study(payload, guard, threading.Event()))
    manifest = json.loads((output / "research-study.json").read_text(encoding="utf-8"))
    assert manifest["study_mode"] == "walk_forward"
    assert len(manifest["child_runs"]) == 4
    assert (output / "out_of_sample_daily_returns.parquet").is_file()
    daily = pq.read_table(output / "out_of_sample_daily_returns.parquet").to_pylist()
    assert len(daily) == 3
    assert [row["independence_group"] for row in daily[:2]] == ["DCE.I", "SHFE.AO"]
    assert daily[-1] == {
        "series_kind": "portfolio",
        "independence_group": None,
        "trading_day": "2025-01-01",
        "daily_return": 0.01,
        "constituent_count": 2,
    }

    def fake_stress(
        payload_value: dict[str, Any],
        results_value: list[dict[str, Any]],
        guard_value: PathGuard,
        cancelled: threading.Event,
        progress: Any,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        del payload_value, results_value, guard_value, cancelled
        progress(
            1.0,
            {
                "stage": "stress_test",
                "completed_count": 2,
                "total_count": 2,
            },
        )
        scenario = {
            "scenario_id": "baseline",
            "status": "completed",
            "cost_multiplier": 1.0,
            "additional_slippage_ticks": 0.0,
            "additional_delay_bars": 0,
            "max_volume_participation_rate": None,
            "fill_mode": "unlimited",
            "completed_run_count": 2,
            "failed_run_count": 0,
            "daily_return_count": 1,
            "total_return": 0.01,
            "max_drawdown": 0.0,
            "trade_count": 4,
            "requested_quantity": 4,
            "filled_quantity": 4,
            "fill_rate": 1.0,
            "return_degradation": 0.0,
            "drawdown_degradation": 0.0,
            "fill_rate_degradation": 0.0,
            "failure_reason": None,
        }
        child = {
            "dataset_id": "TEST0.5m",
            "run_id": "run-stress",
            "run_signature": "sha256:" + "9" * 64,
            "role": "stress",
            "fold_index": 0,
            "scenario_id": "baseline",
        }
        return [scenario], [{"scenario": scenario, "runs": []}], [child]

    monkeypatch.setattr("tvbt.research.run_stress_suite", fake_stress)
    stress_payload = {
        **payload,
        "job_id": "research-stress",
        "research_study_id": "research-stress",
        "study_signature": "sha256:" + "b" * 64,
        "output_path": "research-studies/research-stress",
        "stress_test": {"suite_version": "1.0.0", "volume_participation_rate": 0.1},
    }
    stress_output = guard.resolve(run_research_study(stress_payload, guard, threading.Event()))
    stress_manifest = json.loads(
        (stress_output / "research-study.json").read_text(encoding="utf-8")
    )
    assert stress_manifest["study_mode"] == "walk_forward_stress"
    assert stress_manifest["child_runs"][-1]["role"] == "stress"
    assert stress_manifest["aggregate"]["stress_scenarios"][0]["scenario_id"] == "baseline"
    assert (stress_output / "stress_results.json").is_file()

    def fake_statistics(*args: Any, **kwargs: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        certification = {
            "rules_version": "1.0.0",
            "tier": "exploratory",
            "reliable_candidate_is_historical_only": True,
            "research_candidate_passed": False,
            "reliable_candidate_passed": False,
            "reasons": ["minimum_walk_forward_folds_not_met"],
            "evidence_matrix": [],
        }
        evidence = {
            "method_version": "1.0.0",
            "bootstrap": {},
            "multiple_comparisons": {},
            "parameter_neighborhood": {},
            "parameter_neighborhood_runs": [],
            "certification": certification,
        }
        child = {
            "dataset_id": "TEST0.5m",
            "run_id": "run-neighbor",
            "run_signature": "sha256:" + "8" * 64,
            "role": "neighbor",
            "fold_index": 0,
            "parameter_name": "threshold",
            "neighbor_direction": "upper",
        }
        return evidence, [child]

    monkeypatch.setattr("tvbt.research.run_statistical_validation", fake_statistics)
    certification_payload = {
        **stress_payload,
        "job_id": "research-certification",
        "research_study_id": "research-certification",
        "study_signature": "sha256:" + "c" * 64,
        "output_path": "research-studies/research-certification",
        "statistical_validation": {
            "method_version": "1.0.0",
            "block_size_trading_days": 5,
            "iterations": 2000,
            "confidence_level": 0.95,
            "random_seed": 7,
            "holm_alpha": 0.05,
        },
    }
    certification_output = guard.resolve(
        run_research_study(certification_payload, guard, threading.Event())
    )
    certification_manifest = json.loads(
        (certification_output / "research-study.json").read_text(encoding="utf-8")
    )
    assert certification_manifest["study_mode"] == "walk_forward_certification"
    assert certification_manifest["child_runs"][-1]["role"] == "neighbor"
    assert certification_manifest["aggregate"]["certification"]["tier"] == "exploratory"
    assert (certification_output / "statistical_evidence.json").is_file()
