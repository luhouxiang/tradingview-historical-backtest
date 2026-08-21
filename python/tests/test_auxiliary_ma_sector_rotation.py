from __future__ import annotations

import json
import threading
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pyarrow as pa
import pyarrow.parquet as pq

from tvbt.auxiliary.ma_sector_rotation import (
    DivergenceUpdate,
    MaSectorRotationConfig,
    RankingBar,
    RankingContext,
    RankingInstrument,
    classify_ma_sector_rotation,
    definition,
)
from tvbt.backtest import run_backtest
from tvbt.storage.path_guard import PathGuard


def _parameters(**overrides: int) -> dict[str, int]:
    values = {
        "checkpoint_interval": 64,
        **{f"ma_period_{ordinal}": ordinal + 1 for ordinal in range(1, 9)},
        "minimum_sector_coverage_milli": 1000,
        "capacity_lookback_bars": 3,
        "minimum_average_volume": 100,
        "maximum_rotation_candidates": 10,
    }
    values.update(overrides)
    return values


def _context(*, available: int = 9) -> RankingContext:
    revision_a = "sha256:" + "1" * 64
    revision_b = "sha256:" + "2" * 64
    return RankingContext.from_payload(
        {
            "universe_id": "sample-universe",
            "membership_revision": "sha256:" + "3" * 64,
            "membership_mode": "point_in_time",
            "price_adjustment_mode": "forward_adjusted",
            "price_adjustment_revision": "sha256:" + "4" * 64,
            "episode_id": "episode-1",
            "episode_start_timestamp_utc": 0,
            "episode_available_at_utc": available,
            "memberships": [
                {
                    "dataset_id": "A.1d",
                    "data_revision": revision_a,
                    "sector_id": "sector-a",
                    "effective_from_utc": 0,
                    "effective_to_utc": None,
                    "available_at_utc": 0,
                },
                {
                    "dataset_id": "B.1d",
                    "data_revision": revision_b,
                    "sector_id": "sector-a",
                    "effective_from_utc": 0,
                    "effective_to_utc": None,
                    "available_at_utc": 0,
                },
            ],
        }
    )


def _instrument(dataset_id: str, closes: list[int]) -> RankingInstrument:
    revision = "sha256:" + ("1" if dataset_id == "A.1d" else "2") * 64
    return RankingInstrument(
        dataset_id,
        revision,
        tuple(RankingBar(index, index, close, 1000) for index, close in enumerate(closes)),
    )


def _run(
    closes_a: list[int],
    closes_b: list[int],
    *,
    updates: tuple[DivergenceUpdate, ...] = (),
):
    return classify_ma_sector_rotation(
        anchor_dataset_id="A.1d",
        instruments=[_instrument("A.1d", closes_a), _instrument("B.1d", closes_b)],
        context=_context(),
        config=MaSectorRotationConfig.from_parameters(_parameters()),
        divergence_updates=updates,
    )


def test_definition_is_explicit_non_trading_ranking_adapter() -> None:
    value = definition()
    assert value["algorithm_id"] == "aux_ma_sector_rotation"
    assert value["kind"] == "strategy"
    assert [output["name"] for output in value["outputs"]] == [
        "aux_ma_strength_class",
        "aux_sector_strength_mean",
        "aux_rotation_candidate",
    ]


def test_strict_close_above_classification_and_sector_mean() -> None:
    events = _run(list(range(100, 112)), [100] * 12)
    classes = {
        event.details["instrument_dataset_id"]: event.details["instrument_strength_class"]
        for event in events
        if event.event_type == "aux_ma_strength_class" and event.known_at_bar_index == 9
    }
    assert classes == {"A.1d": 9, "B.1d": 1}
    sector = next(
        event
        for event in events
        if event.event_type == "aux_sector_strength_mean" and event.known_at_bar_index == 9
    )
    assert sector.details["sector_strength_class_sum"] == 10
    assert sector.details["sector_strength_class_count"] == 2
    assert sector.details["sector_strength_mean_milli"] == 5000
    assert sector.details["ma_equal_is_conquered"] is False


def test_rotation_uses_same_timestamp_facts_capacity_and_deletes_with_source() -> None:
    updates = (
        DivergenceUpdate(
            "A.1d",
            "source-top",
            "upsert",
            10,
            "top_divergence",
            "trend",
            8,
            8,
            108,
        ),
        DivergenceUpdate(
            "B.1d",
            "candidate-bottom",
            "upsert",
            10,
            "bottom_divergence",
            "trend",
            8,
            8,
            100,
        ),
        DivergenceUpdate("A.1d", "source-top", "delete", 11),
    )
    events = _run(list(range(100, 112)), [100] * 12, updates=updates)
    candidate = next(
        event
        for event in events
        if event.operation == "upsert" and event.event_type == "aux_rotation_candidate"
    )
    assert candidate.known_at_bar_index == 10
    assert candidate.details["candidate_dataset_id"] == "B.1d"
    assert candidate.details["adjusted_ready"] is True
    assert candidate.details["catch_up"] is True
    assert candidate.details["capacity_average_volume"] == 1000
    assert candidate.details["execution_allowed"] is False
    deleted = next(event for event in events if event.operation == "delete")
    assert deleted.event_id == candidate.event_id
    assert deleted.known_at_bar_index == 11


def test_prefix_is_invariant_when_future_bars_are_appended() -> None:
    short = _run(list(range(100, 111)), [100] * 11)
    long = _run(list(range(100, 113)), [100] * 13)
    short_rows = [event for event in short if event.known_at_bar_index <= 10]
    long_rows = [event for event in long if event.known_at_bar_index <= 10]
    assert short_rows == long_rows


