from __future__ import annotations

import json
import threading
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from tvbt.backtest import run_backtest
from tvbt.replay import generate_replay
from tvbt.storage.path_guard import PathGuard
from tvbt.strategy import MA20RetestShort, StrategyBar, definition


def test_ma_retest_strategy_emits_entry_and_exit_after_causal_transitions() -> None:
    strategy = MA20RetestShort(
        {"ma_period": 3, "touch_tolerance_ticks": 1, "max_retest_bars": 5}, 1
    )
    state = strategy.initialize()
    state.previous_close, state.previous_ma = 110, 100
    transitions = [
        strategy.on_bar(state, StrategyBar(3, 3_000, 100, 101, 89, 90), {"ma": 100}),
        strategy.on_bar(state, StrategyBar(4, 4_000, 95, 101, 90, 98), {"ma": 100}),
        strategy.on_bar(state, StrategyBar(5, 5_000, 99, 100, 89, 90), {"ma": 95}),
        strategy.on_bar(state, StrategyBar(6, 6_000, 100, 110, 99, 105), {"ma": 100}),
    ]
    assert [item.state_changes[0]["state_to"] for item in transitions] == [
        "waiting_retest",
        "waiting_retest_failure",
        "short_open",
        "waiting_break",
    ]
    assert transitions[2].trade_signals[0]["action"] == "open_short"
    assert transitions[3].trade_signals[0]["action"] == "close_short"


def test_replay_and_backtest_share_signals_and_next_open_fills(tmp_path: Path) -> None:
    guard = PathGuard(tmp_path)
    dataset = tmp_path / "normalized" / "TEST.A1.1m" / "revision"
    dataset.mkdir(parents=True)
    closes = [110, 110, 110, 90, 100, 90, 85, 115, 120]
    opens = [110, 110, 110, 100, 95, 105, 85, 90, 120]
    highs = [112, 112, 112, 101, 110, 100, 90, 120, 125]
    lows = [108, 108, 108, 89, 90, 89, 80, 85, 115]
    pq.write_table(
        pa.table(
            {
                "bar_index": pa.array(range(len(closes)), type=pa.int64()),
                "timestamp_utc": pa.array(
                    [index * 60_000 for index in range(len(closes))], type=pa.int64()
                ),
                "open_i64": pa.array(opens, type=pa.int64()),
                "high_i64": pa.array(highs, type=pa.int64()),
                "low_i64": pa.array(lows, type=pa.int64()),
                "close_i64": pa.array(closes, type=pa.int64()),
            }
        ),
        dataset / "bars.parquet",
    )
    (dataset / "meta.json").write_text(
        json.dumps({"price": {"price_scale": 1, "tick_size_i64": 1}}), encoding="utf-8"
    )
    algorithm = definition()
    algorithm_ref = {
        key: algorithm[key] for key in ("kind", "algorithm_id", "algorithm_version", "source_hash")
    }
    facts = {
        "dataset": {
            "dataset_id": "TEST.A1.1m",
            "data_revision": "sha256:" + "1" * 64,
            "bars_path": "normalized/TEST.A1.1m/revision/bars.parquet",
            "meta_path": "normalized/TEST.A1.1m/revision/meta.json",
        },
        "algorithm": algorithm_ref,
        "parameters": {"ma_period": 3, "touch_tolerance_ticks": 1, "max_retest_bars": 5},
        "range": {"warmup_from_bar_index": 0, "from_bar_index": 0, "to_bar_index": 8},
    }
    replay_payload = {
        **facts,
        "cache_key": "sha256:" + "2" * 64,
        "output_path": "cache/replay/strategy",
    }
    replay_ref = generate_replay(replay_payload, guard, threading.Event())
    backtest_payload = {
        **facts,
        "run_id": "run-1",
        "run_signature": "sha256:" + "3" * 64,
        "trace_id": "trace-1",
        "execution": {
            "signal_timing": "bar_close",
            "fill_timing": "next_bar_open",
            "commission": {"mode": "fixed_per_contract", "amount_i64": 100, "money_scale": 100},
            "slippage": {"mode": "ticks", "value": 0},
            "contract_multiplier": 1,
            "margin_ratio": 0.1,
            "intrabar_conflict_rule": "worst_case",
        },
        "capital": {"initial_cash_i64": 1_000_000, "currency": "CNY", "money_scale": 100},
        "random_seed": 7,
        "output_path": "runs/run-1",
    }
    run_ref = run_backtest(backtest_payload, guard, threading.Event())
    replay_events = pq.read_table(tmp_path / replay_ref / "events.parquet").to_pylist()
    replay_signal_ids = [
        json.loads(event["payload_json"])["signal_id"]
        for event in replay_events
        if event["object_type"] == "trade_signal"
    ]
    run_signals = pq.read_table(tmp_path / run_ref / "trade_signals.parquet").to_pylist()
    assert replay_signal_ids == [value["signal_id"] for value in run_signals]
    fills = pq.read_table(tmp_path / run_ref / "fills.parquet").to_pylist()
    assert [value["bar_index"] for value in fills] == [6, 8]
    assert pq.read_table(tmp_path / run_ref / "trades.parquet").num_rows == 1
    log_events = [
        json.loads(line)
        for line in (tmp_path / run_ref / "log.ndjson").read_text(encoding="utf-8").splitlines()
    ]
    event_names = {event["event"] for event in log_events}
    assert {
        "strategy.state.changed",
        "strategy.stage.signal",
        "strategy.trade.signal",
        "backtest.order.recorded",
        "backtest.fill.recorded",
        "backtest.completed",
    } <= event_names
    signal_ids = {event["signal_id"] for event in log_events if "signal_id" in event}
    assert {fill["order_id"] for fill in fills} <= {
        event["order_id"] for event in log_events if event["event"] == "backtest.order.recorded"
    }
    assert {value["signal_id"] for value in run_signals} <= signal_ids
    assert all(
        {"source_file", "source_line", "source_function", "trace_id"} <= event.keys()
        for event in log_events
    )
    assert (tmp_path / run_ref / "_SUCCESS").is_file()
