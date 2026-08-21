from __future__ import annotations

import json
import threading
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from tvbt.algorithms import definitions as algorithm_definitions
from tvbt.auxiliary.macd_zero_axis import (
    MacdZeroAxisConfig,
    MacdZeroAxisSeries,
    classify_macd_zero_axis,
    definition,
    timeframe_minutes,
)
from tvbt.backtest import run_backtest
from tvbt.storage.path_guard import PathGuard
from tvbt.strategy import StrategyBar, run_strategy


def config(*, buffer: int = 0, risk_off_bars: int = 2, reclaim_bars: int = 2) -> MacdZeroAxisConfig:
    return MacdZeroAxisConfig(
        minimum_timeframe_minutes=5,
        fast_period=2,
        slow_period=3,
        signal_period=2,
        zero_axis_buffer_i64=buffer,
        risk_off_confirm_bars=risk_off_bars,
        reclaim_confirm_bars=reclaim_bars,
        dataset_timeframe="5m",
    )


def bars(closes: list[int]) -> list[StrategyBar]:
    return [
        StrategyBar(
            bar_index=index,
            timestamp_utc=1_700_000_000_000 + index * 60_000,
            open_i64=close,
            high_i64=close + 1,
            low_i64=close - 1,
            close_i64=close,
        )
        for index, close in enumerate(closes)
    ]


def test_definition_publishes_only_auxiliary_risk_events() -> None:
    value = definition()
    assert value["algorithm_id"] == "aux_macd_zero_axis_defense"
    assert value["algorithm_version"] == "1.0.0"
    assert value["kind"] == "strategy"
    assert value["name"] == "辅助·MACD零轴防守（风险开关不交易）"
    assert {output["name"] for output in value["outputs"]} == {
        "aux_macd_risk_off",
        "aux_macd_risk_on_candidate",
    }
    assert any(item["algorithm_id"] == value["algorithm_id"] for item in algorithm_definitions())


def test_both_lines_must_confirm_below_and_above_before_risk_gate_changes() -> None:
    closes = [100] * 10
    series = MacdZeroAxisSeries(
        diff=[None, -1, -2, 0, 1, 2, -1, -2, 1, 2],
        dea=[None, -1, -2, 0, 1, 2, -1, -2, 1, 2],
    )
    events = classify_macd_zero_axis(bars(closes), series, config())
    assert [event.event_type for event in events] == [
        "aux_macd_risk_off",
        "aux_macd_risk_on_candidate",
        "aux_macd_risk_off",
        "aux_macd_risk_on_candidate",
    ]
    assert [event.known_at_bar_index for event in events] == [2, 5, 7, 9]
    assert events[0].details["max_participation_multiplier"] == 0.0
    assert events[1].details["max_participation_multiplier"] == 1.0
    assert events[1].details["candidate_only"] is True
    assert all(event.details["standard_signal"] is False for event in events)
    assert all(event.details["execution_allowed"] is False for event in events)


def test_one_line_or_zero_buffer_equality_never_counts_as_axis_confirmation() -> None:
    series = MacdZeroAxisSeries(
        diff=[-2, -2, 0, 2, 2],
        dea=[2, -2, -2, 0, 2],
    )
    assert classify_macd_zero_axis(bars([100] * 5), series, config()) == []

    buffered = MacdZeroAxisSeries(diff=[-1, -2, 1, 2], dea=[-1, -2, 1, 2])
    events = classify_macd_zero_axis(
        bars([100] * 4), buffered, config(buffer=1, risk_off_bars=1, reclaim_bars=1)
    )
    assert [event.known_at_bar_index for event in events] == [1, 3]


def test_every_prefix_has_exactly_the_events_known_in_that_prefix() -> None:
    values = MacdZeroAxisSeries(
        diff=[None, -1, -2, 0, 1, 2, -1, -2, 1, 2],
        dea=[None, -1, -2, 0, 1, 2, -1, -2, 1, 2],
    )
    all_bars = bars([100] * 10)
    complete = classify_macd_zero_axis(all_bars, values, config())
    for length in range(1, len(all_bars) + 1):
        prefix = classify_macd_zero_axis(
            all_bars[:length],
            MacdZeroAxisSeries(values.diff[:length], values.dea[:length]),
            config(),
        )
        assert prefix == [event for event in complete if event.known_at_bar_index < length]


