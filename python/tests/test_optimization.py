from __future__ import annotations

import json
import threading
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from tvbt.optimization import expand_search_space, run_study, select_candidates
from tvbt.storage.path_guard import PathGuard
from tvbt.strategy import definition


def test_search_space_grid_and_seeded_random_are_deterministic() -> None:
    space = [
        {"name": "period", "type": "integer", "minimum": 2, "maximum": 4, "step": 1},
        {"name": "enabled", "type": "boolean", "candidates": [True, False]},
    ]
    combinations = expand_search_space(space)
    assert combinations[0] == {"period": 2, "enabled": True}
    assert combinations[-1] == {"period": 4, "enabled": False}
    assert select_candidates(combinations, "grid", 3, 9) == combinations[:3]
    assert select_candidates(combinations, "random", 4, 9) == select_candidates(
        combinations, "random", 4, 9
    )
    assert len(select_candidates(combinations, "random", 99, 9)) == len(combinations)


def test_study_runs_standard_train_and_validation_backtests(tmp_path: Path) -> None:
    dataset = tmp_path / "normalized" / "TEST.A1.1m" / "revision"
    dataset.mkdir(parents=True)
    closes = [110, 110, 110, 90, 100, 90, 85, 115, 120, 110, 100, 90, 85, 115, 120]
    pq.write_table(
        pa.table(
            {
                "bar_index": pa.array(range(len(closes)), type=pa.int64()),
                "timestamp_utc": pa.array(
                    [index * 60_000 for index in range(len(closes))], type=pa.int64()
                ),
                "trading_day": pa.array(["2026-01-05"] * len(closes), type=pa.string()),
                "open_i64": pa.array(closes, type=pa.int64()),
                "high_i64": pa.array([value + 5 for value in closes], type=pa.int64()),
                "low_i64": pa.array([value - 5 for value in closes], type=pa.int64()),
                "close_i64": pa.array(closes, type=pa.int64()),
            }
        ),
        dataset / "bars.parquet",
    )
    (dataset / "meta.json").write_text(
        json.dumps({"price": {"price_scale": 1, "tick_size_i64": 1}}), encoding="utf-8"
    )
    algorithm = definition()
    payload = {
        "contract_version": "1.0.0",
        "request_id": "request-1",
        "trace_id": "trace-1",
        "job_id": "study-1",
        "study_id": "study-1",
        "dataset": {
            "dataset_id": "TEST.A1.1m",
            "data_revision": "sha256:" + "1" * 64,
            "bars_path": "normalized/TEST.A1.1m/revision/bars.parquet",
            "meta_path": "normalized/TEST.A1.1m/revision/meta.json",
        },
        "algorithm": {
            key: algorithm[key]
            for key in ("kind", "algorithm_id", "algorithm_version", "source_hash")
        },
        "base_parameters": {
            "ma_period": 3,
            "touch_tolerance_ticks": 1,
            "max_retest_bars": 5,
        },
        "search_space": [{"name": "ma_period", "type": "integer", "candidates": [2, 3]}],
        "objectives": [{"metric": "total_return", "direction": "maximize"}],
        "constraints": [{"metric": "trade_count", "operator": ">=", "value": 0}],
        "search": {"method": "random", "budget": 2, "random_seed": 7},
        "ranges": {
            "train": {"warmup_from_bar_index": 0, "from_bar_index": 0, "to_bar_index": 7},
            "validation": {
                "warmup_from_bar_index": 0,
                "from_bar_index": 8,
                "to_bar_index": 14,
            },
        },
        "execution": {
            "signal_timing": "bar_close",
            "fill_timing": "next_bar_open",
            "commission": {
                "mode": "fixed_per_contract",
                "amount_i64": 100,
                "money_scale": 100,
            },
            "slippage": {"mode": "ticks", "value": 0},
            "contract_multiplier": 1,
            "margin_ratio": 0.1,
            "intrabar_conflict_rule": "worst_case",
        },
        "capital": {"initial_cash_i64": 1_000_000, "currency": "CNY", "money_scale": 100},
        "output_path": "studies/study-1",
    }
    result_ref = run_study(payload, PathGuard(tmp_path), threading.Event())
    study = tmp_path / result_ref
    evaluations = json.loads((study / "evaluations.json").read_text(encoding="utf-8"))
    assert len(evaluations) == 2
    assert {value["train_rank"] for value in evaluations} == {1, 2}
    assert {value["validation_rank"] for value in evaluations} == {1, 2}
    assert all(
        (tmp_path / "runs" / value["train_run_id"] / "_SUCCESS").is_file() for value in evaluations
    )
    assert all(
        (tmp_path / "runs" / value["validation_run_id"] / "_SUCCESS").is_file()
        for value in evaluations
    )
    assert (study / "study.json").is_file()
    assert (study / "stability.json").is_file()
    assert (study / "_SUCCESS").is_file()
