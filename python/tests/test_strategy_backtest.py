from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pyarrow as pa
import pyarrow.parquet as pq

from tvbt.backtest import run_backtest
from tvbt.replay import generate_replay
from tvbt.storage.path_guard import PathGuard
from tvbt.strategy import (
    MA20RetestShort,
    StrategyBar,
    consolidation_reversion_definition,
    definition,
    downtrend_reversal_definition,
    first_centre_rotation_definition,
    fixed_level_centre_definition,
    run_strategy,
    third_point_migration_definition,
    trend_divergence_reversal_definition,
)


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


def test_fixed_level_centre_strategy_uses_confirmed_B3_and_shared_causal_events(
    tmp_path: Path, monkeypatch: object
) -> None:
    guard = PathGuard(tmp_path)
    dataset = tmp_path / "normalized" / "TEST.CHAN.5m" / "revision"
    dataset.mkdir(parents=True)
    pq.write_table(
        pa.table(
            {
                "bar_index": pa.array([0, 1, 2], type=pa.int64()),
                "timestamp_utc": pa.array([0, 300_000, 600_000], type=pa.int64()),
                "open_i64": pa.array([105, 108, 111], type=pa.int64()),
                "high_i64": pa.array([108, 114, 112], type=pa.int64()),
                "low_i64": pa.array([102, 107, 103], type=pa.int64()),
                "close_i64": pa.array([105, 112, 105], type=pa.int64()),
            }
        ),
        dataset / "bars.parquet",
    )
    (dataset / "meta.json").write_text('{"price":{"price_scale":1}}', encoding="utf-8")
    center_payload = {
        "object_id": "segment-center-1",
        "start_bar_index": 0,
        "known_at_bar_index": 0,
        "zg_i64": 110,
        "zd_i64": 100,
        "confirmed": True,
    }
    b3_payload = {
        "object_id": "buy-3-1",
        "signal_type": "buy_3",
        "reference_object_id": "segment-center-1",
        "known_at_bar_index": 1,
    }
    fake_events = [
        SimpleNamespace(
            known_at_bar_index=0,
            object_type="segment_zhongshu",
            object_id="segment-center-1",
            operation="upsert",
            payload_json=json.dumps(center_payload),
        ),
        SimpleNamespace(
            known_at_bar_index=1,
            object_type="trade_point",
            object_id="buy-3-1",
            operation="upsert",
            payload_json=json.dumps(b3_payload),
        ),
    ]
    monkeypatch.setattr(
        "tvbt.strategy.run_chan",
        lambda *args, **kwargs: (
            SimpleNamespace(emitter=SimpleNamespace(events=fake_events)),
            [],
            {},
        ),
    )
    algorithm = fixed_level_centre_definition()
    strategy_payload = {
        "dataset": {
            "dataset_id": "TEST.CHAN.5m",
            "data_revision": "sha256:" + "1" * 64,
            "bars_path": "normalized/TEST.CHAN.5m/revision/bars.parquet",
            "meta_path": "normalized/TEST.CHAN.5m/revision/meta.json",
        },
        "algorithm": {
            key: algorithm[key]
            for key in ("kind", "algorithm_id", "algorithm_version", "source_hash")
        },
        "parameters": {
            "checkpoint_interval": 1024,
            "allow_long": True,
            "allow_short": True,
        },
    }
    result = run_strategy(
        strategy_payload,
        guard,
        threading.Event(),
    )
    assert [value["state_to"] for value in result.strategy_states] == [
        "inside",
        "above_with_B3",
        "inside",
    ]
    assert [value["action"] for value in result.trade_signals] == ["open_long", "close_long"]
    assert [value["event_type"] for value in result.chart_events] == [
        "open_long",
        "close_long",
    ]
    assert all(
        event["known_at_bar_index"] >= 0 and event["event_seq"] == index + 1
        for index, event in enumerate(result.events)
    )
    prefix = run_strategy(strategy_payload, guard, threading.Event(), last_bar_index=1)
    assert prefix.events == [event for event in result.events if event["known_at_bar_index"] <= 1]


