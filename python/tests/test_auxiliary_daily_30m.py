from __future__ import annotations

import json
import threading
from dataclasses import replace
from datetime import date
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from tvbt.auxiliary.daily_30m import (
    EXPECTED_SOURCE_HHMM,
    Daily30mBar,
    Daily30mConfig,
    classify_daily_30m_sessions,
    definition,
)
from tvbt.backtest import run_backtest
from tvbt.storage.path_guard import PathGuard
from tvbt.strategy import run_strategy


def config() -> Daily30mConfig:
    return Daily30mConfig.from_parameters(
        {
            "checkpoint_interval": 1024,
            "observation_timeframe_minutes": 30,
            "session_profile_id": 1,
        },
        dataset_timeframe="30m",
        timestamp_semantics="bar_end",
        date_semantics="trading_day",
        timezone="Asia/Shanghai",
    )


def session(
    ranges: list[tuple[int, int]],
    *,
    trading_day: str = "2026-08-20",
    start_bar_index: int = 0,
    source_hhmm: tuple[int, ...] = EXPECTED_SOURCE_HHMM,
    final_close: int | None = None,
) -> list[Daily30mBar]:
    result: list[Daily30mBar] = []
    for position, ((low_i64, high_i64), hhmm) in enumerate(zip(ranges, source_hhmm, strict=True)):
        close_i64 = (
            final_close
            if final_close is not None and position == len(ranges) - 1
            else (low_i64 + high_i64) // 2
        )
        result.append(
            Daily30mBar(
                bar_index=start_bar_index + position,
                timestamp_utc=1_700_000_000_000 + (start_bar_index + position) * 1_800_000,
                trading_day=trading_day,
                source_hhmm=hhmm,
                open_i64=close_i64,
                high_i64=high_i64,
                low_i64=low_i64,
                close_i64=close_i64,
            )
        )
    return result


def test_one_center_uses_first_overlap_closed_boundaries_and_dual_extreme_profile() -> None:
    bars = session(
        [(90, 110), (95, 108), (100, 105), (80, 95), (70, 85), (60, 75), (50, 65), (100, 105)],
        final_close=100,
    )
    classified = classify_daily_30m_sessions(bars, config())
    assert len(classified) == 1
    event = classified[0]
    assert event.known_at_bar_index == 7
    assert event.details["classification"] == "daily_one_center"
    assert event.details["balance_subtype"] == "weak_balance"
    assert event.details["close_position"] == "inside_center"
    assert event.details["center_1_low_i64"] == 100
    assert event.details["center_1_high_i64"] == 105
    assert event.details["standard_center"] is False
    assert event.details["semantic_namespace"] == "heuristic"
    assert event.details["execution_allowed"] is False

    both_extremes = [
        replace(bar, low_i64=50 if bar.bar_index == 0 else bar.low_i64) for bar in bars
    ]
    both_extremes[0] = replace(both_extremes[0], open_i64=90, close_i64=90)
    assert (
        classify_daily_30m_sessions(both_extremes, config())[0].details["balance_subtype"]
        == "dual_extreme_balance"
    )


def test_two_centers_require_a_separator_and_strictly_nonoverlapping_closed_intervals() -> None:
    bars = session(
        [(10, 20), (12, 22), (11, 19), (20, 30), (30, 40), (32, 42), (31, 39), (34, 45)],
        final_close=42,
    )
    event = classify_daily_30m_sessions(bars, config())[0]
    assert event.details["classification"] == "daily_two_center"
    assert event.details["direction"] == "up"
    assert event.details["center_1_start_ordinal"] == 1
    assert event.details["center_2_start_ordinal"] == 5
    assert event.details["close_position"] == "above_upper_center"

    touching = [
        replace(bar, low_i64=19 if bar.bar_index in {4, 5, 6, 7} else bar.low_i64) for bar in bars
    ]
    touching = [
        replace(
            bar, open_i64=max(bar.open_i64, bar.low_i64), close_i64=max(bar.close_i64, bar.low_i64)
        )
        for bar in touching
    ]
    assert (
        classify_daily_30m_sessions(touching, config())[0].details["classification"]
        == "daily_one_center"
    )

    no_separator = []
    for bar in bars:
        if bar.bar_index in {3, 4, 5}:
            no_separator.append(replace(bar, low_i64=30, high_i64=40, open_i64=35, close_i64=35))
        elif bar.bar_index in {6, 7}:
            no_separator.append(replace(bar, low_i64=0, high_i64=5, open_i64=2, close_i64=2))
        else:
            no_separator.append(bar)
    assert (
        classify_daily_30m_sessions(no_separator, config())[0].details["classification"]
        == "daily_one_center"
    )


