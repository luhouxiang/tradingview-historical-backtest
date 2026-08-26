from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from tvbt.storage.path_guard import PathGuard
from tvbt.walk_forward import run_dataset_walk_forward, trading_day_folds


def _bars(path: Path, count: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(
        pa.table(
            {
                "bar_index": pa.array(range(count), type=pa.int64()),
                "trading_day": pa.array(
                    [
                        f"{2020 + index // 360:04d}-{index // 30 % 12 + 1:02d}-{index % 30 + 1:02d}"
                        for index in range(count)
                    ]
                ),
            }
        ),
        path,
    )


def test_fold_boundaries_are_prefix_invariant_and_keep_dataset_start_warmup(
    tmp_path: Path,
) -> None:
    short = tmp_path / "short.parquet"
    long = tmp_path / "long.parquet"
    _bars(short, 378)
    _bars(long, 441)
    range_short = {"warmup_from_bar_index": 0, "from_bar_index": 0, "to_bar_index": 377}
    range_long = {"warmup_from_bar_index": 0, "from_bar_index": 0, "to_bar_index": 440}
    first = trading_day_folds(short, range_short, 252, 63, 63)
    extended = trading_day_folds(long, range_long, 252, 63, 63)
    assert len(first) == 2
    assert extended[: len(first)] == first
    assert first[0]["train_range"] == {
        "warmup_from_bar_index": 0,
        "from_bar_index": 0,
        "to_bar_index": 251,
    }
    assert first[0]["validation_range"] == {
        "warmup_from_bar_index": 0,
        "from_bar_index": 252,
        "to_bar_index": 314,
    }
    assert first[1]["train_range"]["from_bar_index"] == 63
    assert first[1]["validation_range"]["from_bar_index"] == 315


def test_each_fold_selects_only_from_training_and_records_parameter_drift(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    guard = PathGuard(tmp_path)
    bars = guard.resolve("normalized/test/bars.parquet")
    _bars(bars, 378)
    calls: list[tuple[str, int, int]] = []

    def fake_run(
        payload: dict[str, Any],
        dataset: dict[str, Any],
        parameters: dict[str, Any],
        range_value: dict[str, Any],
        run_id: str,
        guard_value: PathGuard,
        cancelled: threading.Event,
    ) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
        del payload, dataset, guard_value, cancelled
        value = int(parameters["threshold"])
        calls.append((run_id, value, int(range_value["from_bar_index"])))
        if run_id.endswith("train"):
            # Fold 0 selects 1; fold 1 selects 2. Validation outcomes are never
            # available while this ranking is being made.
            returned = (
                (0.2 if value == 1 else 0.1)
                if range_value["from_bar_index"] == 0
                else (0.1 if value == 1 else 0.3)
            )
            daily: list[dict[str, Any]] = []
        else:
            returned = -0.05 if value == 1 else 0.05
            daily = [
                {"trading_day": f"day-{range_value['from_bar_index']}", "daily_return": returned}
            ]
        return (
            {"total_return": returned, "max_drawdown": 0.1, "trade_count": 5},
            daily,
            "sha256:" + "1" * 64,
        )

    monkeypatch.setattr("tvbt.walk_forward._run", fake_run)
    payload = {
        "research_study_id": "research-test",
        "strategy": {},
        "parameters": {},
        "execution": {},
        "capital": {},
        "random_seed": 7,
        "walk_forward": {
            "train_trading_days": 252,
            "validation_trading_days": 63,
            "step_trading_days": 63,
            "search_space": [{"name": "threshold", "type": "integer", "candidates": [1, 2]}],
            "objectives": [{"metric": "total_return", "direction": "maximize"}],
            "constraints": [],
            "search": {"method": "grid", "budget": 2, "random_seed": 7},
        },
    }
    dataset = {
        "dataset_id": "TEST.5m",
        "data_revision": "sha256:" + "2" * 64,
        "independence_group": "TEST.T",
        "trading_day_count": 378,
        "bars_path": "normalized/test/bars.parquet",
        "range": {"warmup_from_bar_index": 0, "from_bar_index": 0, "to_bar_index": 377},
    }
    result, child_runs = run_dataset_walk_forward(payload, dataset, 0, guard, threading.Event())
    assert [fold["selected_parameters"]["threshold"] for fold in result["folds"]] == [1, 2]
    assert [fold["parameter_changed"] for fold in result["folds"]] == [False, True]
    assert result["walk_forward_summary"]["parameter_stability"] == 0
    validation_calls = [call for call in calls if call[0].endswith("validation")]
    assert [call[1] for call in validation_calls] == [1, 2]
    assert len(child_runs) == 6


def test_overlapping_validation_windows_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "bars.parquet"
    _bars(path, 400)
    with pytest.raises(ValueError, match="overlap"):
        trading_day_folds(
            path,
            {"warmup_from_bar_index": 0, "from_bar_index": 0, "to_bar_index": 399},
            252,
            63,
            21,
        )


def test_empty_search_space_runs_one_fixed_base_candidate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    guard = PathGuard(tmp_path)
    bars = guard.resolve("normalized/fixed/bars.parquet")
    _bars(bars, 315)
    parameters_seen: list[dict[str, Any]] = []

    def fake_run(
        payload: dict[str, Any],
        dataset: dict[str, Any],
        parameters: dict[str, Any],
        range_value: dict[str, Any],
        run_id: str,
        guard_value: PathGuard,
        cancelled: threading.Event,
    ) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
        del payload, dataset, range_value, guard_value, cancelled
        parameters_seen.append(parameters)
        daily = (
            []
            if run_id.endswith("train")
            else [{"trading_day": "2025-01-01", "daily_return": 0.01}]
        )
        return (
            {"total_return": 0.01, "max_drawdown": 0.0, "trade_count": 1},
            daily,
            "sha256:" + "3" * 64,
        )

    monkeypatch.setattr("tvbt.walk_forward._run", fake_run)
    payload = {
        "research_study_id": "research-fixed",
        "strategy": {},
        "parameters": {"checkpoint_interval": 1024},
        "execution": {},
        "capital": {},
        "random_seed": 7,
        "walk_forward": {
            "train_trading_days": 252,
            "validation_trading_days": 63,
            "step_trading_days": 63,
            "search_space": [],
            "objectives": [{"metric": "total_return", "direction": "maximize"}],
            "constraints": [],
            "search": {"method": "grid", "budget": 1, "random_seed": 7},
        },
    }
    dataset = {
        "dataset_id": "FIXED.5m",
        "data_revision": "sha256:" + "4" * 64,
        "independence_group": "FIXED.F",
        "trading_day_count": 315,
        "bars_path": "normalized/fixed/bars.parquet",
        "range": {"warmup_from_bar_index": 0, "from_bar_index": 0, "to_bar_index": 314},
    }
    result, child_runs = run_dataset_walk_forward(payload, dataset, 0, guard, threading.Event())
    assert result["folds"][0]["selected_parameters"] == {"checkpoint_interval": 1024}
    assert parameters_seen == [{"checkpoint_interval": 1024}, {"checkpoint_interval": 1024}]
    assert len(child_runs) == 2