def test_trend_reversal_strategies_use_only_confirmed_standard_first_points(
    tmp_path: Path, monkeypatch: object
) -> None:
    guard = PathGuard(tmp_path)
    dataset = tmp_path / "normalized" / "TEST.REVERSAL.5m" / "revision"
    dataset.mkdir(parents=True)
    pq.write_table(
        pa.table(
            {
                "bar_index": pa.array([0, 1, 2, 3], type=pa.int64()),
                "timestamp_utc": pa.array([0, 300_000, 600_000, 900_000], type=pa.int64()),
                "open_i64": pa.array([100, 98, 95, 108], type=pa.int64()),
                "high_i64": pa.array([102, 100, 111, 110], type=pa.int64()),
                "low_i64": pa.array([97, 94, 93, 89], type=pa.int64()),
                "close_i64": pa.array([99, 96, 109, 90], type=pa.int64()),
            }
        ),
        dataset / "bars.parquet",
    )
    (dataset / "meta.json").write_text('{"price":{"price_scale":1}}', encoding="utf-8")

    def point(bar_index: int, object_id: str, signal_type: str, signal_class: str) -> object:
        return SimpleNamespace(
            known_at_bar_index=bar_index,
            object_type="trade_point",
            object_id=object_id,
            operation="upsert",
            payload_json=json.dumps(
                {
                    "object_id": object_id,
                    "signal_type": signal_type,
                    "signal_class": signal_class,
                    "known_at_bar_index": bar_index,
                    "confirmed": True,
                }
            ),
        )

    fake_events = [
        point(1, "class-buy-one", "class_buy_1", "class_like"),
        point(2, "trend-buy-one", "buy_1", "standard"),
        point(3, "trend-sell-one", "sell_1", "standard"),
    ]
    monkeypatch.setattr(
        "tvbt.strategy.run_chan",
        lambda *args, **kwargs: (
            SimpleNamespace(emitter=SimpleNamespace(events=fake_events)),
            [],
            {},
        ),
    )

    def payload_for(algorithm: dict[str, object]) -> dict[str, object]:
        return {
            "dataset": {
                "dataset_id": "TEST.REVERSAL.5m",
                "data_revision": "sha256:" + "2" * 64,
                "bars_path": "normalized/TEST.REVERSAL.5m/revision/bars.parquet",
                "meta_path": "normalized/TEST.REVERSAL.5m/revision/meta.json",
            },
            "algorithm": {
                key: algorithm[key]
                for key in ("kind", "algorithm_id", "algorithm_version", "source_hash")
            },
            "parameters": {"checkpoint_interval": 1024},
        }

    long_only_payload = payload_for(downtrend_reversal_definition())
    long_only = run_strategy(long_only_payload, guard, threading.Event())
    assert [value["action"] for value in long_only.trade_signals] == [
        "open_long",
        "close_long",
    ]
    assert [value["state_to"] for value in long_only.strategy_states] == [
        "long_after_B1",
        "short_after_S1",
    ]
    assert all("class-buy-one" not in event["payload_json"] for event in long_only.events)

    bidirectional_payload = payload_for(trend_divergence_reversal_definition())
    bidirectional = run_strategy(bidirectional_payload, guard, threading.Event())
    assert [value["action"] for value in bidirectional.trade_signals] == [
        "open_long",
        "close_long",
        "open_short",
    ]
    assert len(bidirectional.chart_events) == len(bidirectional.trade_signals)
    assert len(bidirectional.stage_signals) == len(bidirectional.strategy_states) == 2
    prefix = run_strategy(bidirectional_payload, guard, threading.Event(), last_bar_index=2)
    assert prefix.events == [
        event for event in bidirectional.events if event["known_at_bar_index"] <= 2
    ]


