from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from tvbt.backtest import _trade_signal_quantity, run_backtest
from tvbt.replay import generate_replay
from tvbt.storage.path_guard import PathGuard
from tvbt.strategy import (
    MA20RetestShort,
    StrategyBar,
    bottom_top_construction_definition,
    centre_oscillation_spread_definition,
    consolidation_reversion_definition,
    definition,
    downtrend_reversal_definition,
    first_centre_rotation_definition,
    fixed_level_centre_definition,
    run_strategy,
    same_level_decomposition_program_definition,
    second_buy_only_definition,
    target_level_rebound_segmented_operation_definition,
    third_buy_only_definition,
    third_point_migration_definition,
    three_level_complete_classification_definition,
    trend_divergence_reversal_definition,
)


def test_center_consumers_publish_new_algorithm_identity() -> None:
    """测试闭区间点中枢语义不会复用旧策略定义。"""
    assert fixed_level_centre_definition()["algorithm_version"] == "1.2.0"
    assert consolidation_reversion_definition()["algorithm_version"] == "1.3.0"
    assert third_point_migration_definition()["algorithm_version"] == "1.2.0"
    assert first_centre_rotation_definition()["algorithm_version"] == "1.3.0"
    assert downtrend_reversal_definition()["algorithm_version"] == "1.1.0"
    assert trend_divergence_reversal_definition()["algorithm_version"] == "1.1.0"
    assert second_buy_only_definition()["algorithm_version"] == "1.1.0"
    third_buy = third_buy_only_definition()
    assert third_buy["algorithm_version"] == "1.1.0"
    assert third_buy["parameter_schema"]["properties"]["first_center_quantity"]["default"] == 2
    assert third_buy["parameter_schema"]["properties"]["late_center_quantity"]["default"] == 1
    oscillation = centre_oscillation_spread_definition()
    assert oscillation["algorithm_version"] == "1.1.0"
    assert oscillation["parameter_schema"]["properties"]["max_entries_per_center"]["default"] == 4
    same_level = same_level_decomposition_program_definition()
    assert same_level["algorithm_version"] == "1.0.0"
    assert same_level["parameter_schema"]["properties"]["odd_direction_is_down"]["default"]
    assert same_level["parameter_schema"]["properties"]["operation_quantity"]["default"] == 1
    three_level = three_level_complete_classification_definition()
    assert three_level["algorithm_version"] == "1.0.0"
    assert three_level["name"] == "三层级完全分类"
    assert three_level["parameter_schema"]["properties"]["level_graph_profile_id"]["default"] == 1
    assert three_level["parameter_schema"]["properties"]["can_handle_high_change_candidate"][
        "default"
    ]
    segmented = target_level_rebound_segmented_operation_definition()
    assert segmented["algorithm_version"] == "1.0.0"
    assert segmented["name"] == "目标级别反弹/回调分段操作"
    assert segmented["parameter_schema"]["properties"]["operation_quantity"]["default"] == 2
    assert (
        segmented["parameter_schema"]["properties"]["partial_take_profit_quantity"]["default"] == 1
    )
    construction = bottom_top_construction_definition()
    assert construction["algorithm_version"] == "1.0.0"
    assert construction["name"] == "底部/顶部构造状态机"
    assert (
        construction["parameter_schema"]["properties"]["coarse_effective_hold_bars"]["default"] == 1
    )


def test_trade_signal_quantity_defaults_to_one_and_rejects_invalid_values() -> None:
    assert _trade_signal_quantity({}) == 1
    assert _trade_signal_quantity({"quantity": 3}) == 3
    for value in (0, -1, True, 1.5, "2"):
        with pytest.raises(ValueError, match="positive integer"):
            _trade_signal_quantity({"quantity": value})


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