def test_no_center_and_all_complete_classifications_wait_for_the_eighth_close() -> None:
    bars = session([(10 + 11 * index, 20 + 11 * index) for index in range(8)], final_close=97)
    for end in range(1, 8):
        assert classify_daily_30m_sessions(bars[:end], config()) == []
    event = classify_daily_30m_sessions(bars, config())[0]
    assert event.details["classification"] == "daily_no_center"
    assert event.details["direction"] == "up"
    assert event.details["daily_strength_subclass"] == "no_center_up"
    assert event.details["candidate_only"] is True


def test_missing_night_or_changed_session_is_rejected_without_a_classification() -> None:
    ranges = [(10 + index, 20 + index) for index in range(8)]
    missing = session(
        ranges[:7],
        source_hhmm=(1000, 1030, 1130, 1330, 1400, 1430, 1500),
    )
    rejected = classify_daily_30m_sessions(missing, config())
    assert [event.event_type for event in rejected] == ["aux_daily_30m_profile_rejected"]
    assert rejected[0].known_at_bar_index == 2
    assert rejected[0].details["classification"] is None

    night = session(ranges, source_hhmm=(2100, *EXPECTED_SOURCE_HHMM[:7]))
    assert classify_daily_30m_sessions(night, config())[0].reason_code == (
        "AUX_DAILY_8X30M_SESSION_TEMPLATE_MISMATCH"
    )

    unfinished = session(ranges[:7], source_hhmm=EXPECTED_SOURCE_HHMM[:7])
    assert classify_daily_30m_sessions(unfinished, config()) == []

    next_day = session(ranges[:1], trading_day="2026-08-21", start_bar_index=7, source_hhmm=(1000,))
    missing_last = classify_daily_30m_sessions([*unfinished, *next_day], config())
    assert missing_last[0].reason_code == ("AUX_DAILY_8X30M_MISSING_BAR_BEFORE_NEXT_TRADING_DAY")
    assert missing_last[0].known_at_bar_index == 7


def test_session_change_deletes_prior_classification_and_prefixes_are_exact() -> None:
    bars = session([(10 + 11 * index, 20 + 11 * index) for index in range(8)], final_close=97)
    extra = replace(
        bars[-1],
        bar_index=8,
        timestamp_utc=bars[-1].timestamp_utc + 1_800_000,
        source_hhmm=1530,
    )
    full = classify_daily_30m_sessions([*bars, extra], config())
    assert [event.operation for event in full] == ["upsert", "delete", "upsert"]
    assert full[1].event_id == full[0].event_id
    for end in range(1, 10):
        prefix = classify_daily_30m_sessions([*bars, extra][:end], config())
        assert prefix == [event for event in full if event.known_at_bar_index < end]


def test_definition_and_profile_validation_keep_the_course_scope_fixed() -> None:
    resolved = definition()
    assert resolved["algorithm_id"] == "aux_daily_30m_classification"
    assert resolved["causal"] is True
    assert len(resolved["source_hash"]) == 71
    parameters = {
        "checkpoint_interval": 1024,
        "observation_timeframe_minutes": 30,
        "session_profile_id": 1,
    }
    for field, value in (
        ("dataset_timeframe", "5m"),
        ("timestamp_semantics", "bar_start"),
        ("date_semantics", "calendar_date"),
        ("timezone", "UTC"),
    ):
        arguments = {
            "dataset_timeframe": "30m",
            "timestamp_semantics": "bar_end",
            "date_semantics": "trading_day",
            "timezone": "Asia/Shanghai",
        }
        arguments[field] = value
        with pytest.raises(ValueError):
            Daily30mConfig.from_parameters(parameters, **arguments)