def test_consolidation_reversion_distinguishes_centre_return_and_third_point_conversion(
    tmp_path: Path, monkeypatch: object
) -> None:
    guard = PathGuard(tmp_path)
    dataset = tmp_path / "normalized" / "TEST.CONSREV.5m" / "revision"
    dataset.mkdir(parents=True)
    closes = [98, 95, 105, 115, 118]
    pq.write_table(
        pa.table(
            {
                "bar_index": pa.array(range(5), type=pa.int64()),
                "timestamp_utc": pa.array([index * 300_000 for index in range(5)]),
                "open_i64": pa.array(closes, type=pa.int64()),
                "high_i64": pa.array([value + 2 for value in closes], type=pa.int64()),
                "low_i64": pa.array([value - 2 for value in closes], type=pa.int64()),
                "close_i64": pa.array(closes, type=pa.int64()),
            }
        ),
        dataset / "bars.parquet",
    )
    (dataset / "meta.json").write_text('{"price":{"price_scale":1}}', encoding="utf-8")

    def event(bar_index: int, object_type: str, object_id: str, value: dict[str, object]) -> object:
        return SimpleNamespace(
            known_at_bar_index=bar_index,
            object_type=object_type,
            object_id=object_id,
            operation="upsert",
            payload_json=json.dumps({"object_id": object_id, **value}),
        )

    fake_events = [
        event(
            0,
            "segment_zhongshu",
            "segment-center-1",
            {"zd_i64": 100, "zg_i64": 110, "confirmed": True},
        ),
        event(
            0,
            "divergence",
            "bottom-cons-div",
            {
                "divergence_kind": "consolidation",
                "reference_object_id": "segment-center-1",
            },
        ),
        event(
            0,
            "divergence",
            "top-cons-div",
            {
                "divergence_kind": "consolidation",
                "reference_object_id": "segment-center-1",
            },
        ),
        event(
            1,
            "trade_point",
            "class-buy-1",
            {
                "signal_type": "class_buy_1",
                "signal_class": "class_like",
                "reference_object_id": "bottom-cons-div",
            },
        ),
        event(
            3,
            "trade_point",
            "class-sell-1",
            {
                "signal_type": "class_sell_1",
                "signal_class": "class_like",
                "reference_object_id": "top-cons-div",
            },
        ),
        event(
            4,
            "trade_point",
            "strict-buy-3",
            {
                "signal_type": "buy_3",
                "signal_class": "standard",
                "reference_object_id": "segment-center-1",
            },
        ),
    ]
    monkeypatch.setattr(
        "tvbt.strategy.run_chan",
        lambda *args, **kwargs: (
            SimpleNamespace(emitter=SimpleNamespace(events=fake_events)),
            [],
            {},
        ),
    )
    algorithm = consolidation_reversion_definition()
    strategy_payload = {
        "dataset": {
            "dataset_id": "TEST.CONSREV.5m",
            "data_revision": "sha256:" + "3" * 64,
            "bars_path": "normalized/TEST.CONSREV.5m/revision/bars.parquet",
            "meta_path": "normalized/TEST.CONSREV.5m/revision/meta.json",
        },
        "algorithm": {
            key: algorithm[key]
            for key in ("kind", "algorithm_id", "algorithm_version", "source_hash")
        },
        "parameters": {"checkpoint_interval": 1024},
    }
    result = run_strategy(strategy_payload, guard, threading.Event())
    assert [value["state_to"] for value in result.strategy_states] == [
        "reverting_up_to_centre",
        "returned_to_centre",
        "reverting_down_to_centre",
        "converted_to_B3",
    ]
    assert [value["action"] for value in result.trade_signals] == [
        "open_long",
        "close_long",
        "open_short",
        "close_short",
        "open_long",
    ]
    assert len(result.chart_events) == len(result.trade_signals)
    prefix = run_strategy(strategy_payload, guard, threading.Event(), last_bar_index=3)
    assert prefix.events == [event for event in result.events if event["known_at_bar_index"] <= 3]