def test_fixed_timeframe_and_parameter_relations_fail_explicitly() -> None:
    parameters: dict[str, object] = {
        "minimum_timeframe_minutes": 60,
        "fast_period": 12,
        "slow_period": 26,
        "signal_period": 9,
        "zero_axis_buffer_ticks": 0,
        "risk_off_confirm_bars": 1,
        "reclaim_confirm_bars": 2,
    }
    with pytest.raises(ValueError, match="must equal the fixed"):
        MacdZeroAxisConfig.from_parameters(parameters, 1, "5m")
    parameters["minimum_timeframe_minutes"] = 5
    parameters["fast_period"] = 26
    with pytest.raises(ValueError, match="fast_period must be less"):
        MacdZeroAxisConfig.from_parameters(parameters, 1, "5m")
    with pytest.raises(ValueError, match="Nm, Nh or Nd"):
        timeframe_minutes("weekly")
    assert timeframe_minutes("2h") == 120
    assert timeframe_minutes("1d") == 1440


def test_runner_and_formal_run_publish_visible_events_but_no_trades(tmp_path: Path) -> None:
    dataset = tmp_path / "normalized" / "TEST.MACD.5m" / "revision"
    dataset.mkdir(parents=True)
    closes = [10, 9, 8, 7, 6, 5, 6, 7, 8, 9, 10, 11, 12, 11, 10, 9, 8]
    pq.write_table(
        pa.table(
            {
                "bar_index": pa.array(range(len(closes)), type=pa.int64()),
                "timestamp_utc": pa.array(
                    [1_700_000_000_000 + index * 300_000 for index in range(len(closes))],
                    type=pa.int64(),
                ),
                "open_i64": pa.array(closes, type=pa.int64()),
                "high_i64": pa.array([value + 1 for value in closes], type=pa.int64()),
                "low_i64": pa.array([value - 1 for value in closes], type=pa.int64()),
                "close_i64": pa.array(closes, type=pa.int64()),
            }
        ),
        dataset / "bars.parquet",
    )
    (dataset / "meta.json").write_text(
        json.dumps({"timeframe": "5m", "price": {"price_scale": 1, "tick_size_i64": 1}}),
        encoding="utf-8",
    )
    algorithm = definition()
    parameters = {
        "minimum_timeframe_minutes": 5,
        "fast_period": 2,
        "slow_period": 3,
        "signal_period": 2,
        "zero_axis_buffer_ticks": 0,
        "risk_off_confirm_bars": 1,
        "reclaim_confirm_bars": 2,
    }
    payload = {
        "dataset": {
            "dataset_id": "TEST.MACD.5m",
            "data_revision": "sha256:" + "1" * 64,
            "bars_path": "normalized/TEST.MACD.5m/revision/bars.parquet",
            "meta_path": "normalized/TEST.MACD.5m/revision/meta.json",
        },
        "algorithm": {
            key: algorithm[key]
            for key in ("kind", "algorithm_id", "algorithm_version", "source_hash")
        },
        "parameters": parameters,
    }
    full = run_strategy(payload, PathGuard(tmp_path), threading.Event())
    prefix = run_strategy(payload, PathGuard(tmp_path), threading.Event(), last_bar_index=8)
    assert [event["event_type"] for event in full.chart_events] == [
        "aux_macd_risk_off",
        "aux_macd_risk_on_candidate",
        "aux_macd_risk_off",
    ]
    assert full.trade_signals == full.strategy_states == full.stage_signals == []
    assert prefix.events == [event for event in full.events if event["known_at_bar_index"] <= 8]

    run_ref = run_backtest(
        {
            **payload,
            "run_id": "run-aux-macd",
            "run_signature": "sha256:" + "2" * 64,
            "trace_id": "trace-aux-macd",
            "range": {
                "warmup_from_bar_index": 0,
                "from_bar_index": 0,
                "to_bar_index": len(closes) - 1,
            },
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
            "output_path": "runs/run-aux-macd",
        },
        PathGuard(tmp_path),
        threading.Event(),
    )
    run_path = tmp_path / run_ref
    assert pq.read_table(run_path / "trades.parquet").num_rows == 0
    assert pq.read_table(run_path / "trade_signals.parquet").num_rows == 0
    manifest = json.loads((run_path / "run.json").read_text(encoding="utf-8"))
    assert [item["algorithm_id"] for item in manifest["strategy"]["indicator_dependencies"]] == [
        "macd"
    ]
    assert (run_path / "_SUCCESS").is_file()