def test_runner_and_formal_run_publish_visible_classification_without_trades(
    tmp_path: Path,
) -> None:
    dataset = tmp_path / "normalized" / "TEST.DAILY30M.30m" / "revision"
    dataset.mkdir(parents=True)
    ranges = [(10, 20), (12, 22), (11, 19), (20, 30), (30, 40), (32, 42), (31, 39), (34, 45)]
    closes = [(low + high) // 2 for low, high in ranges]
    closes[-1] = 42
    pq.write_table(
        pa.table(
            {
                "bar_index": pa.array(range(8), type=pa.int64()),
                "timestamp_utc": pa.array(
                    [1_700_000_000_000 + index * 1_800_000 for index in range(8)],
                    type=pa.int64(),
                ),
                "trading_day": pa.array([date(2026, 8, 20)] * 8, type=pa.date32()),
                "source_hhmm": pa.array(EXPECTED_SOURCE_HHMM, type=pa.int32()),
                "open_i64": pa.array(closes, type=pa.int64()),
                "high_i64": pa.array([high for _, high in ranges], type=pa.int64()),
                "low_i64": pa.array([low for low, _ in ranges], type=pa.int64()),
                "close_i64": pa.array(closes, type=pa.int64()),
            }
        ),
        dataset / "bars.parquet",
    )
    (dataset / "meta.json").write_text(
        json.dumps(
            {
                "timeframe": "30m",
                "source": {"timestamp_semantics": "bar_end"},
                "time": {"date_semantics": "trading_day", "timezone": "Asia/Shanghai"},
                "price": {"price_scale": 1, "tick_size_i64": 1},
            }
        ),
        encoding="utf-8",
    )
    algorithm = definition()
    parameters = {
        "checkpoint_interval": 64,
        "observation_timeframe_minutes": 30,
        "session_profile_id": 1,
    }
    payload = {
        "dataset": {
            "dataset_id": "TEST.DAILY30M.30m",
            "data_revision": "sha256:" + "1" * 64,
            "bars_path": "normalized/TEST.DAILY30M.30m/revision/bars.parquet",
            "meta_path": "normalized/TEST.DAILY30M.30m/revision/meta.json",
        },
        "algorithm": {
            key: algorithm[key]
            for key in ("kind", "algorithm_id", "algorithm_version", "source_hash")
        },
        "parameters": parameters,
    }
    guard = PathGuard(tmp_path)
    full = run_strategy(payload, guard, threading.Event())
    prefix = run_strategy(payload, guard, threading.Event(), last_bar_index=6)
    assert [event["event_type"] for event in full.chart_events] == ["aux_daily_30m_classification"]
    assert full.chart_events[0]["classification"] == "daily_two_center"
    assert full.chart_events[0]["known_at_bar_index"] == 7
    assert full.trade_signals == full.strategy_states == full.stage_signals == []
    assert prefix.events == []

    run_ref = run_backtest(
        {
            **payload,
            "run_id": "run-aux-daily30m",
            "run_signature": "sha256:" + "2" * 64,
            "trace_id": "trace-aux-daily30m",
            "range": {"warmup_from_bar_index": 0, "from_bar_index": 0, "to_bar_index": 7},
            "execution": {
                "signal_timing": "bar_close",
                "fill_timing": "next_bar_open",
                "commission": {
                    "mode": "fixed_per_contract",
                    "amount_i64": 0,
                    "money_scale": 100,
                },
                "slippage": {"mode": "ticks", "value": 0},
                "contract_multiplier": 1,
                "margin_ratio": 0.1,
                "intrabar_conflict_rule": "worst_case",
            },
            "capital": {
                "initial_cash_i64": 1_000_000,
                "currency": "CNY",
                "money_scale": 100,
            },
            "random_seed": 20260821,
            "output_path": "runs/run-aux-daily30m",
        },
        guard,
        threading.Event(),
    )
    run_path = tmp_path / run_ref
    assert pq.read_table(run_path / "trades.parquet").num_rows == 0
    assert pq.read_table(run_path / "trade_signals.parquet").num_rows == 0
    chart_events = pq.read_table(run_path / "chart_events.parquet").to_pylist()
    assert len(chart_events) == 1
    assert json.loads(chart_events[0]["payload_json"])["event_type"] == (
        "aux_daily_30m_classification"
    )
    assert (run_path / "_SUCCESS").is_file()