def test_third_point_migration_hold_exits_on_new_centre_or_opposing_trend_divergence(
    tmp_path: Path, monkeypatch: object
) -> None:
    guard = PathGuard(tmp_path)
    dataset = tmp_path / "normalized" / "TEST.MIGHOLD.5m" / "revision"
    dataset.mkdir(parents=True)
    closes = [100, 112, 114, 116, 95, 90]
    pq.write_table(
        pa.table(
            {
                "bar_index": pa.array(range(6), type=pa.int64()),
                "timestamp_utc": pa.array([index * 300_000 for index in range(6)]),
                "open_i64": pa.array(closes, type=pa.int64()),
                "high_i64": pa.array([value + 2 for value in closes], type=pa.int64()),
                "low_i64": pa.array([value - 2 for value in closes], type=pa.int64()),
                "close_i64": pa.array(closes, type=pa.int64()),
            }
        ),
        dataset / "bars.parquet",
    )
    (dataset / "meta.json").write_text('{"price":{"price_scale":1}}', encoding="utf-8")

    def event(bar_index: int, object_type: str, object_id: str, value: dict[str, object]) -> object:
        return SimpleNamespace(
            known_at_bar_index=bar_index,
            object_type=object_type,
            object_id=object_id,
            operation="upsert",
            payload_json=json.dumps({"object_id": object_id, **value}),
        )

    fake_events = [
        event(0, "segment_zhongshu", "center-one", {"confirmed": True}),
        event(
            1,
            "trade_point",
            "strict-buy-three",
            {
                "signal_type": "buy_3",
                "signal_class": "standard",
                "reference_object_id": "center-one",
            },
        ),
        event(
            2,
            "trade_point",
            "class-sell-one",
            {"signal_type": "class_sell_1", "signal_class": "class_like"},
        ),
        event(3, "segment_zhongshu", "center-two", {"confirmed": True}),
        event(
            4,
            "trade_point",
            "strict-sell-three",
            {
                "signal_type": "sell_3",
                "signal_class": "standard",
                "reference_object_id": "center-two",
            },
        ),
        event(
            5,
            "trade_point",
            "standard-buy-one",
            {"signal_type": "buy_1", "signal_class": "standard"},
        ),
    ]
    monkeypatch.setattr(
        "tvbt.strategy.run_chan",
        lambda *args, **kwargs: (
            SimpleNamespace(emitter=SimpleNamespace(events=fake_events)),
            [],
            {},
        ),
    )
    algorithm = third_point_migration_definition()
    strategy_payload = {
        "dataset": {
            "dataset_id": "TEST.MIGHOLD.5m",
            "data_revision": "sha256:" + "4" * 64,
            "bars_path": "normalized/TEST.MIGHOLD.5m/revision/bars.parquet",
            "meta_path": "normalized/TEST.MIGHOLD.5m/revision/meta.json",
        },
        "algorithm": {
            key: algorithm[key]
            for key in ("kind", "algorithm_id", "algorithm_version", "source_hash")
        },
        "parameters": {"checkpoint_interval": 1024},
    }
    result = run_strategy(strategy_payload, guard, threading.Event())
    assert [value["state_to"] for value in result.strategy_states] == [
        "holding_upward_migration",
        "migration_hold_exited",
        "holding_downward_migration",
        "migration_hold_exited",
    ]
    assert [value["action"] for value in result.trade_signals] == [
        "open_long",
        "close_long",
        "open_short",
        "close_short",
    ]
    assert result.trade_signals[1]["reason_code"] == "NEW_SAME_LEVEL_CENTRE_CONFIRMED"
    assert result.trade_signals[3]["reason_code"] == "SAME_LEVEL_TREND_DIVERGENCE_CONFIRMED"
    prefix = run_strategy(strategy_payload, guard, threading.Event(), last_bar_index=4)
    assert prefix.events == [event for event in result.events if event["known_at_bar_index"] <= 4]