def test_second_buy_only_hands_strongest_B2_to_B3_and_backtests_two_contracts(
    tmp_path: Path, monkeypatch: object
) -> None:
    guard = PathGuard(tmp_path)
    dataset = tmp_path / "normalized" / "TEST.B2.5m" / "revision"
    dataset.mkdir(parents=True)
    opens = [90, 105, 101, 103, 118, 117]
    closes = [95, 110, 100, 120, 115, 116]
    pq.write_table(
        pa.table(
            {
                "bar_index": pa.array(range(6), type=pa.int64()),
                "timestamp_utc": pa.array([index * 300_000 for index in range(6)]),
                "open_i64": pa.array(opens, type=pa.int64()),
                "high_i64": pa.array([value + 2 for value in closes], type=pa.int64()),
                "low_i64": pa.array([value - 2 for value in closes], type=pa.int64()),
                "close_i64": pa.array(closes, type=pa.int64()),
            }
        ),
        dataset / "bars.parquet",
    )
    (dataset / "meta.json").write_text(
        '{"price":{"price_scale":1,"tick_size_i64":1}}', encoding="utf-8"
    )

    def event(bar_index: int, object_type: str, object_id: str, value: dict[str, object]) -> object:
        return SimpleNamespace(
            known_at_bar_index=bar_index,
            object_type=object_type,
            object_id=object_id,
            operation="upsert",
            payload_json=json.dumps({"object_id": object_id, "confirmed": True, **value}),
        )

    def segment(
        known_at: int,
        object_id: str,
        start: int,
        end: int,
        start_price: int,
        end_price: int,
        direction: str,
    ) -> object:
        return event(
            known_at,
            "segment",
            object_id,
            {
                "start_bar_index": start,
                "end_bar_index": end,
                "start_price_i64": start_price,
                "end_price_i64": end_price,
                "direction": direction,
            },
        )

    fake_events = [
        event(
            0,
            "divergence",
            "bottom-trend-divergence",
            {
                "bar_index": 0,
                "signal_type": "bottom_divergence",
                "divergence_kind": "trend",
                "reference_object_id": "center-one",
            },
        ),
        segment(1, "first-rebound", 0, 1, 90, 110, "up"),
        segment(2, "first-retest", 1, 2, 110, 100, "down"),
        event(
            2,
            "trade_point",
            "strongest-buy-two",
            {
                "bar_index": 2,
                "price_i64": 100,
                "signal_type": "buy_2",
                "signal_class": "standard",
                "strength": "strongest",
                "reference_object_id": "bottom-trend-divergence",
            },
        ),
        event(
            2,
            "trade_point",
            "same-point-buy-three",
            {
                "bar_index": 2,
                "price_i64": 100,
                "signal_type": "buy_3",
                "signal_class": "standard",
                "reference_object_id": "center-one",
            },
        ),
        segment(3, "nondivergent-followthrough", 2, 3, 100, 120, "up"),
        event(
            4,
            "trade_point",
            "standard-sell-one",
            {
                "bar_index": 4,
                "price_i64": 115,
                "signal_type": "sell_1",
                "signal_class": "standard",
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
    algorithm = second_buy_only_definition()
    parameters = {
        "checkpoint_interval": 1024,
        "allow_strongest": True,
        "allow_normal": True,
        "allow_weakest": True,
        "strongest_quantity": 2,
        "normal_quantity": 2,
        "weakest_quantity": 1,
    }
    facts = {
        "dataset": {
            "dataset_id": "TEST.B2.5m",
            "data_revision": "sha256:" + "6" * 64,
            "bars_path": "normalized/TEST.B2.5m/revision/bars.parquet",
            "meta_path": "normalized/TEST.B2.5m/revision/meta.json",
        },
        "algorithm": {
            key: algorithm[key]
            for key in ("kind", "algorithm_id", "algorithm_version", "source_hash")
        },
        "parameters": parameters,
        "range": {"warmup_from_bar_index": 0, "from_bar_index": 0, "to_bar_index": 5},
    }
    result = run_strategy(facts, guard, threading.Event())
    assert [value["state_to"] for value in result.strategy_states] == [
        "long_after_B2_strongest",
        "handed_off_B3_trend",
        "exited_standard_sell_point",
    ]
    assert [value["quantity"] for value in result.trade_signals] == [2, 2]
    assert [value["event_type"] for value in result.chart_events] == [
        "open_long",
        "handoff_to_B3_trend",
        "close_long",
    ]
    assert result.chart_events[0]["bar_index"] == 2
    assert result.chart_events[0]["known_at_bar_index"] == 2
    prefix = run_strategy(facts, guard, threading.Event(), last_bar_index=3)
    assert prefix.events == [value for value in result.events if value["known_at_bar_index"] <= 3]

    # A close signal that defaults to one contract must still flatten the
    # complete two-contract net position.
    result.trade_signals[1]["quantity"] = 1
    monkeypatch.setattr("tvbt.backtest.run_strategy", lambda *args, **kwargs: result)
    run_ref = run_backtest(
        {
            **facts,
            "run_id": "run-B2",
            "run_signature": "sha256:" + "7" * 64,
            "trace_id": "trace-B2",
            "execution": {
                "signal_timing": "bar_close",
                "fill_timing": "next_bar_open",
                "commission": {
                    "mode": "fixed_per_contract",
                    "amount_i64": 100,
                    "money_scale": 100,
                },
                "slippage": {"mode": "ticks", "value": 1},
                "contract_multiplier": 1,
                "margin_ratio": 0.1,
                "intrabar_conflict_rule": "worst_case",
            },
            "capital": {
                "initial_cash_i64": 1_000_000,
                "currency": "CNY",
                "money_scale": 100,
            },
            "random_seed": 7,
            "output_path": "runs/run-B2",
        },
        guard,
        threading.Event(),
    )
    run_dir = tmp_path / run_ref
    assert [
        value["quantity"] for value in pq.read_table(run_dir / "fills.parquet").to_pylist()
    ] == [
        2,
        2,
    ]
    trade = pq.read_table(run_dir / "trades.parquet").to_pylist()[0]
    assert trade["quantity"] == 2
    assert trade["gross_pnl_i64"] == 2_400
    assert trade["commission_i64"] == 400
    assert trade["slippage_i64"] == 400
    assert trade["net_pnl_i64"] == 2_000
    position = pq.read_table(run_dir / "positions.parquet").to_pylist()[3]
    assert position["quantity"] == 2
    assert position["unrealized_pnl_i64"] == 3_200
    equity = pq.read_table(run_dir / "equity.parquet").to_pylist()[3]
    assert equity["margin_i64"] == 2_400


def test_second_buy_only_caps_weak_entry_and_exits_failed_or_divergent_followthrough(
    tmp_path: Path, monkeypatch: object
) -> None:
    guard = PathGuard(tmp_path)
    dataset = tmp_path / "normalized" / "TEST.B2.BRANCHES.5m" / "revision"
    dataset.mkdir(parents=True)
    closes = [90, 110, 100, 109, 100, 120, 112, 125, 124]
    pq.write_table(
        pa.table(
            {
                "bar_index": pa.array(range(9), type=pa.int64()),
                "timestamp_utc": pa.array([index * 300_000 for index in range(9)]),
                "open_i64": pa.array(closes, type=pa.int64()),
                "high_i64": pa.array([value + 1 for value in closes], type=pa.int64()),
                "low_i64": pa.array([value - 1 for value in closes], type=pa.int64()),
                "close_i64": pa.array(closes, type=pa.int64()),
            }
        ),
        dataset / "bars.parquet",
    )
    (dataset / "meta.json").write_text('{"price":{"price_scale":1}}', encoding="utf-8")

    def event(known_at: int, object_type: str, object_id: str, value: dict[str, object]) -> object:
        return SimpleNamespace(
            known_at_bar_index=known_at,
            object_type=object_type,
            object_id=object_id,
            operation="upsert",
            payload_json=json.dumps({"object_id": object_id, "confirmed": True, **value}),
        )

    def segment(
        known_at: int,
        object_id: str,
        start: int,
        end: int,
        start_price: int,
        end_price: int,
        direction: str,
    ) -> object:
        return event(
            known_at,
            "segment",
            object_id,
            {
                "start_bar_index": start,
                "end_bar_index": end,
                "start_price_i64": start_price,
                "end_price_i64": end_price,
                "direction": direction,
            },
        )

    def buy_two(
        known_at: int, object_id: str, endpoint: int, price: int, strength: str, source: str
    ) -> object:
        return event(
            known_at,
            "trade_point",
            object_id,
            {
                "bar_index": endpoint,
                "price_i64": price,
                "signal_type": "buy_2",
                "signal_class": "standard",
                "strength": strength,
                "reference_object_id": source,
            },
        )

    fake_events = [
        event(
            0,
            "divergence",
            "weak-origin",
            {
                "bar_index": 0,
                "signal_type": "bottom_divergence",
                "divergence_kind": "trend",
                "reference_object_id": "center-weak",
            },
        ),
        segment(1, "weak-rebound", 0, 1, 90, 110, "up"),
        segment(2, "weak-retest", 1, 2, 110, 100, "down"),
        buy_two(2, "weak-buy-two", 2, 100, "weakest", "weak-origin"),
        segment(3, "failed-followthrough", 2, 3, 100, 109, "up"),
        event(
            4,
            "trade_point",
            "class-buy-two-ignored",
            {
                "bar_index": 4,
                "price_i64": 100,
                "signal_type": "class_buy_2",
                "signal_class": "class_like",
                "strength": "normal",
            },
        ),
        event(
            4,
            "divergence",
            "normal-origin",
            {
                "bar_index": 4,
                "signal_type": "bottom_divergence",
                "divergence_kind": "trend",
                "reference_object_id": "center-normal",
            },
        ),
        segment(5, "normal-rebound", 4, 5, 100, 120, "up"),
        segment(6, "normal-retest", 5, 6, 120, 112, "down"),
        buy_two(6, "normal-buy-two", 6, 112, "normal", "normal-origin"),
        segment(7, "divergent-followthrough", 6, 7, 112, 125, "up"),
        event(
            7,
            "divergence",
            "followthrough-top-divergence",
            {
                "bar_index": 7,
                "signal_type": "top_divergence",
                "divergence_kind": "consolidation",
                "reference_object_id": "center-normal",
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
    algorithm = second_buy_only_definition()
    payload = {
        "dataset": {
            "dataset_id": "TEST.B2.BRANCHES.5m",
            "data_revision": "sha256:" + "8" * 64,
            "bars_path": "normalized/TEST.B2.BRANCHES.5m/revision/bars.parquet",
            "meta_path": "normalized/TEST.B2.BRANCHES.5m/revision/meta.json",
        },
        "algorithm": {
            key: algorithm[key]
            for key in ("kind", "algorithm_id", "algorithm_version", "source_hash")
        },
        "parameters": {
            "checkpoint_interval": 1024,
            "allow_strongest": True,
            "allow_normal": True,
            "allow_weakest": True,
            "strongest_quantity": 3,
            "normal_quantity": 3,
            "weakest_quantity": 1,
        },
    }
    result = run_strategy(payload, guard, threading.Event())
    assert [value["quantity"] for value in result.trade_signals] == [1, 1, 3, 3]
    assert [
        value["reason_code"] for value in result.trade_signals if value["action"] == "close_long"
    ] == ["NEXT_UP_FAILED_NEW_HIGH", "FOLLOWTHROUGH_CONSOLIDATION_DIVERGENCE"]
    assert all(
        value["reference_object_id"] != "class-buy-two-ignored" for value in result.strategy_states
    )

    invalid = {**payload, "parameters": {**payload["parameters"], "weakest_quantity": 4}}
    with pytest.raises(ValueError, match="weakest_quantity"):
        run_strategy(invalid, guard, threading.Event())


def test_third_buy_only_prefers_first_center_and_holds_new_center_until_trend_divergence(
    tmp_path: Path, monkeypatch: object
) -> None:
    guard = PathGuard(tmp_path)
    dataset = tmp_path / "normalized" / "TEST.B3.HOLD.5m" / "revision"
    dataset.mkdir(parents=True)
    closes = [90, 98, 100, 120, 100, 125, 120, 122, 130, 128]
    pq.write_table(
        pa.table(
            {
                "bar_index": pa.array(range(10), type=pa.int64()),
                "timestamp_utc": pa.array([index * 300_000 for index in range(10)]),
                "open_i64": pa.array(closes, type=pa.int64()),
                "high_i64": pa.array([value + 2 for value in closes], type=pa.int64()),
                "low_i64": pa.array([value - 2 for value in closes], type=pa.int64()),
                "close_i64": pa.array(closes, type=pa.int64()),
                "volume": pa.array([500] * 10, type=pa.int64()),
            }
        ),
        dataset / "bars.parquet",
    )
    (dataset / "meta.json").write_text('{"price":{"price_scale":1}}', encoding="utf-8")

    def event(known_at: int, object_type: str, object_id: str, value: dict[str, object]) -> object:
        return SimpleNamespace(
            known_at_bar_index=known_at,
            object_type=object_type,
            object_id=object_id,
            operation="upsert",
            payload_json=json.dumps({"object_id": object_id, "confirmed": True, **value}),
        )

    def segment(
        known_at: int,
        object_id: str,
        start: int,
        end: int,
        start_price: int,
        end_price: int,
        direction: str,
    ) -> object:
        return event(
            known_at,
            "segment",
            object_id,
            {
                "start_bar_index": start,
                "end_bar_index": end,
                "start_price_i64": start_price,
                "end_price_i64": end_price,
                "direction": direction,
            },
        )

    fake_events = [
        event(
            2,
            "segment_zhongshu",
            "first-up-center",
            {
                "start_bar_index": 0,
                "end_bar_index": 2,
                "zd_i64": 90,
                "zg_i64": 100,
                "dd_i64": 80,
                "gg_i64": 110,
                "analysis_level": "segment",
                "leave_direction": "up",
            },
        ),
        segment(3, "B3-departure", 2, 3, 100, 120, "up"),
        segment(4, "B3-first-return", 3, 4, 120, 100, "down"),
        event(
            4,
            "trade_point",
            "first-center-B3",
            {
                "bar_index": 4,
                "price_i64": 100,
                "signal_type": "buy_3",
                "signal_class": "standard",
                "reference_object_id": "first-up-center",
            },
        ),
        segment(5, "B3-followthrough", 4, 5, 100, 125, "up"),
        event(
            7,
            "segment_zhongshu",
            "second-up-center",
            {
                "start_bar_index": 5,
                "end_bar_index": 7,
                "zd_i64": 112,
                "zg_i64": 118,
                "dd_i64": 111,
                "gg_i64": 130,
                "analysis_level": "segment",
                "leave_direction": "up",
            },
        ),
        event(
            8,
            "divergence",
            "up-trend-top-divergence",
            {
                "bar_index": 8,
                "price_i64": 130,
                "signal_type": "top_divergence",
                "divergence_kind": "trend",
                "reference_object_id": "second-up-center",
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
    algorithm = third_buy_only_definition()
    payload = {
        "dataset": {
            "dataset_id": "TEST.B3.HOLD.5m",
            "data_revision": "sha256:" + "9" * 64,
            "bars_path": "normalized/TEST.B3.HOLD.5m/revision/bars.parquet",
            "meta_path": "normalized/TEST.B3.HOLD.5m/revision/meta.json",
        },
        "algorithm": {
            key: algorithm[key]
            for key in ("kind", "algorithm_id", "algorithm_version", "source_hash")
        },
        "parameters": {
            "checkpoint_interval": 1024,
            "allow_late_center": True,
            "first_center_quantity": 2,
            "late_center_quantity": 1,
            "minimum_entry_volume": 100,
        },
    }
    result = run_strategy(payload, guard, threading.Event())
    assert [value["state_to"] for value in result.strategy_states] == [
        "long_after_first_center_B3",
        "holding_after_B3_followthrough",
        "holding_new_center_without_trend_divergence",
        "exited_trend_divergence",
    ]
    assert [value["quantity"] for value in result.trade_signals] == [2, 2]
    assert result.trade_signals[0]["center_ordinal_in_trend"] == 1
    assert result.trade_signals[0]["priority"] == "high"
    assert [value["event_type"] for value in result.chart_events] == [
        "open_long",
        "hold_after_B3",
        "hold_new_center",
        "close_long",
    ]
    prefix = run_strategy(payload, guard, threading.Event(), last_bar_index=7)
    assert prefix.events == [value for value in result.events if value["known_at_bar_index"] <= 7]


def test_third_buy_only_penalizes_late_center_and_covers_both_followthrough_failures(
    tmp_path: Path, monkeypatch: object
) -> None:
    guard = PathGuard(tmp_path)
    dataset = tmp_path / "normalized" / "TEST.B3.BRANCHES.5m" / "revision"
    dataset.mkdir(parents=True)
    closes = [90, 100, 120, 100, 119, 130, 150, 130, 155, 150]
    volumes = [500, 500, 500, 500, 500, 500, 500, 50, 500, 500]
    pq.write_table(
        pa.table(
            {
                "bar_index": pa.array(range(10), type=pa.int64()),
                "timestamp_utc": pa.array([index * 300_000 for index in range(10)]),
                "open_i64": pa.array(closes, type=pa.int64()),
                "high_i64": pa.array([value + 2 for value in closes], type=pa.int64()),
                "low_i64": pa.array([value - 2 for value in closes], type=pa.int64()),
                "close_i64": pa.array(closes, type=pa.int64()),
                "volume": pa.array(volumes, type=pa.int64()),
            }
        ),
        dataset / "bars.parquet",
    )
    (dataset / "meta.json").write_text('{"price":{"price_scale":1}}', encoding="utf-8")

    def event(known_at: int, object_type: str, object_id: str, value: dict[str, object]) -> object:
        return SimpleNamespace(
            known_at_bar_index=known_at,
            object_type=object_type,
            object_id=object_id,
            operation="upsert",
            payload_json=json.dumps({"object_id": object_id, "confirmed": True, **value}),
        )

    def center(
        known_at: int,
        object_id: str,
        start: int,
        end: int,
        zd: int,
        zg: int,
        dd: int,
        gg: int,
    ) -> object:
        return event(
            known_at,
            "segment_zhongshu",
            object_id,
            {
                "start_bar_index": start,
                "end_bar_index": end,
                "zd_i64": zd,
                "zg_i64": zg,
                "dd_i64": dd,
                "gg_i64": gg,
                "analysis_level": "segment",
                "leave_direction": "up",
            },
        )

    def segment(
        known_at: int,
        object_id: str,
        start: int,
        end: int,
        start_price: int,
        end_price: int,
        direction: str,
    ) -> object:
        return event(
            known_at,
            "segment",
            object_id,
            {
                "start_bar_index": start,
                "end_bar_index": end,
                "start_price_i64": start_price,
                "end_price_i64": end_price,
                "direction": direction,
            },
        )

    def b3(known_at: int, object_id: str, endpoint: int, price: int, center_id: str) -> object:
        return event(
            known_at,
            "trade_point",
            object_id,
            {
                "bar_index": endpoint,
                "price_i64": price,
                "signal_type": "buy_3",
                "signal_class": "standard",
                "reference_object_id": center_id,
            },
        )

    fake_events = [
        center(1, "center-one", 0, 1, 90, 100, 80, 110),
        event(
            1,
            "trade_point",
            "candidate-B3-ignored",
            {
                "bar_index": 1,
                "price_i64": 100,
                "signal_type": "buy_3",
                "signal_class": "standard",
                "confirmed": False,
            },
        ),
        event(
            1,
            "trade_point",
            "class-B3-ignored",
            {
                "bar_index": 1,
                "price_i64": 100,
                "signal_type": "class_buy_3",
                "signal_class": "class_like",
            },
        ),
        segment(2, "first-departure", 1, 2, 100, 120, "up"),
        segment(3, "first-return", 2, 3, 120, 100, "down"),
        b3(3, "first-B3", 3, 100, "center-one"),
        segment(4, "failed-followthrough", 3, 4, 100, 119, "up"),
        event(
            4,
            "divergence",
            "failed-followthrough-divergence",
            {
                "bar_index": 4,
                "price_i64": 119,
                "signal_type": "top_divergence",
                "divergence_kind": "consolidation",
            },
        ),
        center(5, "center-two", 4, 5, 125, 130, 121, 140),
        segment(6, "late-departure", 5, 6, 130, 150, "up"),
        segment(7, "late-return", 6, 7, 150, 130, "down"),
        b3(7, "late-B3", 7, 130, "center-two"),
        segment(8, "late-followthrough", 7, 8, 130, 155, "up"),
        event(
            8,
            "divergence",
            "late-followthrough-divergence",
            {
                "bar_index": 8,
                "price_i64": 155,
                "signal_type": "top_divergence",
                "divergence_kind": "consolidation",
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
    algorithm = third_buy_only_definition()
    base_parameters = {
        "checkpoint_interval": 1024,
        "allow_late_center": True,
        "first_center_quantity": 2,
        "late_center_quantity": 1,
        "minimum_entry_volume": 0,
    }
    payload = {
        "dataset": {
            "dataset_id": "TEST.B3.BRANCHES.5m",
            "data_revision": "sha256:" + "a" * 64,
            "bars_path": "normalized/TEST.B3.BRANCHES.5m/revision/bars.parquet",
            "meta_path": "normalized/TEST.B3.BRANCHES.5m/revision/meta.json",
        },
        "algorithm": {
            key: algorithm[key]
            for key in ("kind", "algorithm_id", "algorithm_version", "source_hash")
        },
        "parameters": base_parameters,
    }
    result = run_strategy(payload, guard, threading.Event())
    assert [value["quantity"] for value in result.trade_signals] == [2, 2, 1, 1]
    assert [
        value["reason_code"] for value in result.trade_signals if value["action"] == "close_long"
    ] == [
        "B3_FOLLOWTHROUGH_FAILED_NEW_HIGH",
        "B3_FOLLOWTHROUGH_CONSOLIDATION_DIVERGENCE",
    ]
    late_entry = next(
        value for value in result.trade_signals if value["reference_object_id"] == "late-B3"
    )
    assert late_entry["center_ordinal_in_trend"] == 2
    assert late_entry["priority"] == "penalized"
    assert all(
        value["reference_object_id"] not in {"candidate-B3-ignored", "class-B3-ignored"}
        for value in result.strategy_states
    )

    liquidity_filtered = run_strategy(
        {
            **payload,
            "parameters": {**base_parameters, "minimum_entry_volume": 100},
        },
        guard,
        threading.Event(),
    )
    assert [value["quantity"] for value in liquidity_filtered.trade_signals] == [2, 2]
    assert any(
        value["reason_code"] == "B3_LIQUIDITY_FILTER_BLOCKED"
        and value["reference_object_id"] == "late-B3"
        for value in liquidity_filtered.strategy_states
    )

    late_disabled = run_strategy(
        {
            **payload,
            "parameters": {**base_parameters, "allow_late_center": False},
        },
        guard,
        threading.Event(),
    )
    assert any(
        value["reason_code"] == "LATE_CENTER_B3_DISABLED"
        and value["reference_object_id"] == "late-B3"
        for value in late_disabled.strategy_states
    )
    invalid = {
        **payload,
        "parameters": {**base_parameters, "late_center_quantity": 3},
    }
    with pytest.raises(ValueError, match="late_center_quantity"):
        run_strategy(invalid, guard, threading.Event())


def test_third_buy_only_exits_on_S3_center_return_or_source_revision_without_reentry(
    tmp_path: Path, monkeypatch: object
) -> None:
    guard = PathGuard(tmp_path)
    dataset = tmp_path / "normalized" / "TEST.B3.INVALIDATION.5m" / "revision"
    dataset.mkdir(parents=True)
    closes = [90, 100, 120, 100, 125, 95, 100]
    pq.write_table(
        pa.table(
            {
                "bar_index": pa.array(range(7), type=pa.int64()),
                "timestamp_utc": pa.array([index * 300_000 for index in range(7)]),
                "open_i64": pa.array(closes, type=pa.int64()),
                "high_i64": pa.array([value + 2 for value in closes], type=pa.int64()),
                "low_i64": pa.array([value - 2 for value in closes], type=pa.int64()),
                "close_i64": pa.array(closes, type=pa.int64()),
                "volume": pa.array([500] * 7, type=pa.int64()),
            }
        ),
        dataset / "bars.parquet",
    )
    (dataset / "meta.json").write_text('{"price":{"price_scale":1}}', encoding="utf-8")

    def event(
        known_at: int,
        object_type: str,
        object_id: str,
        value: dict[str, object],
        operation: str = "upsert",
    ) -> object:
        return SimpleNamespace(
            known_at_bar_index=known_at,
            object_type=object_type,
            object_id=object_id,
            operation=operation,
            payload_json=(
                "{}"
                if operation == "delete"
                else json.dumps({"object_id": object_id, "confirmed": True, **value})
            ),
        )

    def segment(
        known_at: int,
        object_id: str,
        start: int,
        end: int,
        start_price: int,
        end_price: int,
        direction: str,
    ) -> object:
        return event(
            known_at,
            "segment",
            object_id,
            {
                "start_bar_index": start,
                "end_bar_index": end,
                "start_price_i64": start_price,
                "end_price_i64": end_price,
                "direction": direction,
            },
        )

    center = event(
        1,
        "segment_zhongshu",
        "source-center",
        {
            "start_bar_index": 0,
            "end_bar_index": 1,
            "zd_i64": 90,
            "zg_i64": 100,
            "dd_i64": 80,
            "gg_i64": 110,
            "analysis_level": "segment",
            "leave_direction": "up",
        },
    )
    b3_payload = {
        "bar_index": 3,
        "price_i64": 100,
        "signal_type": "buy_3",
        "signal_class": "standard",
        "reference_object_id": "source-center",
    }
    base_events = [
        center,
        segment(2, "departure", 1, 2, 100, 120, "up"),
        segment(3, "first-return", 2, 3, 120, 100, "down"),
        event(3, "trade_point", "source-B3", b3_payload),
        segment(4, "followthrough", 3, 4, 100, 125, "up"),
    ]
    scenarios = {
        "STANDARD_S3_INVALIDATED_B3_HOLD": [
            event(
                5,
                "trade_point",
                "invalidating-S3",
                {
                    "bar_index": 5,
                    "price_i64": 95,
                    "signal_type": "sell_3",
                    "signal_class": "standard",
                },
            )
        ],
        "CONFIRMED_RETURN_ENTERED_SOURCE_CENTER": [
            segment(5, "return-into-center", 4, 5, 125, 100, "down")
        ],
        "B3_SOURCE_REVISED": [event(5, "trade_point", "source-B3", {}, "delete")],
    }
    current_events = base_events
    monkeypatch.setattr(
        "tvbt.strategy.run_chan",
        lambda *args, **kwargs: (
            SimpleNamespace(emitter=SimpleNamespace(events=current_events)),
            [],
            {},
        ),
    )
    algorithm = third_buy_only_definition()
    payload = {
        "dataset": {
            "dataset_id": "TEST.B3.INVALIDATION.5m",
            "data_revision": "sha256:" + "b" * 64,
            "bars_path": "normalized/TEST.B3.INVALIDATION.5m/revision/bars.parquet",
            "meta_path": "normalized/TEST.B3.INVALIDATION.5m/revision/meta.json",
        },
        "algorithm": {
            key: algorithm[key]
            for key in ("kind", "algorithm_id", "algorithm_version", "source_hash")
        },
        "parameters": {
            "checkpoint_interval": 1024,
            "allow_late_center": True,
            "first_center_quantity": 2,
            "late_center_quantity": 1,
            "minimum_entry_volume": 0,
        },
    }
    for expected_reason, invalidation_events in scenarios.items():
        current_events = [
            *base_events,
            *invalidation_events,
            event(6, "trade_point", "source-B3", {**b3_payload, "revision_marker": 2}),
        ]
        result = run_strategy(payload, guard, threading.Event())
        assert [value["action"] for value in result.trade_signals] == [
            "open_long",
            "close_long",
        ]
        assert result.trade_signals[-1]["reason_code"] == expected_reason
        assert any(
            value["reason_code"] == "B3_FIRST_RETURN_ALREADY_CONSUMED"
            for value in result.strategy_states
        )


def test_centre_oscillation_spread_swings_both_directions_then_hands_off_on_B3(
    tmp_path: Path, monkeypatch: object
) -> None:
    guard = PathGuard(tmp_path)
    dataset = tmp_path / "normalized" / "TEST.OSC.HANDOFF.5m" / "revision"
    dataset.mkdir(parents=True)
    closes = [100, 100, 92, 108, 112, 110]
    pq.write_table(
        pa.table(
            {
                "bar_index": pa.array(range(len(closes)), type=pa.int64()),
                "timestamp_utc": pa.array([index * 300_000 for index in range(len(closes))]),
                "open_i64": pa.array(closes, type=pa.int64()),
                "high_i64": pa.array([value + 2 for value in closes], type=pa.int64()),
                "low_i64": pa.array([value - 2 for value in closes], type=pa.int64()),
                "close_i64": pa.array(closes, type=pa.int64()),
            }
        ),
        dataset / "bars.parquet",
    )
    (dataset / "meta.json").write_text('{"price":{"price_scale":1}}', encoding="utf-8")

    def event(
        known_at: int,
        object_type: str,
        object_id: str,
        value: dict[str, object],
        operation: str = "upsert",
    ) -> object:
        return SimpleNamespace(
            known_at_bar_index=known_at,
            object_type=object_type,
            object_id=object_id,
            operation=operation,
            payload_json=(
                "{}"
                if operation == "delete"
                else json.dumps({"object_id": object_id, "confirmed": True, **value})
            ),
        )

    def monitor(
        known_at: int,
        object_id: str,
        endpoint: int,
        zn: int,
        direction: str,
    ) -> object:
        return event(
            known_at,
            "center_monitor",
            object_id,
            {
                "bar_index": endpoint,
                "z_i64": 100,
                "zn_i64": zn,
                "component_direction": direction,
                "analysis_level": "segment",
                "reference_object_id": "active-center",
            },
        )

    def divergence(
        known_at: int,
        object_id: str,
        endpoint: int,
        price: int,
        signal_type: str,
    ) -> object:
        return event(
            known_at,
            "divergence",
            object_id,
            {
                "bar_index": endpoint,
                "price_i64": price,
                "signal_type": signal_type,
                "divergence_kind": "consolidation",
                "reference_object_id": "active-center",
            },
        )

    fake_events = [
        event(
            1,
            "segment_zhongshu",
            "active-center",
            {
                "start_bar_index": 0,
                "end_bar_index": 3,
                "zd_i64": 90,
                "zg_i64": 110,
                "z_i64": 100,
                "analysis_level": "segment",
                "component_count": 5,
                "status": "extended",
            },
        ),
        monitor(2, "monitor-down", 2, 105, "down"),
        divergence(2, "bottom-divergence", 2, 92, "bottom_divergence"),
        monitor(3, "monitor-up", 3, 95, "up"),
        divergence(3, "top-divergence", 3, 108, "top_divergence"),
        event(
            4,
            "trade_point",
            "confirmed-B3",
            {
                "bar_index": 4,
                "price_i64": 112,
                "signal_type": "buy_3",
                "signal_class": "standard",
                "reference_object_id": "active-center",
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
    algorithm = centre_oscillation_spread_definition()
    payload = {
        "dataset": {
            "dataset_id": "TEST.OSC.HANDOFF.5m",
            "data_revision": "sha256:" + "c" * 64,
            "bars_path": "normalized/TEST.OSC.HANDOFF.5m/revision/bars.parquet",
            "meta_path": "normalized/TEST.OSC.HANDOFF.5m/revision/meta.json",
        },
        "algorithm": {
            key: algorithm[key]
            for key in ("kind", "algorithm_id", "algorithm_version", "source_hash")
        },
        "parameters": {
            "checkpoint_interval": 1024,
            "allow_long": True,
            "allow_short": True,
            "strong_quantity": 2,
            "neutral_quantity": 1,
            "weak_quantity": 1,
            "estimated_round_trip_cost_i64": 2,
            "minimum_net_range_i64": 1,
            "fast_execution_available": False,
            "max_entries_per_center": 4,
        },
    }
    result = run_strategy(payload, guard, threading.Event())
    assert [value["state_to"] for value in result.strategy_states] == [
        "oscillation_ready",
        "oscillation_long_strong",
        "oscillation_short_strong",
        "oscillation_stopped_by_B3",
    ]
    assert [value["action"] for value in result.trade_signals] == [
        "open_long",
        "close_long",
        "open_short",
        "close_short",
    ]
    assert [value["quantity"] for value in result.trade_signals] == [2, 2, 2, 2]
    assert [value["event_type"] for value in result.chart_events] == [
        "open_long",
        "swing_buy",
        "close_long",
        "open_short",
        "swing_sell",
        "close_short",
        "stop_oscillation",
        "handoff_to_trend",
    ]
    prefix = run_strategy(payload, guard, threading.Event(), last_bar_index=3)
    assert prefix.events == [value for value in result.events if value["known_at_bar_index"] <= 3]

    monkeypatch.setattr("tvbt.backtest.run_strategy", lambda *args, **kwargs: result)
    run_ref = run_backtest(
        {
            **payload,
            "range": {"warmup_from_bar_index": 0, "from_bar_index": 0, "to_bar_index": 5},
            "run_id": "run-OSC",
            "run_signature": "sha256:" + "f" * 64,
            "trace_id": "trace-OSC",
            "execution": {
                "signal_timing": "bar_close",
                "fill_timing": "next_bar_open",
                "commission": {
                    "mode": "fixed_per_contract",
                    "amount_i64": 10,
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
            "random_seed": 7,
            "output_path": "runs/run-OSC",
        },
        guard,
        threading.Event(),
    )
    run_dir = tmp_path / run_ref
    assert [value["action"] for value in pq.read_table(run_dir / "fills.parquet").to_pylist()] == [
        "open_long",
        "close_long",
        "open_short",
        "close_short",
    ]
    assert [
        value["quantity"] for value in pq.read_table(run_dir / "trades.parquet").to_pylist()
    ] == [2, 2]


def test_centre_oscillation_spread_applies_cost_Zn_turnover_and_promotion_risk(
    tmp_path: Path, monkeypatch: object
) -> None:
    guard = PathGuard(tmp_path)
    dataset = tmp_path / "normalized" / "TEST.OSC.RISK.5m" / "revision"
    dataset.mkdir(parents=True)
    closes = [100, 88, 94, 108, 100, 100]
    pq.write_table(
        pa.table(
            {
                "bar_index": pa.array(range(len(closes)), type=pa.int64()),
                "timestamp_utc": pa.array([index * 300_000 for index in range(len(closes))]),
                "open_i64": pa.array(closes, type=pa.int64()),
                "high_i64": pa.array([value + 2 for value in closes], type=pa.int64()),
                "low_i64": pa.array([value - 2 for value in closes], type=pa.int64()),
                "close_i64": pa.array(closes, type=pa.int64()),
            }
        ),
        dataset / "bars.parquet",
    )
    (dataset / "meta.json").write_text('{"price":{"price_scale":1}}', encoding="utf-8")

    def event(known_at: int, object_type: str, object_id: str, value: dict[str, object]) -> object:
        return SimpleNamespace(
            known_at_bar_index=known_at,
            object_type=object_type,
            object_id=object_id,
            operation="upsert",
            payload_json=json.dumps({"object_id": object_id, "confirmed": True, **value}),
        )

    def center(known_at: int, component_count: int) -> object:
        return event(
            known_at,
            "segment_zhongshu",
            "risk-center",
            {
                "start_bar_index": 0,
                "end_bar_index": 4,
                "zd_i64": 90,
                "zg_i64": 110,
                "z_i64": 100,
                "analysis_level": "segment",
                "component_count": component_count,
                "status": "extended",
            },
        )

    def pair(
        known_at: int,
        suffix: str,
        endpoint: int,
        price: int,
        zn: int,
        signal_type: str,
    ) -> list[object]:
        direction = "down" if signal_type == "bottom_divergence" else "up"
        return [
            event(
                known_at,
                "center_monitor",
                f"monitor-{suffix}",
                {
                    "bar_index": endpoint,
                    "z_i64": 100,
                    "zn_i64": zn,
                    "component_direction": direction,
                    "analysis_level": "segment",
                    "reference_object_id": "risk-center",
                },
            ),
            event(
                known_at,
                "divergence",
                f"divergence-{suffix}",
                {
                    "bar_index": endpoint,
                    "price_i64": price,
                    "signal_type": signal_type,
                    "divergence_kind": "consolidation",
                    "reference_object_id": "risk-center",
                },
            ),
        ]

    fake_events = [
        center(0, 5),
        *pair(1, "below", 1, 88, 85, "bottom_divergence"),
        *pair(2, "strong", 2, 94, 105, "bottom_divergence"),
        *pair(3, "top", 3, 108, 95, "top_divergence"),
        center(4, 9),
    ]
    monkeypatch.setattr(
        "tvbt.strategy.run_chan",
        lambda *args, **kwargs: (
            SimpleNamespace(emitter=SimpleNamespace(events=fake_events)),
            [],
            {},
        ),
    )
    algorithm = centre_oscillation_spread_definition()
    parameters = {
        "checkpoint_interval": 1024,
        "allow_long": True,
        "allow_short": True,
        "strong_quantity": 2,
        "neutral_quantity": 1,
        "weak_quantity": 1,
        "estimated_round_trip_cost_i64": 0,
        "minimum_net_range_i64": 1,
        "fast_execution_available": False,
        "max_entries_per_center": 1,
    }
    payload = {
        "dataset": {
            "dataset_id": "TEST.OSC.RISK.5m",
            "data_revision": "sha256:" + "d" * 64,
            "bars_path": "normalized/TEST.OSC.RISK.5m/revision/bars.parquet",
            "meta_path": "normalized/TEST.OSC.RISK.5m/revision/meta.json",
        },
        "algorithm": {
            key: algorithm[key]
            for key in ("kind", "algorithm_id", "algorithm_version", "source_hash")
        },
        "parameters": parameters,
    }
    result = run_strategy(payload, guard, threading.Event())
    assert [value["action"] for value in result.trade_signals] == ["open_long", "close_long"]
    assert any(
        value["reason_code"] == "ZN_BELOW_ZD_WITHOUT_FAST_EXECUTION"
        for value in result.strategy_states
    )
    assert any(
        value["reason_code"] == "CENTER_TURNOVER_CAP_REACHED" for value in result.strategy_states
    )
    assert result.strategy_states[-1]["reason_code"] == "NINE_COMPONENT_CENTER_PROMOTION_RISK"

    fast = run_strategy(
        {
            **payload,
            "parameters": {**parameters, "fast_execution_available": True},
        },
        guard,
        threading.Event(),
    )
    assert [value["quantity"] for value in fast.trade_signals] == [1, 1]
    assert fast.trade_signals[0]["priority"] == "weak"

    cost_filtered = run_strategy(
        {
            **payload,
            "parameters": {
                **parameters,
                "estimated_round_trip_cost_i64": 20,
                "minimum_net_range_i64": 0,
            },
        },
        guard,
        threading.Event(),
    )
    assert not cost_filtered.trade_signals
    assert any(
        value["reason_code"] == "OSCILLATION_RANGE_NOT_ABOVE_COST"
        for value in cost_filtered.strategy_states
    )

    with pytest.raises(ValueError, match="weak <= neutral <= strong"):
        run_strategy(
            {
                **payload,
                "parameters": {**parameters, "strong_quantity": 1, "neutral_quantity": 2},
            },
            guard,
            threading.Event(),
        )


def test_centre_oscillation_spread_stops_on_S3_center_or_source_revision(
    tmp_path: Path, monkeypatch: object
) -> None:
    guard = PathGuard(tmp_path)
    dataset = tmp_path / "normalized" / "TEST.OSC.INVALIDATION.5m" / "revision"
    dataset.mkdir(parents=True)
    closes = [100, 94, 96, 98]
    pq.write_table(
        pa.table(
            {
                "bar_index": pa.array(range(len(closes)), type=pa.int64()),
                "timestamp_utc": pa.array([index * 300_000 for index in range(len(closes))]),
                "open_i64": pa.array(closes, type=pa.int64()),
                "high_i64": pa.array([value + 2 for value in closes], type=pa.int64()),
                "low_i64": pa.array([value - 2 for value in closes], type=pa.int64()),
                "close_i64": pa.array(closes, type=pa.int64()),
            }
        ),
        dataset / "bars.parquet",
    )
    (dataset / "meta.json").write_text('{"price":{"price_scale":1}}', encoding="utf-8")

    def event(
        known_at: int,
        object_type: str,
        object_id: str,
        value: dict[str, object],
        operation: str = "upsert",
    ) -> object:
        return SimpleNamespace(
            known_at_bar_index=known_at,
            object_type=object_type,
            object_id=object_id,
            operation=operation,
            payload_json=(
                "{}"
                if operation == "delete"
                else json.dumps({"object_id": object_id, "confirmed": True, **value})
            ),
        )

    center_payload = {
        "start_bar_index": 0,
        "end_bar_index": 3,
        "zd_i64": 90,
        "zg_i64": 110,
        "z_i64": 100,
        "analysis_level": "segment",
        "component_count": 5,
        "status": "extended",
    }
    monitor_payload = {
        "bar_index": 1,
        "z_i64": 100,
        "zn_i64": 105,
        "component_direction": "down",
        "analysis_level": "segment",
        "reference_object_id": "source-center",
    }
    divergence_payload = {
        "bar_index": 1,
        "price_i64": 94,
        "signal_type": "bottom_divergence",
        "divergence_kind": "consolidation",
        "reference_object_id": "source-center",
    }
    base_events = [
        event(0, "segment_zhongshu", "source-center", center_payload),
        event(1, "center_monitor", "source-monitor", monitor_payload),
        event(1, "divergence", "source-divergence", divergence_payload),
    ]
    scenarios = {
        "CONFIRMED_S3_STOPPED_OSCILLATION": [
            event(
                2,
                "trade_point",
                "confirmed-S3",
                {
                    "bar_index": 2,
                    "price_i64": 96,
                    "signal_type": "sell_3",
                    "signal_class": "standard",
                    "reference_object_id": "source-center",
                },
            )
        ],
        "OSCILLATION_SOURCE_FACT_REVISED": [
            event(2, "divergence", "source-divergence", {}, "delete")
        ],
        "ACTIVE_CENTER_LEFT_WITHOUT_THIRD_POINT": [
            event(
                2,
                "segment_zhongshu",
                "source-center",
                {**center_payload, "status": "left", "leave_direction": "up"},
            )
        ],
        "ACTIVE_CENTER_DELETED": [event(2, "segment_zhongshu", "source-center", {}, "delete")],
    }
    current_events = base_events
    monkeypatch.setattr(
        "tvbt.strategy.run_chan",
        lambda *args, **kwargs: (
            SimpleNamespace(emitter=SimpleNamespace(events=current_events)),
            [],
            {},
        ),
    )
    algorithm = centre_oscillation_spread_definition()
    payload = {
        "dataset": {
            "dataset_id": "TEST.OSC.INVALIDATION.5m",
            "data_revision": "sha256:" + "e" * 64,
            "bars_path": "normalized/TEST.OSC.INVALIDATION.5m/revision/bars.parquet",
            "meta_path": "normalized/TEST.OSC.INVALIDATION.5m/revision/meta.json",
        },
        "algorithm": {
            key: algorithm[key]
            for key in ("kind", "algorithm_id", "algorithm_version", "source_hash")
        },
        "parameters": {
            "checkpoint_interval": 1024,
            "allow_long": True,
            "allow_short": True,
            "strong_quantity": 2,
            "neutral_quantity": 1,
            "weak_quantity": 1,
            "estimated_round_trip_cost_i64": 0,
            "minimum_net_range_i64": 1,
            "fast_execution_available": False,
            "max_entries_per_center": 4,
        },
    }
    for expected_reason, invalidation in scenarios.items():
        current_events = [*base_events, *invalidation]
        result = run_strategy(payload, guard, threading.Event())
        assert [value["action"] for value in result.trade_signals] == [
            "open_long",
            "close_long",
        ]
        assert result.trade_signals[-1]["reason_code"] == expected_reason
        if expected_reason == "CONFIRMED_S3_STOPPED_OSCILLATION":
            assert any(
                value["event_type"] == "handoff_to_trend" and value["handoff_direction"] == "down"
                for value in result.chart_events
            )


def test_same_level_decomposition_compares_Ai_Ai_plus_2_and_branches_on_Ai_plus_3(
    tmp_path: Path, monkeypatch: object
) -> None:
    guard = PathGuard(tmp_path)
    dataset = tmp_path / "normalized" / "TEST.SAME.LEVEL.5m" / "revision"
    dataset.mkdir(parents=True)
    closes = [200, 100, 150, 100, 160, 140, 155, 150]
    pq.write_table(
        pa.table(
            {
                "bar_index": pa.array(range(len(closes)), type=pa.int64()),
                "timestamp_utc": pa.array(
                    [index * 300_000 for index in range(len(closes))], type=pa.int64()
                ),
                "open_i64": pa.array(closes, type=pa.int64()),
                "high_i64": pa.array([value + 2 for value in closes], type=pa.int64()),
                "low_i64": pa.array([value - 2 for value in closes], type=pa.int64()),
                "close_i64": pa.array(closes, type=pa.int64()),
            }
        ),
        dataset / "bars.parquet",
    )
    (dataset / "meta.json").write_text('{"price":{"price_scale":1}}', encoding="utf-8")

    def segment(
        known_at: int,
        object_id: str,
        start: int,
        end: int,
        start_price: int,
        end_price: int,
        direction: str,
    ) -> object:
        return SimpleNamespace(
            known_at_bar_index=known_at,
            object_type="segment",
            object_id=object_id,
            operation="upsert",
            payload_json=json.dumps(
                {
                    "object_id": object_id,
                    "confirmed": True,
                    "confirmed_at_bar_index": known_at,
                    "start_bar_index": start,
                    "end_bar_index": end,
                    "start_price_i64": start_price,
                    "end_price_i64": end_price,
                    "direction": direction,
                }
            ),
        )

    fake_events = [
        segment(1, "A1-down", 0, 1, 200, 100, "down"),
        segment(2, "A2-up", 1, 2, 100, 150, "up"),
        # 等号不算创新低，A3 确认后执行机械买入。
        segment(3, "A3-down", 2, 3, 150, 100, "down"),
        # A4 创新高且无背驰，先持有并等待 A5 分支。
        segment(4, "A4-up", 3, 4, 100, 160, "up"),
        # A5 跌破 A2 高点，发布“等待新同级结构”；同时它相对 A3 不创新低。
        segment(5, "A5-down", 4, 5, 160, 140, "down"),
        # A6 相对 A4 不创新高，反向为做空。
        segment(6, "A6-up", 5, 6, 140, 155, "up"),
    ]
    monkeypatch.setattr(
        "tvbt.strategy.run_chan",
        lambda *args, **kwargs: (
            SimpleNamespace(emitter=SimpleNamespace(events=fake_events)),
            [],
            {},
        ),
    )
    algorithm = same_level_decomposition_program_definition()
    payload = {
        "dataset": {
            "dataset_id": "TEST.SAME.LEVEL.5m",
            "data_revision": "sha256:" + "1" * 64,
            "bars_path": "normalized/TEST.SAME.LEVEL.5m/revision/bars.parquet",
            "meta_path": "normalized/TEST.SAME.LEVEL.5m/revision/meta.json",
        },
        "algorithm": {
            key: algorithm[key]
            for key in ("kind", "algorithm_id", "algorithm_version", "source_hash")
        },
        "parameters": {
            "checkpoint_interval": 1024,
            "odd_direction_is_down": True,
            "allow_long": True,
            "allow_short": True,
            "operation_quantity": 2,
        },
    }
    result = run_strategy(payload, guard, threading.Event())
    assert [value["action"] for value in result.trade_signals] == [
        "open_long",
        "close_long",
        "open_short",
    ]
    assert [value["quantity"] for value in result.trade_signals] == [2, 2, 2]
    assert result.trade_signals[0]["reason_code"] == (
        "SAME_LEVEL_LATER_MOVEMENT_FAILED_NEW_EXTREME"
    )
    assert any(
        value["event_type"] == "wait_new_same_level_structure"
        and value["ai_index"] == 2
        and value["ai_plus_3_index"] == 5
        for value in result.chart_events
    )
    assert any(value["event_type"] == "same_level_hold" for value in result.chart_events)
    prefix = run_strategy(payload, guard, threading.Event(), last_bar_index=5)
    assert prefix.events == [value for value in result.events if value["known_at_bar_index"] <= 5]

    # A5 恰好守住 A2 高点时不算破坏，继续围绕原中枢。
    fake_events[4] = segment(5, "A5-down", 4, 5, 160, 150, "down")
    preserved = run_strategy(payload, guard, threading.Event())
    assert any(
        value["event_type"] == "continue_original_center"
        and value["reason_code"] == "AI_PLUS_3_PRESERVED_AI_DIRECTIONAL_EXTREME"
        for value in preserved.chart_events
    )


def test_same_level_decomposition_uses_confirmed_divergence_and_stops_on_promotion(
    tmp_path: Path, monkeypatch: object
) -> None:
    guard = PathGuard(tmp_path)
    dataset = tmp_path / "normalized" / "TEST.SAME.LEVEL.PROMOTE.5m" / "revision"
    dataset.mkdir(parents=True)
    closes = [200, 100, 150, 90, 100, 100]
    pq.write_table(
        pa.table(
            {
                "bar_index": pa.array(range(len(closes)), type=pa.int64()),
                "timestamp_utc": pa.array(
                    [index * 300_000 for index in range(len(closes))], type=pa.int64()
                ),
                "open_i64": pa.array(closes, type=pa.int64()),
                "high_i64": pa.array([value + 2 for value in closes], type=pa.int64()),
                "low_i64": pa.array([value - 2 for value in closes], type=pa.int64()),
                "close_i64": pa.array(closes, type=pa.int64()),
            }
        ),
        dataset / "bars.parquet",
    )
    (dataset / "meta.json").write_text('{"price":{"price_scale":1}}', encoding="utf-8")

    def event(
        known_at: int,
        object_type: str,
        object_id: str,
        value: dict[str, object],
        operation: str = "upsert",
    ) -> object:
        return SimpleNamespace(
            known_at_bar_index=known_at,
            object_type=object_type,
            object_id=object_id,
            operation=operation,
            payload_json=(
                "{}"
                if operation == "delete"
                else json.dumps({"object_id": object_id, "confirmed": True, **value})
            ),
        )

    def segment(
        known_at: int,
        object_id: str,
        start: int,
        end: int,
        start_price: int,
        end_price: int,
        direction: str,
        *,
        confirmed: bool = True,
    ) -> object:
        return event(
            known_at,
            "segment",
            object_id,
            {
                "confirmed": confirmed,
                "confirmed_at_bar_index": known_at if confirmed else None,
                "start_bar_index": start,
                "end_bar_index": end,
                "start_price_i64": start_price,
                "end_price_i64": end_price,
                "direction": direction,
            },
        )

    fake_events = [
        segment(1, "A1-down", 0, 1, 200, 100, "down"),
        segment(2, "A2-up", 1, 2, 100, 150, "up"),
        segment(3, "A3-down", 2, 3, 150, 90, "down"),
        event(
            3,
            "divergence",
            "A3-bottom-divergence",
            {
                "bar_index": 3,
                "price_i64": 90,
                "signal_type": "bottom_divergence",
                "divergence_kind": "consolidation",
                "reference_object_id": "center-one",
                "confirmed_at_bar_index": 3,
            },
        ),
        # 未确认单元永远不能参与 Ai/Ai+2 比较。
        segment(3, "unfinished-A4", 3, 4, 90, 160, "up", confirmed=False),
        event(
            4,
            "segment_zhongshu",
            "higher-center-candidate",
            {
                "start_bar_index": 0,
                "end_bar_index": 4,
                "zd_i64": 100,
                "zg_i64": 120,
                "z_i64": 110,
                "analysis_level": "segment",
                "component_kind": "segment",
                "component_count": 9,
                "confirmed_at_bar_index": 4,
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
    algorithm = same_level_decomposition_program_definition()
    payload = {
        "dataset": {
            "dataset_id": "TEST.SAME.LEVEL.PROMOTE.5m",
            "data_revision": "sha256:" + "2" * 64,
            "bars_path": "normalized/TEST.SAME.LEVEL.PROMOTE.5m/revision/bars.parquet",
            "meta_path": "normalized/TEST.SAME.LEVEL.PROMOTE.5m/revision/meta.json",
        },
        "algorithm": {
            key: algorithm[key]
            for key in ("kind", "algorithm_id", "algorithm_version", "source_hash")
        },
        "parameters": {
            "checkpoint_interval": 1024,
            "odd_direction_is_down": True,
            "allow_long": True,
            "allow_short": True,
            "operation_quantity": 1,
        },
    }
    result = run_strategy(payload, guard, threading.Event())
    assert [value["action"] for value in result.trade_signals] == ["open_long", "close_long"]
    assert result.trade_signals[0]["reason_code"] == (
        "CONFIRMED_SAME_LEVEL_CONSOLIDATION_DIVERGENCE"
    )
    promotion = next(
        value for value in result.chart_events if value["event_type"] == "promote_level_candidate"
    )
    assert promotion["component_count"] == 9
    assert result.strategy_states[-1]["state_to"] == "same_level_promotion_candidate"

    # 来源分解被修订时平仓并从修订时点重新起链，不用终态回写旧操作。
    fake_events.pop()
    fake_events.append(segment(4, "A1-down", 0, 1, 200, 95, "down"))
    revised = run_strategy(payload, guard, threading.Event())
    assert [value["action"] for value in revised.trade_signals] == ["open_long", "close_long"]
    assert revised.trade_signals[-1]["reason_code"] == (
        "CANONICAL_SAME_LEVEL_DECOMPOSITION_REVISED"
    )
    assert any(
        value["event_type"] == "same_level_decomposition_reset" for value in revised.chart_events
    )

    with pytest.raises(ValueError, match="operation_quantity"):
        run_strategy(
            {**payload, "parameters": {**payload["parameters"], "operation_quantity": 0}},
            guard,
            threading.Event(),
        )


def _three_level_event(
    known_at: int,
    object_type: str,
    object_id: str,
    value: dict[str, object],
    operation: str = "upsert",
) -> object:
    return SimpleNamespace(
        known_at_bar_index=known_at,
        object_type=object_type,
        object_id=object_id,
        operation=operation,
        payload_json=(
            "{}"
            if operation == "delete"
            else json.dumps({"object_id": object_id, "confirmed": True, **value})
        ),
    )


def _three_level_center(
    *,
    leave_direction: str,
    confirmed_at: int = 1,
    end_bar_index: int = 1,
) -> dict[str, object]:
    return {
        "start_bar_index": 0,
        "end_bar_index": end_bar_index,
        "zd_i64": 90,
        "zg_i64": 110,
        "z_i64": 100,
        "analysis_level": "segment",
        "component_kind": "segment",
        "component_count": 5,
        "confirmed_at_bar_index": confirmed_at,
        "status": "left",
        "leave_direction": leave_direction,
    }


def _run_three_level_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    events: list[object],
    *,
    dataset_id: str,
    parameter_overrides: dict[str, object] | None = None,
    last_bar_index: int | None = None,
) -> tuple[object, dict[str, object], PathGuard]:
    guard = PathGuard(tmp_path)
    dataset = tmp_path / "normalized" / dataset_id / "revision"
    dataset.mkdir(parents=True, exist_ok=True)
    closes = [100, 88, 80, 90, 92, 82, 78, 85]
    pq.write_table(
        pa.table(
            {
                "bar_index": pa.array(range(len(closes)), type=pa.int64()),
                "timestamp_utc": pa.array(
                    [index * 300_000 for index in range(len(closes))], type=pa.int64()
                ),
                "open_i64": pa.array(closes, type=pa.int64()),
                "high_i64": pa.array([value + 2 for value in closes], type=pa.int64()),
                "low_i64": pa.array([value - 2 for value in closes], type=pa.int64()),
                "close_i64": pa.array(closes, type=pa.int64()),
            }
        ),
        dataset / "bars.parquet",
    )
    (dataset / "meta.json").write_text(
        '{"price":{"price_scale":1,"tick_size_i64":1}}', encoding="utf-8"
    )
    monkeypatch.setattr(
        "tvbt.strategy.run_chan",
        lambda *args, **kwargs: (
            SimpleNamespace(emitter=SimpleNamespace(events=events)),
            [],
            {},
        ),
    )
    algorithm = three_level_complete_classification_definition()
    payload: dict[str, object] = {
        "dataset": {
            "dataset_id": dataset_id,
            "data_revision": "sha256:" + "3" * 64,
            "bars_path": f"normalized/{dataset_id}/revision/bars.parquet",
            "meta_path": f"normalized/{dataset_id}/revision/meta.json",
        },
        "algorithm": {
            key: algorithm[key]
            for key in ("kind", "algorithm_id", "algorithm_version", "source_hash")
        },
        "parameters": {
            "checkpoint_interval": 1024,
            "level_graph_profile_id": 1,
            "allow_long": True,
            "allow_short": True,
            "operation_quantity": 2,
            "can_handle_mid_third_point": True,
            "can_handle_mid_center_continue": True,
            "can_handle_high_change_candidate": True,
            **(parameter_overrides or {}),
        },
    }
    return (
        run_strategy(
            payload,  # type: ignore[arg-type]
            guard,
            threading.Event(),
            last_bar_index=last_bar_index,
        ),
        payload,
        guard,
    )


def test_three_level_classification_waits_and_blocks_unmanageable_legal_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events = [
        _three_level_event(
            1,
            "segment_zhongshu",
            "middle-center-down",
            _three_level_center(leave_direction="down"),
        ),
        _three_level_event(
            2,
            "trade_point",
            "lowest-B1",
            {
                "bar_index": 2,
                "price_i64": 80,
                "signal_type": "buy_1",
                "signal_class": "standard",
                "reference_object_id": "lowest-divergence",
                "confirmed_at_bar_index": 2,
            },
        ),
    ]
    result, payload, guard = _run_three_level_fixture(
        tmp_path,
        monkeypatch,
        events,
        dataset_id="TEST.THREE.LEVEL.BLOCKED.5m",
        parameter_overrides={"can_handle_mid_third_point": False},
    )
    assert [value["state_to"] for value in result.strategy_states] == [
        "WAIT_LOW_TURN",
        "LOW_TURN_ACTIVE",
    ]
    low_turn = result.strategy_states[-1]
    assert low_turn["reason_code"] == "LEGAL_BRANCH_UNMANAGEABLE"
    assert low_turn["max_participation_quantity"] == 0
    assert low_turn["level_graph_profile"] == "segment_center_chain_v1"
    assert "mid_third_point" not in low_turn["handled_branches"]
    assert result.trade_signals == []
    assert any(
        value["event_type"] == "low_turn_participation_blocked"
        and value["reason_code"] == "LEGAL_BRANCH_UNMANAGEABLE"
        for value in result.chart_events
    )
    participation_cap = next(
        value for value in result.chart_events if value["event_type"] == "participation_cap"
    )
    assert participation_cap["max_participation_quantity"] == 0

    prefix = run_strategy(
        payload,  # type: ignore[arg-type]
        guard,
        threading.Event(),
        last_bar_index=1,
    )
    assert [value["state_to"] for value in prefix.strategy_states] == ["WAIT_LOW_TURN"]
    assert prefix.trade_signals == []


def test_three_level_classification_progresses_low_mid_then_high_without_prediction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events = [
        _three_level_event(
            1,
            "segment_zhongshu",
            "middle-center-down",
            _three_level_center(leave_direction="down"),
        ),
        _three_level_event(
            2,
            "divergence",
            "lowest-bottom-divergence",
            {
                "bar_index": 2,
                "price_i64": 80,
                "signal_type": "bottom_divergence",
                "divergence_kind": "trend",
                "reference_object_id": "lowest-downtrend",
                "confirmed_at_bar_index": 2,
            },
        ),
        # A high-level-looking event before the middle branch completes must not be reused later.
        _three_level_event(
            3,
            "movement_state",
            "premature-migration-down",
            {
                "start_bar_index": 1,
                "end_bar_index": 3,
                "price_i64": 75,
                "state_type": "centre_migration_down",
                "direction": "down",
                "analysis_level": "segment",
                "reference_object_id": "premature-center",
                "confirmed_at_bar_index": 3,
            },
        ),
        _three_level_event(
            4,
            "trade_point",
            "middle-S3",
            {
                "bar_index": 4,
                "price_i64": 90,
                "signal_type": "sell_3",
                "signal_class": "standard",
                "reference_object_id": "middle-center-down",
                "confirmed_at_bar_index": 4,
            },
        ),
        _three_level_event(
            5,
            "movement_state",
            "completed-migration-down",
            {
                "start_bar_index": 1,
                "end_bar_index": 5,
                "price_i64": 82,
                "state_type": "centre_migration_down",
                "direction": "down",
                "analysis_level": "segment",
                "reference_object_id": "next-lower-center",
                "confirmed_at_bar_index": 5,
            },
        ),
    ]
    result, payload, guard = _run_three_level_fixture(
        tmp_path,
        monkeypatch,
        events,
        dataset_id="TEST.THREE.LEVEL.DOWN.5m",
    )
    assert [value["state_to"] for value in result.strategy_states] == [
        "WAIT_LOW_TURN",
        "LOW_TURN_ACTIVE",
        "MID_THIRD_POINT",
        "HIGH_CHANGE_CANDIDATE",
    ]
    assert [value["known_at_bar_index"] for value in result.strategy_states] == [1, 2, 4, 5]
    assert [value["action"] for value in result.trade_signals] == [
        "open_long",
        "close_long",
    ]
    assert [value["quantity"] for value in result.trade_signals] == [2, 2]
    candidate = result.strategy_states[-1]
    assert candidate["high_change_status"] == "candidate"
    assert candidate["reference_object_id"] == "completed-migration-down"
    assert not any(
        value["reference_object_id"] == "premature-migration-down" for value in result.chart_events
    )
    prefix = run_strategy(
        payload,  # type: ignore[arg-type]
        guard,
        threading.Event(),
        last_bar_index=4,
    )
    assert prefix.events == [value for value in result.events if value["known_at_bar_index"] <= 4]
    assert "HIGH_CHANGE_CANDIDATE" not in {value["state_to"] for value in prefix.strategy_states}


def test_three_level_classification_prioritizes_third_point_boundary_and_center_continue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base_events = [
        _three_level_event(
            1,
            "segment_zhongshu",
            "middle-center-down",
            _three_level_center(leave_direction="down"),
        ),
        _three_level_event(
            2,
            "trade_point",
            "lowest-B1",
            {
                "bar_index": 2,
                "price_i64": 80,
                "signal_type": "buy_1",
                "signal_class": "standard",
                "reference_object_id": "lowest-divergence",
                "confirmed_at_bar_index": 2,
            },
        ),
        _three_level_event(
            3,
            "segment",
            "unfinished-return",
            {
                "start_bar_index": 2,
                "end_bar_index": 3,
                "start_price_i64": 80,
                "end_price_i64": 100,
                "direction": "up",
                "confirmed": False,
                "confirmed_at_bar_index": None,
            },
        ),
        _three_level_event(
            4,
            "segment",
            "boundary-return",
            {
                "start_bar_index": 2,
                "end_bar_index": 4,
                "start_price_i64": 80,
                "end_price_i64": 90,
                "direction": "up",
                "confirmed_at_bar_index": 4,
            },
        ),
        _three_level_event(
            4,
            "trade_point",
            "middle-S3-boundary",
            {
                "bar_index": 4,
                "price_i64": 90,
                "signal_type": "sell_3",
                "signal_class": "standard",
                "reference_object_id": "middle-center-down",
                "confirmed_at_bar_index": 4,
            },
        ),
    ]
    boundary, _, _ = _run_three_level_fixture(
        tmp_path,
        monkeypatch,
        base_events,
        dataset_id="TEST.THREE.LEVEL.BOUNDARY.5m",
    )
    assert boundary.strategy_states[-1]["state_to"] == "MID_THIRD_POINT"
    assert not any(value["event_type"] == "mid_center_continue" for value in boundary.chart_events)

    continuation_events = [
        *base_events[:-2],
        _three_level_event(
            4,
            "segment",
            "return-entered-center",
            {
                "start_bar_index": 2,
                "end_bar_index": 4,
                "start_price_i64": 80,
                "end_price_i64": 91,
                "direction": "up",
                "confirmed_at_bar_index": 4,
            },
        ),
    ]
    continuation, _, _ = _run_three_level_fixture(
        tmp_path,
        monkeypatch,
        continuation_events,
        dataset_id="TEST.THREE.LEVEL.CONTINUE.5m",
    )
    assert continuation.strategy_states[-1]["state_to"] == "MID_CENTER_CONTINUE"
    assert [value["action"] for value in continuation.trade_signals] == ["open_long"]
    chart_event = next(
        value for value in continuation.chart_events if value["event_type"] == "mid_center_continue"
    )
    assert chart_event["position_action"] == "hold"


def test_three_level_classification_is_direction_symmetric_and_resets_revised_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events = [
        _three_level_event(
            1,
            "segment_zhongshu",
            "middle-center-up",
            _three_level_center(leave_direction="up"),
        ),
        _three_level_event(
            2,
            "trade_point",
            "lowest-S1",
            {
                "bar_index": 2,
                "price_i64": 120,
                "signal_type": "sell_1",
                "signal_class": "standard",
                "reference_object_id": "lowest-top-divergence",
                "confirmed_at_bar_index": 2,
            },
        ),
        _three_level_event(
            3,
            "trade_point",
            "middle-B3",
            {
                "bar_index": 3,
                "price_i64": 110,
                "signal_type": "buy_3",
                "signal_class": "standard",
                "reference_object_id": "middle-center-up",
                "confirmed_at_bar_index": 3,
            },
        ),
        _three_level_event(
            4,
            "movement_state",
            "completed-migration-up",
            {
                "start_bar_index": 1,
                "end_bar_index": 4,
                "price_i64": 118,
                "state_type": "centre_migration_up",
                "direction": "up",
                "analysis_level": "segment",
                "reference_object_id": "next-upper-center",
                "confirmed_at_bar_index": 4,
            },
        ),
    ]
    result, _, _ = _run_three_level_fixture(
        tmp_path,
        monkeypatch,
        events,
        dataset_id="TEST.THREE.LEVEL.UP.5m",
    )
    assert [value["action"] for value in result.trade_signals] == [
        "open_short",
        "close_short",
    ]
    assert result.strategy_states[-1]["candidate_direction"] == "up"

    revised_events = [
        events[0],
        events[1],
        _three_level_event(
            3,
            "trade_point",
            "lowest-S1",
            {
                "bar_index": 2,
                "price_i64": 119,
                "signal_type": "sell_1",
                "signal_class": "standard",
                "reference_object_id": "lowest-top-divergence",
                "confirmed_at_bar_index": 3,
            },
        ),
    ]
    revised, payload, guard = _run_three_level_fixture(
        tmp_path,
        monkeypatch,
        revised_events,
        dataset_id="TEST.THREE.LEVEL.REVISED.5m",
    )
    assert [value["action"] for value in revised.trade_signals] == [
        "open_short",
        "close_short",
    ]
    assert revised.trade_signals[-1]["reason_code"] == "THREE_LEVEL_SOURCE_FACT_REVISED"
    assert any(value["event_type"] == "three_level_context_reset" for value in revised.chart_events)

    with pytest.raises(ValueError, match="level_graph_profile_id"):
        run_strategy(
            {
                **payload,
                "parameters": {
                    **payload["parameters"],  # type: ignore[dict-item]
                    "level_graph_profile_id": 2,
                },
            },
            guard,
            threading.Event(),
        )


def _segmented_center(
    *,
    zd_i64: int = 90,
    zg_i64: int = 100,
    end_bar_index: int = 4,
    status: str = "confirmed",
    leave_direction: str | None = None,
) -> dict[str, object]:
    return {
        "start_bar_index": 1,
        "end_bar_index": end_bar_index,
        "zd_i64": zd_i64,
        "zg_i64": zg_i64,
        "z_i64": (zd_i64 + zg_i64) // 2,
        "analysis_level": "segment",
        "component_kind": "segment",
        "component_count": 3,
        "confirmed_at_bar_index": 4,
        "status": status,
        "leave_direction": leave_direction,
    }


def _segmented_long_events(*, followthrough_end_i64: int = 130) -> list[object]:
    return [
        _three_level_event(
            1,
            "trade_point",
            "target-B1",
            {
                "bar_index": 1,
                "price_i64": 80,
                "signal_type": "buy_1",
                "signal_class": "standard",
                "reference_object_id": "target-bottom-divergence",
                "confirmed_at_bar_index": 1,
            },
        ),
        _three_level_event(
            2,
            "segment",
            "first-up",
            {
                "start_bar_index": 1,
                "end_bar_index": 2,
                "start_price_i64": 80,
                "end_price_i64": 100,
                "direction": "up",
                "confirmed_at_bar_index": 2,
            },
        ),
        _three_level_event(
            3,
            "segment",
            "counter-down",
            {
                "start_bar_index": 2,
                "end_bar_index": 3,
                "start_price_i64": 100,
                "end_price_i64": 90,
                "direction": "down",
                "confirmed_at_bar_index": 3,
            },
        ),
        _three_level_event(
            4,
            "segment",
            "third-up",
            {
                "start_bar_index": 3,
                "end_bar_index": 4,
                "start_price_i64": 90,
                "end_price_i64": 110,
                "direction": "up",
                "confirmed_at_bar_index": 4,
            },
        ),
        _three_level_event(
            4,
            "segment_zhongshu",
            "first-target-center",
            _segmented_center(),
        ),
        _three_level_event(
            5,
            "segment",
            "departure-up",
            {
                "start_bar_index": 4,
                "end_bar_index": 5,
                "start_price_i64": 110,
                "end_price_i64": 125,
                "direction": "up",
                "confirmed_at_bar_index": 5,
            },
        ),
        _three_level_event(
            6,
            "segment",
            "return-down",
            {
                "start_bar_index": 5,
                "end_bar_index": 6,
                "start_price_i64": 125,
                "end_price_i64": 112,
                "direction": "down",
                "confirmed_at_bar_index": 6,
            },
        ),
        _three_level_event(
            6,
            "segment_zhongshu",
            "first-target-center",
            _segmented_center(
                end_bar_index=6,
                status="left",
                leave_direction="up",
            ),
        ),
        _three_level_event(
            6,
            "trade_point",
            "first-center-B3",
            {
                "bar_index": 6,
                "price_i64": 112,
                "signal_type": "buy_3",
                "signal_class": "standard",
                "reference_object_id": "first-target-center",
                "confirmed_at_bar_index": 6,
            },
        ),
        _three_level_event(
            7,
            "segment",
            "followthrough-up",
            {
                "start_bar_index": 6,
                "end_bar_index": 7,
                "start_price_i64": 112,
                "end_price_i64": followthrough_end_i64,
                "direction": "up",
                "confirmed_at_bar_index": 7,
            },
        ),
    ]


def _segmented_short_events() -> list[object]:
    return [
        _three_level_event(
            1,
            "trade_point",
            "target-S1",
            {
                "bar_index": 1,
                "price_i64": 120,
                "signal_type": "sell_1",
                "signal_class": "standard",
                "reference_object_id": "target-top-divergence",
                "confirmed_at_bar_index": 1,
            },
        ),
        _three_level_event(
            2,
            "segment",
            "first-down",
            {
                "start_bar_index": 1,
                "end_bar_index": 2,
                "start_price_i64": 120,
                "end_price_i64": 100,
                "direction": "down",
                "confirmed_at_bar_index": 2,
            },
        ),
        _three_level_event(
            3,
            "segment",
            "counter-up",
            {
                "start_bar_index": 2,
                "end_bar_index": 3,
                "start_price_i64": 100,
                "end_price_i64": 120,
                "direction": "up",
                "confirmed_at_bar_index": 3,
            },
        ),
        _three_level_event(
            4,
            "segment",
            "third-down",
            {
                "start_bar_index": 3,
                "end_bar_index": 4,
                "start_price_i64": 120,
                "end_price_i64": 90,
                "direction": "down",
                "confirmed_at_bar_index": 4,
            },
        ),
        _three_level_event(
            4,
            "segment_zhongshu",
            "first-target-center-short",
            _segmented_center(zd_i64=100, zg_i64=120),
        ),
        _three_level_event(
            5,
            "segment",
            "departure-down",
            {
                "start_bar_index": 4,
                "end_bar_index": 5,
                "start_price_i64": 90,
                "end_price_i64": 75,
                "direction": "down",
                "confirmed_at_bar_index": 5,
            },
        ),
        _three_level_event(
            6,
            "segment",
            "return-up",
            {
                "start_bar_index": 5,
                "end_bar_index": 6,
                "start_price_i64": 75,
                "end_price_i64": 88,
                "direction": "up",
                "confirmed_at_bar_index": 6,
            },
        ),
        _three_level_event(
            6,
            "segment_zhongshu",
            "first-target-center-short",
            _segmented_center(
                zd_i64=100,
                zg_i64=120,
                end_bar_index=6,
                status="left",
                leave_direction="down",
            ),
        ),
        _three_level_event(
            6,
            "trade_point",
            "first-center-S3",
            {
                "bar_index": 6,
                "price_i64": 88,
                "signal_type": "sell_3",
                "signal_class": "standard",
                "reference_object_id": "first-target-center-short",
                "confirmed_at_bar_index": 6,
            },
        ),
        _three_level_event(
            7,
            "segment",
            "followthrough-down",
            {
                "start_bar_index": 6,
                "end_bar_index": 7,
                "start_price_i64": 88,
                "end_price_i64": 70,
                "direction": "down",
                "confirmed_at_bar_index": 7,
            },
        ),
    ]


def _run_segmented_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    events: list[object],
    *,
    dataset_id: str,
    parameter_overrides: dict[str, object] | None = None,
    last_bar_index: int | None = None,
) -> tuple[object, dict[str, object], PathGuard]:
    guard = PathGuard(tmp_path)
    dataset = tmp_path / "normalized" / dataset_id / "revision"
    dataset.mkdir(parents=True, exist_ok=True)
    closes = [100, 80, 100, 90, 110, 125, 112, 130, 132]
    pq.write_table(
        pa.table(
            {
                "bar_index": pa.array(range(len(closes)), type=pa.int64()),
                "timestamp_utc": pa.array(
                    [index * 300_000 for index in range(len(closes))], type=pa.int64()
                ),
                "open_i64": pa.array(closes, type=pa.int64()),
                "high_i64": pa.array([value + 2 for value in closes], type=pa.int64()),
                "low_i64": pa.array([value - 2 for value in closes], type=pa.int64()),
                "close_i64": pa.array(closes, type=pa.int64()),
            }
        ),
        dataset / "bars.parquet",
    )
    (dataset / "meta.json").write_text(
        '{"price":{"price_scale":1,"tick_size_i64":1}}', encoding="utf-8"
    )
    monkeypatch.setattr(
        "tvbt.strategy.run_chan",
        lambda *args, **kwargs: (
            SimpleNamespace(emitter=SimpleNamespace(events=events)),
            [],
            {},
        ),
    )
    algorithm = target_level_rebound_segmented_operation_definition()
    payload: dict[str, object] = {
        "dataset": {
            "dataset_id": dataset_id,
            "data_revision": "sha256:" + "8" * 64,
            "bars_path": f"normalized/{dataset_id}/revision/bars.parquet",
            "meta_path": f"normalized/{dataset_id}/revision/meta.json",
        },
        "algorithm": {
            key: algorithm[key]
            for key in ("kind", "algorithm_id", "algorithm_version", "source_hash")
        },
        "parameters": {
            "checkpoint_interval": 1024,
            "level_graph_profile_id": 1,
            "allow_long": True,
            "allow_short": True,
            "operation_quantity": 3,
            "partial_take_profit_quantity": 1,
            "estimated_round_trip_cost_i64": 0,
            "minimum_net_segment_i64": 1,
            "execution_available": True,
            **(parameter_overrides or {}),
        },
    }
    return (
        run_strategy(
            payload,  # type: ignore[arg-type]
            guard,
            threading.Event(),
            last_bar_index=last_bar_index,
        ),
        payload,
        guard,
    )


def test_segmented_rebound_scales_out_and_back_in_before_trend_handoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events = _segmented_long_events()
    result, payload, guard = _run_segmented_fixture(
        tmp_path,
        monkeypatch,
        events,
        dataset_id="TEST.SEGMENTED.REBOUND.5m",
    )
    assert [value["state_to"] for value in result.strategy_states] == [
        "TARGET_REBOUND_ACTIVE",
        "FIRST_LEG_PARTIAL_TAKE_PROFIT",
        "COUNTER_LEG_REENTERED",
        "TARGET_CENTER_CONFIRMED",
        "WAIT_TREND_FOLLOWTHROUGH",
        "TREND_HANDOFF",
    ]
    assert [value["action"] for value in result.trade_signals] == [
        "open_long",
        "reduce_long",
        "add_long",
    ]
    assert [value["quantity"] for value in result.trade_signals] == [3, 1, 1]
    assert [value["side"] for value in result.trade_signals] == ["long", "short", "long"]
    assert {
        "partial_take_profit",
        "reenter",
        "target_center_confirmed",
        "trend_handoff_wait",
        "trend_handoff",
    } <= {value["event_type"] for value in result.chart_events}
    assert all(
        value["assume_second_directional_leg_new_extreme"] is False
        for value in result.strategy_states
    )

    prefix = run_strategy(
        payload,  # type: ignore[arg-type]
        guard,
        threading.Event(),
        last_bar_index=6,
    )
    assert prefix.trade_signals == result.trade_signals
    assert prefix.strategy_states[-1]["state_to"] == "WAIT_TREND_FOLLOWTHROUGH"
    assert not any(value["event_type"] == "trend_handoff" for value in prefix.chart_events)

    backtest_payload = {
        **payload,
        "run_id": "run-segmented-rebound",
        "run_signature": "sha256:" + "9" * 64,
        "trace_id": "trace-segmented-rebound",
        "range": {"warmup_from_bar_index": 0, "from_bar_index": 0, "to_bar_index": 8},
        "execution": {
            "signal_timing": "bar_close",
            "fill_timing": "next_bar_open",
            "commission": {"mode": "fixed_per_contract", "amount_i64": 0, "money_scale": 1},
            "slippage": {"mode": "ticks", "value": 0},
            "contract_multiplier": 1,
            "margin_ratio": 0.1,
            "intrabar_conflict_rule": "worst_case",
        },
        "capital": {"initial_cash_i64": 1_000_000, "currency": "CNY", "money_scale": 1},
        "random_seed": 8,
        "output_path": "runs/run-segmented-rebound",
    }
    run_ref = run_backtest(
        backtest_payload,  # type: ignore[arg-type]
        guard,
        threading.Event(),
    )
    fills = pq.read_table(tmp_path / run_ref / "fills.parquet").to_pylist()
    assert [(value["action"], value["quantity"]) for value in fills] == [
        ("open_long", 3),
        ("reduce_long", 1),
        ("add_long", 1),
    ]
    positions = {
        value["bar_index"]: value
        for value in pq.read_table(tmp_path / run_ref / "positions.parquet").to_pylist()
    }
    assert positions[2]["quantity"] == 3
    assert positions[3]["quantity"] == 2
    assert positions[4]["quantity"] == 3
    trades = pq.read_table(tmp_path / run_ref / "trades.parquet").to_pylist()
    assert len(trades) == 1 and trades[0]["quantity"] == 1


def test_segmented_callback_is_symmetric_and_allows_equal_source_extreme(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result, _, _ = _run_segmented_fixture(
        tmp_path,
        monkeypatch,
        _segmented_short_events(),
        dataset_id="TEST.SEGMENTED.CALLBACK.5m",
    )
    assert [value["action"] for value in result.trade_signals] == [
        "open_short",
        "reduce_short",
        "add_short",
    ]
    assert [value["side"] for value in result.trade_signals] == ["short", "long", "short"]
    assert result.strategy_states[0]["state_to"] == "TARGET_CALLBACK_ACTIVE"
    assert result.strategy_states[-1]["state_to"] == "TREND_HANDOFF"
    reentry = next(value for value in result.chart_events if value["event_type"] == "reenter")
    assert reentry["position_quantity_after"] == 3


def test_segmented_operation_exits_on_cost_structure_third_point_and_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    filtered_result, _, _ = _run_segmented_fixture(
        tmp_path,
        monkeypatch,
        [
            _three_level_event(
                1,
                "trade_point",
                "class-B1",
                {
                    "bar_index": 1,
                    "price_i64": 80,
                    "signal_type": "class_buy_1",
                    "signal_class": "class_like",
                    "confirmed_at_bar_index": 1,
                },
            ),
            _three_level_event(
                2,
                "trade_point",
                "unfinished-B1",
                {
                    "bar_index": 2,
                    "price_i64": 79,
                    "signal_type": "buy_1",
                    "signal_class": "standard",
                    "confirmed": False,
                    "confirmed_at_bar_index": 2,
                },
            ),
        ],
        dataset_id="TEST.SEGMENTED.FILTERED.5m",
    )
    assert filtered_result.strategy_states == []
    assert filtered_result.trade_signals == []

    observe_only, _, _ = _run_segmented_fixture(
        tmp_path,
        monkeypatch,
        _segmented_long_events()[:2],
        dataset_id="TEST.SEGMENTED.OBSERVE.5m",
        parameter_overrides={"execution_available": False},
    )
    assert observe_only.trade_signals == []
    assert [value["state_to"] for value in observe_only.strategy_states] == [
        "TARGET_REBOUND_ACTIVE",
        "FIRST_LEG_PARTIAL_TAKE_PROFIT",
    ]
    partial_event = next(
        value for value in observe_only.chart_events if value["event_type"] == "partial_take_profit"
    )
    assert partial_event["executed_quantity"] == 0

    cost_events = _segmented_long_events()[:2]
    cost_result, cost_payload, cost_guard = _run_segmented_fixture(
        tmp_path,
        monkeypatch,
        cost_events,
        dataset_id="TEST.SEGMENTED.COST.5m",
        parameter_overrides={"estimated_round_trip_cost_i64": 20},
    )
    assert [value["action"] for value in cost_result.trade_signals] == [
        "open_long",
        "close_long",
    ]
    assert cost_result.strategy_states[-1]["reason_code"] == (
        "FIRST_LEG_UNFAVORABLE_AFTER_ESTIMATED_COST"
    )
    assert any(
        value["event_type"] == "unfavorable_execution_exit" for value in cost_result.chart_events
    )

    structure_events = _segmented_long_events()[:3]
    structure_events[-1] = _three_level_event(
        3,
        "segment",
        "counter-down",
        {
            "start_bar_index": 2,
            "end_bar_index": 3,
            "start_price_i64": 100,
            "end_price_i64": 79,
            "direction": "down",
            "confirmed_at_bar_index": 3,
        },
    )
    structure_result, _, _ = _run_segmented_fixture(
        tmp_path,
        monkeypatch,
        structure_events,
        dataset_id="TEST.SEGMENTED.STRUCTURE.5m",
    )
    assert [value["action"] for value in structure_result.trade_signals] == [
        "open_long",
        "reduce_long",
        "close_long",
    ]
    assert structure_result.trade_signals[-1]["quantity"] == 2
    assert structure_result.strategy_states[-1]["reason_code"] == (
        "COUNTER_LEG_BROKE_SOURCE_TURN_EXTREME"
    )

    opposite_events = _segmented_long_events()[:5]
    opposite_events.append(
        _three_level_event(
            5,
            "trade_point",
            "first-center-S3",
            {
                "bar_index": 5,
                "price_i64": 95,
                "signal_type": "sell_3",
                "signal_class": "standard",
                "reference_object_id": "first-target-center",
                "confirmed_at_bar_index": 5,
            },
        )
    )
    opposite_result, _, _ = _run_segmented_fixture(
        tmp_path,
        monkeypatch,
        opposite_events,
        dataset_id="TEST.SEGMENTED.OPPOSITE.5m",
    )
    assert opposite_result.strategy_states[-1]["reason_code"] == (
        "OPPOSITE_THIRD_POINT_INVALIDATED_SEGMENTED_OPERATION"
    )
    assert opposite_result.trade_signals[-1]["action"] == "close_long"

    failed_followthrough, failed_payload, failed_guard = _run_segmented_fixture(
        tmp_path,
        monkeypatch,
        _segmented_long_events(followthrough_end_i64=125),
        dataset_id="TEST.SEGMENTED.FOLLOWTHROUGH.5m",
    )
    assert failed_followthrough.strategy_states[-1]["reason_code"] == (
        "THIRD_POINT_FOLLOWTHROUGH_FAILED_NEW_EXTREME"
    )
    assert failed_followthrough.trade_signals[-1]["action"] == "close_long"
    monkeypatch.setattr("tvbt.backtest.run_strategy", lambda *args, **kwargs: failed_followthrough)
    failed_run_ref = run_backtest(
        {
            **failed_payload,
            "run_id": "run-segmented-followthrough-failed",
            "run_signature": "sha256:" + "7" * 64,
            "trace_id": "trace-segmented-followthrough-failed",
            "range": {"warmup_from_bar_index": 0, "from_bar_index": 0, "to_bar_index": 8},
            "execution": {
                "signal_timing": "bar_close",
                "fill_timing": "next_bar_open",
                "commission": {
                    "mode": "fixed_per_contract",
                    "amount_i64": 0,
                    "money_scale": 1,
                },
                "slippage": {"mode": "ticks", "value": 0},
                "contract_multiplier": 1,
                "margin_ratio": 0.1,
                "intrabar_conflict_rule": "worst_case",
            },
            "capital": {
                "initial_cash_i64": 1_000_000,
                "currency": "CNY",
                "money_scale": 1,
            },
            "random_seed": 8,
            "output_path": "runs/run-segmented-followthrough-failed",
        },  # type: ignore[arg-type]
        failed_guard,
        threading.Event(),
    )
    failed_run = tmp_path / failed_run_ref
    failed_fills = pq.read_table(failed_run / "fills.parquet").to_pylist()
    assert [(value["action"], value["quantity"]) for value in failed_fills] == [
        ("open_long", 3),
        ("reduce_long", 1),
        ("add_long", 1),
        ("close_long", 3),
    ]
    failed_trades = pq.read_table(failed_run / "trades.parquet").to_pylist()
    assert sorted(value["quantity"] for value in failed_trades) == [1, 1, 2]
    failed_positions = pq.read_table(failed_run / "positions.parquet").to_pylist()
    assert failed_positions[-1]["side"] == "flat" and failed_positions[-1]["quantity"] == 0

    revised_events = _segmented_long_events()[:2]
    revised_events.append(
        _three_level_event(
            3,
            "segment",
            "first-up",
            {
                "start_bar_index": 1,
                "end_bar_index": 2,
                "start_price_i64": 80,
                "end_price_i64": 101,
                "direction": "up",
                "confirmed_at_bar_index": 3,
            },
        )
    )
    revised_result, _, _ = _run_segmented_fixture(
        tmp_path,
        monkeypatch,
        revised_events,
        dataset_id="TEST.SEGMENTED.REVISED.5m",
    )
    assert revised_result.strategy_states[-1]["state_to"] == "SEGMENTED_OPERATION_RESET"
    assert revised_result.trade_signals[-1]["action"] == "close_long"
    assert any(
        value["event_type"] == "segmented_operation_reset" for value in revised_result.chart_events
    )

    departure_revised_events = _segmented_long_events()[:9]
    departure_revised_events.append(
        _three_level_event(
            7,
            "segment",
            "departure-up",
            {
                "start_bar_index": 4,
                "end_bar_index": 5,
                "start_price_i64": 110,
                "end_price_i64": 126,
                "direction": "up",
                "confirmed_at_bar_index": 7,
            },
        )
    )
    departure_revised, _, _ = _run_segmented_fixture(
        tmp_path,
        monkeypatch,
        departure_revised_events,
        dataset_id="TEST.SEGMENTED.DEPARTURE.REVISED.5m",
    )
    assert departure_revised.strategy_states[-1]["state_to"] == ("SEGMENTED_OPERATION_RESET")
    assert departure_revised.trade_signals[-1]["action"] == "close_long"

    late_divergence_events = _segmented_long_events()
    late_divergence_events.append(
        _three_level_event(
            8,
            "divergence",
            "late-followthrough-top-divergence",
            {
                "bar_index": 7,
                "price_i64": 130,
                "signal_type": "top_divergence",
                "divergence_kind": "trend",
                "reference_object_id": "followthrough-up",
                "confirmed_at_bar_index": 8,
            },
        )
    )
    late_divergence, _, _ = _run_segmented_fixture(
        tmp_path,
        monkeypatch,
        late_divergence_events,
        dataset_id="TEST.SEGMENTED.LATE.DIVERGENCE.5m",
    )
    assert late_divergence.strategy_states[-2]["state_to"] == "TREND_HANDOFF"
    assert late_divergence.strategy_states[-1]["reason_code"] == (
        "TREND_HANDOFF_FOLLOWTHROUGH_DIVERGENCE_CONFIRMED"
    )
    assert late_divergence.trade_signals[-1]["action"] == "close_long"

    with pytest.raises(ValueError, match="partial_take_profit_quantity"):
        run_strategy(
            {
                **cost_payload,
                "parameters": {
                    **cost_payload["parameters"],  # type: ignore[dict-item]
                    "partial_take_profit_quantity": 3,
                },
            },
            cost_guard,
            threading.Event(),
        )


def _construction_center(start_bar_index: int, end_bar_index: int) -> dict[str, object]:
    return {
        "start_bar_index": start_bar_index,
        "end_bar_index": end_bar_index,
        "zd_i64": 90,
        "zg_i64": 110,
        "z_i64": 100,
        "analysis_level": "segment",
        "component_kind": "segment",
        "component_count": 3,
        "confirmed_at_bar_index": end_bar_index,
    }


def _run_construction_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    events: list[object],
    *,
    dataset_id: str,
    bars: list[tuple[int, int, int]] | None = None,
    last_bar_index: int | None = None,
    parameter_overrides: dict[str, object] | None = None,
) -> tuple[object, dict[str, object], PathGuard]:
    guard = PathGuard(tmp_path)
    dataset = tmp_path / "normalized" / dataset_id / "revision"
    dataset.mkdir(parents=True, exist_ok=True)
    price_rows = bars or [(100, 98, 100) for _ in range(12)]
    closes = [row[2] for row in price_rows]
    pq.write_table(
        pa.table(
            {
                "bar_index": pa.array(range(len(price_rows)), type=pa.int64()),
                "timestamp_utc": pa.array(
                    [index * 300_000 for index in range(len(price_rows))], type=pa.int64()
                ),
                "open_i64": pa.array(closes, type=pa.int64()),
                "high_i64": pa.array([row[0] for row in price_rows], type=pa.int64()),
                "low_i64": pa.array([row[1] for row in price_rows], type=pa.int64()),
                "close_i64": pa.array(closes, type=pa.int64()),
            }
        ),
        dataset / "bars.parquet",
    )
    (dataset / "meta.json").write_text(
        '{"price":{"price_scale":1,"tick_size_i64":1}}', encoding="utf-8"
    )
    monkeypatch.setattr(
        "tvbt.strategy.run_chan",
        lambda *args, **kwargs: (
            SimpleNamespace(emitter=SimpleNamespace(events=events)),
            [],
            {},
        ),
    )
    algorithm = bottom_top_construction_definition()
    payload: dict[str, object] = {
        "dataset": {
            "dataset_id": dataset_id,
            "data_revision": "sha256:" + "a" * 64,
            "bars_path": f"normalized/{dataset_id}/revision/bars.parquet",
            "meta_path": f"normalized/{dataset_id}/revision/meta.json",
        },
        "algorithm": {
            key: algorithm[key]
            for key in ("kind", "algorithm_id", "algorithm_version", "source_hash")
        },
        "parameters": {
            "checkpoint_interval": 1024,
            "level_graph_profile_id": 1,
            "allow_long": True,
            "allow_short": True,
            "operation_quantity": 2,
            "execution_available": True,
            "coarse_effective_hold_bars": 1,
            **(parameter_overrides or {}),
        },
    }
    return (
        run_strategy(
            payload,  # type: ignore[arg-type]
            guard,
            threading.Event(),
            last_bar_index=last_bar_index,
        ),
        payload,
        guard,
    )


def test_bottom_top_construction_precise_success_handoff_revision_and_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events = [
        _three_level_event(
            1,
            "trade_point",
            "standard-B1",
            {
                "bar_index": 1,
                "price_i64": 80,
                "signal_type": "buy_1",
                "signal_class": "standard",
                "confirmed_at_bar_index": 1,
            },
        ),
        _three_level_event(
            2,
            "segment_zhongshu",
            "unrelated-center",
            _construction_center(0, 2),
        ),
        _three_level_event(
            4,
            "segment_zhongshu",
            "bottom-center",
            _construction_center(1, 4),
        ),
        _three_level_event(
            5,
            "segment_zhongshu",
            "bottom-center",
            {
                **_construction_center(1, 5),
                "confirmed_at_bar_index": 4,
                "status": "left",
                "leave_direction": "up",
            },
        ),
        _three_level_event(
            6,
            "trade_point",
            "bottom-first-B3",
            {
                "bar_index": 6,
                "price_i64": 112,
                "signal_type": "buy_3",
                "signal_class": "standard",
                "reference_object_id": "bottom-center",
                "confirmed_at_bar_index": 6,
            },
        ),
        _three_level_event(
            7,
            "trade_point",
            "standard-S1",
            {
                "bar_index": 7,
                "price_i64": 125,
                "signal_type": "sell_1",
                "signal_class": "standard",
                "confirmed_at_bar_index": 7,
            },
        ),
        _three_level_event(
            8,
            "segment_zhongshu",
            "top-center",
            _construction_center(7, 8),
        ),
        _three_level_event(
            9,
            "trade_point",
            "top-first-S3",
            {
                "bar_index": 9,
                "price_i64": 88,
                "signal_type": "sell_3",
                "signal_class": "standard",
                "reference_object_id": "top-center",
                "confirmed_at_bar_index": 9,
            },
        ),
        _three_level_event(
            10,
            "trade_point",
            "standard-S1",
            {
                "bar_index": 7,
                "price_i64": 126,
                "signal_type": "sell_1",
                "signal_class": "standard",
                "confirmed_at_bar_index": 10,
            },
        ),
    ]
    result, payload, guard = _run_construction_fixture(
        tmp_path,
        monkeypatch,
        events,
        dataset_id="TEST.BOTTOM.TOP.PRECISE.5m",
    )
    assert [value["state_to"] for value in result.strategy_states] == [
        "BOTTOM_BUILDING",
        "BOTTOM_RESULTING_CENTER_CONFIRMED",
        "BOTTOM_BUILD_SUCCESS",
        "BOTTOM_TOP_CONSTRUCTION_RESET",
        "TOP_BUILDING",
        "TOP_RESULTING_CENTER_CONFIRMED",
        "TOP_BUILD_SUCCESS",
        "BOTTOM_TOP_CONSTRUCTION_RESET",
    ]
    assert [value["action"] for value in result.trade_signals] == [
        "open_long",
        "close_long",
        "open_short",
        "close_short",
    ]
    assert {value["event_type"] for value in result.chart_events} >= {
        "bottom_building",
        "bottom_resulting_center",
        "bottom_build_success",
        "top_building",
        "top_resulting_center",
        "top_build_success",
        "bottom_top_construction_handoff",
        "bottom_top_construction_reset",
    }
    prefix = run_strategy(
        payload,  # type: ignore[arg-type]
        guard,
        threading.Event(),
        last_bar_index=5,
    )
    assert prefix.strategy_states == result.strategy_states[:2]
    assert [value["action"] for value in prefix.trade_signals] == ["open_long"]


def test_bottom_top_construction_failure_filters_nonstandard_points(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events = [
        _three_level_event(
            0,
            "trade_point",
            "class-B1",
            {
                "bar_index": 0,
                "price_i64": 78,
                "signal_type": "class_buy_1",
                "signal_class": "class_like",
            },
        ),
        _three_level_event(
            1,
            "trade_point",
            "unfinished-B1",
            {
                "bar_index": 1,
                "price_i64": 79,
                "signal_type": "buy_1",
                "signal_class": "standard",
                "confirmed": False,
            },
        ),
        _three_level_event(
            2,
            "trade_point",
            "standard-B1",
            {
                "bar_index": 2,
                "price_i64": 80,
                "signal_type": "buy_1",
                "signal_class": "standard",
            },
        ),
        _three_level_event(
            4,
            "segment_zhongshu",
            "bottom-center",
            _construction_center(2, 4),
        ),
        _three_level_event(
            5,
            "trade_point",
            "class-S3",
            {
                "bar_index": 5,
                "price_i64": 89,
                "signal_type": "class_sell_3",
                "signal_class": "class_like",
                "reference_object_id": "bottom-center",
            },
        ),
        _three_level_event(
            6,
            "trade_point",
            "bottom-first-S3",
            {
                "bar_index": 6,
                "price_i64": 88,
                "signal_type": "sell_3",
                "signal_class": "standard",
                "reference_object_id": "bottom-center",
            },
        ),
    ]
    result, _, _ = _run_construction_fixture(
        tmp_path,
        monkeypatch,
        events,
        dataset_id="TEST.BOTTOM.FAILURE.5m",
    )
    assert [value["state_to"] for value in result.strategy_states] == [
        "BOTTOM_BUILDING",
        "BOTTOM_RESULTING_CENTER_CONFIRMED",
        "BOTTOM_BUILD_FAILED",
    ]
    assert [value["action"] for value in result.trade_signals] == [
        "open_long",
        "close_long",
    ]
    assert result.trade_signals[-1]["reason_code"] == (
        "FIRST_RESULTING_CENTER_SELL_3_FAILED_BOTTOM"
    )


def test_bottom_top_construction_coarse_zones_use_strict_boundaries_without_trades(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events = [
        _three_level_event(
            1,
            "fractal",
            "coarse-bottom",
            {
                "bar_index": 0,
                "price_i64": 80,
                "zone_low_i64": 80,
                "zone_high_i64": 90,
                "fractal_type": "bottom",
                "confirmed_at_bar_index": 1,
            },
        ),
        _three_level_event(
            4,
            "fractal",
            "coarse-top",
            {
                "bar_index": 3,
                "price_i64": 120,
                "zone_low_i64": 110,
                "zone_high_i64": 120,
                "fractal_type": "top",
                "confirmed_at_bar_index": 4,
            },
        ),
        _three_level_event(
            7,
            "fractal",
            "coarse-bottom-failed",
            {
                "bar_index": 6,
                "price_i64": 80,
                "zone_low_i64": 80,
                "zone_high_i64": 90,
                "fractal_type": "bottom",
                "confirmed_at_bar_index": 7,
            },
        ),
    ]
    bars = [
        (100, 98, 99),
        (90, 80, 85),
        (92, 80, 90),
        (93, 81, 91),
        (120, 110, 115),
        (120, 108, 110),
        (119, 107, 109),
        (90, 80, 85),
        (89, 79, 84),
    ]
    result, _, _ = _run_construction_fixture(
        tmp_path,
        monkeypatch,
        events,
        bars=bars,
        dataset_id="TEST.BOTTOM.TOP.COARSE.5m",
    )
    assert result.trade_signals == []
    event_types = [value["event_type"] for value in result.chart_events]
    assert event_types == [
        "coarse_bottom_zone",
        "coarse_bottom_success",
        "coarse_top_zone",
        "coarse_top_success",
        "coarse_bottom_zone",
        "coarse_bottom_failure",
    ]
    assert all(value["coarse_zone_executes_trade"] is False for value in result.chart_events)


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
    sample = Path(__file__).parents[2] / "trading-data" / "history" / "30#AOL9.txt"
    if not sample.is_file():
        pytest.skip(f"唯一历史数据源中不存在完整测试文件：{sample}")
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
    algorithms_and_parameters = [
        (algorithm, {"checkpoint_interval": 1024})
        for algorithm in (
            downtrend_reversal_definition(),
            trend_divergence_reversal_definition(),
            consolidation_reversion_definition(),
            third_point_migration_definition(),
            first_centre_rotation_definition(),
        )
    ]
    algorithms_and_parameters.append(
        (
            second_buy_only_definition(),
            {
                "checkpoint_interval": 1024,
                "allow_strongest": True,
                "allow_normal": True,
                "allow_weakest": True,
                "strongest_quantity": 2,
                "normal_quantity": 2,
                "weakest_quantity": 1,
            },
        )
    )
    algorithms_and_parameters.append(
        (
            third_buy_only_definition(),
            {
                "checkpoint_interval": 1024,
                "allow_late_center": True,
                "first_center_quantity": 2,
                "late_center_quantity": 1,
                "minimum_entry_volume": 0,
            },
        )
    )
    algorithms_and_parameters.append(
        (
            centre_oscillation_spread_definition(),
            {
                "checkpoint_interval": 1024,
                "allow_long": True,
                "allow_short": True,
                "strong_quantity": 2,
                "neutral_quantity": 1,
                "weak_quantity": 1,
                "estimated_round_trip_cost_i64": 0,
                "minimum_net_range_i64": 1,
                "fast_execution_available": False,
                "max_entries_per_center": 4,
            },
        )
    )
    algorithms_and_parameters.append(
        (
            same_level_decomposition_program_definition(),
            {
                "checkpoint_interval": 1024,
                "odd_direction_is_down": True,
                "allow_long": True,
                "allow_short": True,
                "operation_quantity": 1,
            },
        )
    )
    algorithms_and_parameters.append(
        (
            three_level_complete_classification_definition(),
            {
                "checkpoint_interval": 1024,
                "level_graph_profile_id": 1,
                "allow_long": True,
                "allow_short": True,
                "operation_quantity": 1,
                "can_handle_mid_third_point": True,
                "can_handle_mid_center_continue": True,
                "can_handle_high_change_candidate": True,
            },
        )
    )
    algorithms_and_parameters.append(
        (
            target_level_rebound_segmented_operation_definition(),
            {
                "checkpoint_interval": 1024,
                "level_graph_profile_id": 1,
                "allow_long": True,
                "allow_short": True,
                "operation_quantity": 2,
                "partial_take_profit_quantity": 1,
                "estimated_round_trip_cost_i64": 0,
                "minimum_net_segment_i64": 1,
                "execution_available": True,
            },
        )
    )
    algorithms_and_parameters.append(
        (
            bottom_top_construction_definition(),
            {
                "checkpoint_interval": 1024,
                "level_graph_profile_id": 1,
                "allow_long": True,
                "allow_short": True,
                "operation_quantity": 1,
                "execution_available": True,
                "coarse_effective_hold_bars": 1,
            },
        )
    )
    for algorithm, parameters in algorithms_and_parameters:
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
                "parameters": parameters,
            },
            guard,
            threading.Event(),
        )
        assert len(strategy_result.bars) == 5000
        assert all(
            event["known_at_bar_index"] >= 0 and event["event_seq"] == index + 1
            for index, event in enumerate(strategy_result.events)
        )