def test_invalid_period_ladder_and_membership_overlap_are_rejected() -> None:
    parameters = _parameters(ma_period_3=3)
    try:
        MaSectorRotationConfig.from_parameters(parameters)
    except ValueError as error:
        assert "strictly increasing" in str(error)
    else:
        raise AssertionError("non-increasing MA ladder was accepted")

    payload = {
        "universe_id": "u",
        "membership_revision": "sha256:" + "3" * 64,
        "membership_mode": "point_in_time",
        "price_adjustment_mode": "forward_adjusted",
        "price_adjustment_revision": "sha256:" + "4" * 64,
        "episode_id": "e",
        "episode_start_timestamp_utc": 0,
        "episode_available_at_utc": 1,
        "memberships": [
            {
                "dataset_id": "A.1d",
                "data_revision": "sha256:" + "1" * 64,
                "sector_id": "s1",
                "effective_from_utc": 0,
                "effective_to_utc": 10,
                "available_at_utc": 0,
            },
            {
                "dataset_id": "A.1d",
                "data_revision": "sha256:" + "1" * 64,
                "sector_id": "s2",
                "effective_from_utc": 9,
                "effective_to_utc": None,
                "available_at_utc": 0,
            },
            {
                "dataset_id": "B.1d",
                "data_revision": "sha256:" + "2" * 64,
                "sector_id": "s2",
                "effective_from_utc": 0,
                "effective_to_utc": None,
                "available_at_utc": 0,
            },
        ],
    }
    try:
        RankingContext.from_payload(payload)
    except ValueError as error:
        assert "must not overlap" in str(error)
    else:
        raise AssertionError("overlapping membership intervals were accepted")


def test_formal_zero_trade_run_records_ranking_context_and_chart_facts(
    tmp_path: Path, monkeypatch: object
) -> None:
    guard = PathGuard(tmp_path)
    refs = []
    for dataset_id, revision_digit, closes in (
        ("A.1d", "1", list(range(100, 112))),
        ("B.1d", "2", [100] * 12),
    ):
        directory = tmp_path / "normalized" / dataset_id / "revision"
        directory.mkdir(parents=True)
        pq.write_table(
            pa.table(
                {
                    "bar_index": pa.array(range(12), type=pa.int64()),
                    "timestamp_utc": pa.array(range(12), type=pa.int64()),
                    "open_i64": pa.array(closes, type=pa.int64()),
                    "high_i64": pa.array([value + 1 for value in closes], type=pa.int64()),
                    "low_i64": pa.array([value - 1 for value in closes], type=pa.int64()),
                    "close_i64": pa.array(closes, type=pa.int64()),
                    "volume": pa.array([1000] * 12, type=pa.int64()),
                }
            ),
            directory / "bars.parquet",
        )
        revision = "sha256:" + revision_digit * 64
        (directory / "meta.json").write_text(
            json.dumps(
                {
                    "dataset_id": dataset_id,
                    "data_revision": revision,
                    "timeframe": "1d",
                    "source": {"timestamp_semantics": "bar_end"},
                    "time": {"date_semantics": "trading_day", "timezone": "Asia/Shanghai"},
                    "price": {"price_scale": 1, "tick_size_i64": 1},
                }
            ),
            encoding="utf-8",
        )
        refs.append(
            {
                "dataset_id": dataset_id,
                "data_revision": revision,
                "bars_path": f"normalized/{dataset_id}/revision/bars.parquet",
                "meta_path": f"normalized/{dataset_id}/revision/meta.json",
            }
        )
    monkeypatch.setattr(
        "tvbt.strategy.run_chan",
        lambda *args, **kwargs: (SimpleNamespace(emitter=SimpleNamespace(events=[])), [], {}),
    )
    algorithm = definition()
    public_context = asdict(_context())
    public_context["memberships"] = list(public_context["memberships"])
    payload = {
        "dataset": refs[0],
        "ranking_datasets": refs,
        "ranking_context": public_context,
        "algorithm": {
            key: algorithm[key]
            for key in ("kind", "algorithm_id", "algorithm_version", "source_hash")
        },
        "parameters": _parameters(),
        "range": {"warmup_from_bar_index": 0, "from_bar_index": 9, "to_bar_index": 11},
        "run_id": "ranking-run",
        "run_signature": "sha256:" + "5" * 64,
        "trace_id": "ranking-trace",
        "execution": {
            "signal_timing": "bar_close",
            "fill_timing": "next_bar_open",
            "commission": {"mode": "fixed_per_contract", "amount_i64": 0},
            "slippage": {"mode": "ticks", "value": 0},
            "contract_multiplier": 1,
            "margin_ratio": 1,
            "intrabar_conflict_rule": "worst_case",
        },
        "capital": {"initial_cash_i64": 1_000_000, "currency": "CNY", "money_scale": 100},
        "random_seed": 20260821,
        "output_path": "runs/ranking-run",
    }
    run_ref = run_backtest(payload, guard, threading.Event())
    run_directory = tmp_path / run_ref
    manifest = json.loads((run_directory / "run.json").read_text(encoding="utf-8"))
    assert manifest["ranking_context"]["membership_mode"] == "point_in_time"
    assert manifest["ranking_context"]["price_adjustment_mode"] == "forward_adjusted"
    assert manifest["strategy"]["indicator_dependencies"][0]["algorithm_id"] == "ma"
    summary = json.loads((run_directory / "summary.json").read_text(encoding="utf-8"))
    assert summary["trade_count"] == 0
    rows = pq.read_table(run_directory / "chart_events.parquet").to_pylist()
    assert any("aux_ma_strength_class" in row["payload_json"] for row in rows)
    assert any("aux_sector_strength_mean" in row["payload_json"] for row in rows)
    assert (run_directory / "_SUCCESS").is_file()