def test_first_centre_rotation_filters_later_same_direction_third_points(
    tmp_path: Path, monkeypatch: object
) -> None:
    guard = PathGuard(tmp_path)
    dataset = tmp_path / "normalized" / "TEST.ROTATE.5m" / "revision"
    dataset.mkdir(parents=True)
    closes = [100, 112, 110, 115, 95, 93, 90]
    pq.write_table(
        pa.table(
            {
                "bar_index": pa.array(range(7), type=pa.int64()),
                "timestamp_utc": pa.array([index * 300_000 for index in range(7)]),
                "open_i64": pa.array(closes, type=pa.int64()),
                "high_i64": pa.array([value + 2 for value in closes], type=pa.int64()),
                "low_i64": pa.array([value - 2 for value in closes], type=pa.int64()),
                "close_i64": pa.array(closes, type=pa.int64()),
            }
        ),
        dataset / "bars.parquet",
    )
    (dataset / "meta.json").write_text('{"price":{"price_scale":1}}', encoding="utf-8")

    def event(bar_index: int, object_type: str, object_id: str, value: dict[str, object]) -> object:
        return SimpleNamespace(
            known_at_bar_index=bar_index,
            object_type=object_type,
            object_id=object_id,
            operation="upsert",
            payload_json=json.dumps({"object_id": object_id, **value}),
        )

    def third(bar_index: int, object_id: str, signal_type: str, center_id: str) -> object:
        return event(
            bar_index,
            "trade_point",
            object_id,
            {
                "signal_type": signal_type,
                "signal_class": "standard",
                "reference_object_id": center_id,
            },
        )

    fake_events = [
        event(0, "segment_zhongshu", "center-one", {"confirmed": True}),
        third(1, "first-buy-three", "buy_3", "center-one"),
        event(
            2,
            "trade_point",
            "top-consolidation-divergence",
            {"signal_type": "class_sell_1", "signal_class": "class_like"},
        ),
        third(3, "later-buy-three", "buy_3", "center-later"),
        third(4, "first-sell-three", "sell_3", "center-later"),
        event(5, "segment_zhongshu", "center-down-new", {"confirmed": True}),
        third(6, "later-sell-three", "sell_3", "center-down-new"),
    ]
    monkeypatch.setattr(
        "tvbt.strategy.run_chan",
        lambda *args, **kwargs: (
            SimpleNamespace(emitter=SimpleNamespace(events=fake_events)),
            [],
            {},
        ),
    )
    algorithm = first_centre_rotation_definition()
    strategy_payload = {
        "dataset": {
            "dataset_id": "TEST.ROTATE.5m",
            "data_revision": "sha256:" + "5" * 64,
            "bars_path": "normalized/TEST.ROTATE.5m/revision/bars.parquet",
            "meta_path": "normalized/TEST.ROTATE.5m/revision/meta.json",
        },
        "algorithm": {
            key: algorithm[key]
            for key in ("kind", "algorithm_id", "algorithm_version", "source_hash")
        },
        "parameters": {"checkpoint_interval": 1024},
    }
    result = run_strategy(strategy_payload, guard, threading.Event())
    assert [value["action"] for value in result.trade_signals] == [
        "open_long",
        "close_long",
        "open_short",
        "close_short",
    ]
    assert result.trade_signals[1]["reason_code"] == "CONSOLIDATION_DIVERGENCE_CONFIRMED"
    assert [
        value["state_to"]
        for value in result.strategy_states
        if value["reason_code"] == "LATER_CENTRE_THIRD_POINT_FILTERED"
    ] == ["later_centre_BUY_3_filtered", "later_centre_SELL_3_filtered"]
    assert len(result.chart_events) == len(result.trade_signals)
    prefix = run_strategy(strategy_payload, guard, threading.Event(), last_bar_index=5)
    assert prefix.events == [event for event in result.events if event["known_at_bar_index"] <= 5]


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
    log_lines = (tmp_path / run_ref / "log.ndjson").read_text(encoding="utf-8").splitlines()
    assert all(line.startswith("[") and "][INFO][tvbt/backtest.py][" in line for line in log_lines)
    event_names = {line.split("] ", 1)[1].split(" ", 1)[0] for line in log_lines}
    assert {
        "strategy.state.changed",
        "strategy.stage.signal",
        "strategy.trade.signal",
        "backtest.order.recorded",
        "backtest.fill.recorded",
        "backtest.completed",
    } <= event_names
    log_text = "\n".join(log_lines)
    assert all(f'"order_id":"{fill["order_id"]}"' in log_text for fill in fills)
    assert all(f'"signal_id":"{value["signal_id"]}"' in log_text for value in run_signals)
    assert '"trace_id":"trace-1"' in log_text
    assert (tmp_path / run_ref / "_SUCCESS").is_file()


def test_chan_strategies_run_on_real_aol9_prefix(tmp_path: Path) -> None:
    guard = PathGuard(tmp_path)
    dataset = tmp_path / "normalized" / "SHFE.AOL9.5m" / "revision"
    dataset.mkdir(parents=True)
    sample = Path(__file__).parents[2] / "samples" / "30#AOL9.txt"
    rows = sample.read_text(encoding="gb18030").splitlines()[2:5002]
    values = [[field.strip() for field in row.split(",")] for row in rows]
    pq.write_table(
        pa.table(
            {
                "bar_index": pa.array(range(len(values)), type=pa.int64()),
                "timestamp_utc": pa.array(
                    [1_700_000_000_000 + index * 300_000 for index in range(len(values))],
                    type=pa.int64(),
                ),
                "open_i64": pa.array([int(value[2]) for value in values], type=pa.int64()),
                "high_i64": pa.array([int(value[3]) for value in values], type=pa.int64()),
                "low_i64": pa.array([int(value[4]) for value in values], type=pa.int64()),
                "close_i64": pa.array([int(value[5]) for value in values], type=pa.int64()),
            }
        ),
        dataset / "bars.parquet",
    )
    (dataset / "meta.json").write_text(
        '{"price":{"price_scale":1,"tick_size_i64":1}}', encoding="utf-8"
    )
    algorithm = fixed_level_centre_definition()
    result = run_strategy(
        {
            "dataset": {
                "dataset_id": "SHFE.AOL9.5m",
                "data_revision": "sha256:" + "4" * 64,
                "bars_path": "normalized/SHFE.AOL9.5m/revision/bars.parquet",
                "meta_path": "normalized/SHFE.AOL9.5m/revision/meta.json",
            },
            "algorithm": {
                key: algorithm[key]
                for key in ("kind", "algorithm_id", "algorithm_version", "source_hash")
            },
            "parameters": {
                "checkpoint_interval": 1024,
                "allow_long": True,
                "allow_short": True,
            },
        },
        guard,
        threading.Event(),
    )
    assert result.strategy_states
    assert len(result.stage_signals) == len(result.strategy_states)
    assert all(
        event["known_at_bar_index"] >= 0 and event["event_seq"] == index + 1
        for index, event in enumerate(result.events)
    )
    assert {value["state_to"] for value in result.strategy_states} <= {
        "inside",
        "below_without_S3",
        "below_with_S3",
        "above_without_B3",
        "above_with_B3",
    }
    for algorithm in (
        downtrend_reversal_definition(),
        trend_divergence_reversal_definition(),
        consolidation_reversion_definition(),
        third_point_migration_definition(),
        first_centre_rotation_definition(),
    ):
        strategy_result = run_strategy(
            {
                "dataset": {
                    "dataset_id": "SHFE.AOL9.5m",
                    "data_revision": "sha256:" + "4" * 64,
                    "bars_path": "normalized/SHFE.AOL9.5m/revision/bars.parquet",
                    "meta_path": "normalized/SHFE.AOL9.5m/revision/meta.json",
                },
                "algorithm": {
                    key: algorithm[key]
                    for key in ("kind", "algorithm_id", "algorithm_version", "source_hash")
                },
                "parameters": {"checkpoint_interval": 1024},
            },
            guard,
            threading.Event(),
        )
        assert len(strategy_result.bars) == 5000
        assert all(
            event["known_at_bar_index"] >= 0 and event["event_seq"] == index + 1
            for index, event in enumerate(strategy_result.events)
        )
