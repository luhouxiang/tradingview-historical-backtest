from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from tvbt.algorithms import definitions as algorithm_definitions
from tvbt.auxiliary.boll_bardo import (
    BardoContext,
    BollBardoConfig,
    BollSeries,
    classify_boll_bardo,
    definition,
    derive_bardo_contexts,
)
from tvbt.backtest import run_backtest
from tvbt.storage.path_guard import PathGuard
from tvbt.strategy import StrategyBar, run_strategy


def config(
    *,
    failed_reentry_bars: int = 2,
    contraction_bars: int = 2,
) -> BollBardoConfig:
    return BollBardoConfig(
        checkpoint_interval=64,
        observation_timeframe_minutes=5,
        level_mapping_profile_id=1,
        boll_period=3,
        boll_standard_deviations=1.0,
        effective_reentry_bars=2,
        failed_reentry_confirm_bars=failed_reentry_bars,
        band_turn_confirm_bars=1,
        band_turn_min_change_i64=0,
        contraction_confirm_bars=contraction_bars,
        contraction_min_width_drop_i64=0,
        tick_size_i64=1,
        dataset_timeframe="5m",
    )


def bars(
    closes: list[int],
    *,
    highs: list[int] | None = None,
    lows: list[int] | None = None,
) -> list[StrategyBar]:
    high_values = highs or [value + 1 for value in closes]
    low_values = lows or [value - 1 for value in closes]
    return [
        StrategyBar(
            bar_index=index,
            timestamp_utc=1_700_000_000_000 + index * 300_000,
            open_i64=close,
            high_i64=high_values[index],
            low_i64=low_values[index],
            close_i64=close,
        )
        for index, close in enumerate(closes)
    ]


def test_definition_registers_only_non_trading_auxiliary_outputs() -> None:
    value = definition()
    assert value["algorithm_id"] == "aux_boll_bardo_warning"
    assert value["algorithm_version"] == "1.0.0"
    assert value["kind"] == "strategy"
    assert value["name"] == "辅助·BOLL中阴判断（预警不交易）"
    assert {output["name"] for output in value["outputs"]} == {
        "aux_boll_superstrong_exit",
        "aux_boll_second_buy_zone",
        "aux_boll_second_sell_zone",
        "aux_boll_bardo_end_or_promotion_warning",
    }
    assert any(item["algorithm_id"] == value["algorithm_id"] for item in algorithm_definitions())


def test_superstrong_exit_requires_new_extreme_failed_reentry_and_yields_second_zones() -> None:
    values = bars(
        [12, 9, 10, 10, 9, 7, 9, 8, 8, 9],
        highs=[12, 10, 13, 12, 10, 8, 10, 9, 9, 10],
        lows=[11, 8, 9, 9, 8, 7, 8, 6, 7, 8],
    )
    series = BollSeries(
        middle=[8.0] * 10,
        upper=[10.0, 10.0, 10.0, 10.0, 9.0, 10.0, 10.0, 10.0, 10.0, 10.0],
        lower=[5.0, 5.0, 5.0, 5.0, 5.0, 8.0, 8.0, 8.0, 8.0, 9.0],
    )
    events = classify_boll_bardo(values, series, [None] * len(values), config())
    assert [event.event_type for event in events] == [
        "aux_boll_superstrong_exit",
        "aux_boll_second_sell_zone",
        "aux_boll_superstrong_exit",
        "aux_boll_second_buy_zone",
    ]
    assert [event.known_at_bar_index for event in events] == [3, 4, 8, 9]
    assert [event.bar_index for event in events] == [2, 4, 7, 9]
    assert events[0].details["old_superstrong_direction"] == "up"
    assert events[1].details["standard_second_point"] is False
    assert events[2].details["old_superstrong_direction"] == "down"
    assert all(event.details["standard_signal"] is False for event in events)
    assert all(event.details["execution_allowed"] is False for event in events)


def test_equal_band_is_inside_but_effective_strict_reentry_cancels_candidate() -> None:
    values = bars(
        [10, 11, 10, 11, 11],
        highs=[10, 11, 10, 12, 13],
        lows=[9, 10, 9, 10, 10],
    )
    series = BollSeries(
        middle=[9.0] * 5,
        upper=[10.0] * 5,
        lower=[5.0] * 5,
    )
    assert classify_boll_bardo(values, series, [None] * 5, config()) == []

    return_bar_extreme = bars([11, 10], highs=[11, 12], lows=[10, 9])
    return_bar_series = BollSeries(
        middle=[8.0, 8.0],
        upper=[10.0, 10.0],
        lower=[5.0, 5.0],
    )
    events = classify_boll_bardo(
        return_bar_extreme,
        return_bar_series,
        [None, None],
        config(failed_reentry_bars=1),
    )
    assert [(event.bar_index, event.known_at_bar_index) for event in events] == [(1, 1)]


def test_contraction_warns_only_inside_confirmed_structural_bardo_context() -> None:
    values = bars([10] * 6)
    series = BollSeries(
        middle=[10.0] * 6,
        upper=[16.0, 15.0, 14.0, 14.0, 13.0, 12.0],
        lower=[4.0, 5.0, 6.0, 6.0, 7.0, 8.0],
    )
    context = BardoContext("divergence-1", 0, "down")
    events = classify_boll_bardo(values, series, [context] * 6, config())
    warnings = [
        event for event in events if event.event_type == "aux_boll_bardo_end_or_promotion_warning"
    ]
    assert [event.known_at_bar_index for event in warnings] == [2, 5]
    assert warnings[0].reference_object_id == "divergence-1"
    assert warnings[0].details["confirms_third_point"] is False
    assert warnings[0].details["structural_bardo_context"] is True
    assert classify_boll_bardo(values, series, [None] * 6, config()) == []


@dataclass(frozen=True)
class Event:
    event_seq: int
    known_at_bar_index: int
    object_type: str
    object_id: str
    operation: str
    payload_json: str


def event(
    sequence: int,
    bar_index: int,
    object_type: str,
    object_id: str,
    payload: dict[str, object] | None,
) -> Event:
    return Event(
        sequence,
        bar_index,
        object_type,
        object_id,
        "delete" if payload is None else "upsert",
        json.dumps(payload or {}),
    )


def test_bardo_context_uses_confirmed_preceding_facts_and_resolves_causally() -> None:
    values = bars([10] * 6)
    structural_events = [
        event(
            1,
            1,
            "divergence",
            "div-bottom",
            {
                "confirmed": True,
                "divergence_kind": "trend",
                "signal_type": "bottom_divergence",
            },
        ),
        event(
            2,
            3,
            "trade_point",
            "buy-3",
            {"confirmed": True, "signal_class": "standard", "signal_type": "buy_3"},
        ),
        event(
            3,
            4,
            "divergence",
            "div-top",
            {
                "confirmed": True,
                "divergence_kind": "trend",
                "signal_type": "top_divergence",
            },
        ),
        event(4, 5, "divergence", "div-top", None),
    ]
    contexts = derive_bardo_contexts(values, structural_events)
    assert [None if item is None else item.source_object_id for item in contexts] == [
        None,
        "div-bottom",
        "div-bottom",
        None,
        "div-top",
        None,
    ]
    assert contexts[1] is not None and contexts[1].old_direction == "down"
    assert contexts[4] is not None and contexts[4].old_direction == "up"


def test_every_prefix_preserves_event_time_and_unfinished_candidates() -> None:
    values = bars(
        [12, 9, 10, 10, 9],
        highs=[12, 10, 13, 12, 10],
        lows=[11, 8, 9, 9, 8],
    )
    series = BollSeries(
        middle=[8.0] * 5,
        upper=[10.0, 10.0, 10.0, 10.0, 9.0],
        lower=[5.0] * 5,
    )
    complete = classify_boll_bardo(values, series, [None] * 5, config())
    assert complete[0].bar_index == 2
    assert complete[0].known_at_bar_index == 3
    for length in range(1, 6):
        prefix = classify_boll_bardo(
            values[:length],
            BollSeries(
                series.middle[:length],
                series.upper[:length],
                series.lower[:length],
            ),
            [None] * length,
            config(),
        )
        assert prefix == [event for event in complete if event.known_at_bar_index < length]


def test_fixed_timeframe_and_profile_parameters_fail_explicitly() -> None:
    parameters: dict[str, object] = {
        "checkpoint_interval": 64,
        "observation_timeframe_minutes": 30,
        "level_mapping_profile_id": 1,
        "boll_period": 20,
        "boll_stddev_milli": 2000,
        "effective_reentry_bars": 2,
        "failed_reentry_confirm_bars": 2,
        "band_turn_confirm_bars": 1,
        "band_turn_min_change_ticks": 0,
        "contraction_confirm_bars": 3,
        "contraction_min_width_drop_ticks": 0,
    }
    with pytest.raises(ValueError, match="must equal the fixed"):
        BollBardoConfig.from_parameters(parameters, 1, "5m")
    parameters["observation_timeframe_minutes"] = 5
    parameters["level_mapping_profile_id"] = 2
    with pytest.raises(ValueError, match="between 1 and 1"):
        BollBardoConfig.from_parameters(parameters, 1, "5m")


def test_runner_and_formal_run_publish_boll_events_without_trades(tmp_path: Path) -> None:
    dataset = tmp_path / "normalized" / "TEST.BOLL.5m" / "revision"
    dataset.mkdir(parents=True)
    closes = [10, 10, 20, 16, 21, 18, 17, 16, 15, 14]
    pq.write_table(
        pa.table(
            {
                "bar_index": pa.array(range(len(closes)), type=pa.int64()),
                "timestamp_utc": pa.array(
                    [1_700_000_000_000 + index * 300_000 for index in range(len(closes))],
                    type=pa.int64(),
                ),
                "trading_day": pa.array(["2026-01-05"] * len(closes), type=pa.string()),
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
        "checkpoint_interval": 64,
        "observation_timeframe_minutes": 5,
        "level_mapping_profile_id": 1,
        "boll_period": 3,
        "boll_stddev_milli": 1000,
        "effective_reentry_bars": 2,
        "failed_reentry_confirm_bars": 2,
        "band_turn_confirm_bars": 1,
        "band_turn_min_change_ticks": 0,
        "contraction_confirm_bars": 2,
        "contraction_min_width_drop_ticks": 0,
    }
    payload = {
        "dataset": {
            "dataset_id": "TEST.BOLL.5m",
            "data_revision": "sha256:" + "1" * 64,
            "bars_path": "normalized/TEST.BOLL.5m/revision/bars.parquet",
            "meta_path": "normalized/TEST.BOLL.5m/revision/meta.json",
        },
        "algorithm": {
            key: algorithm[key]
            for key in ("kind", "algorithm_id", "algorithm_version", "source_hash")
        },
        "parameters": parameters,
    }
    full = run_strategy(payload, PathGuard(tmp_path), threading.Event())
    prefix = run_strategy(payload, PathGuard(tmp_path), threading.Event(), last_bar_index=5)
    assert full.chart_events
    assert full.trade_signals == full.strategy_states == full.stage_signals == []
    assert all(event["event_type"].startswith("aux_boll_") for event in full.chart_events)
    assert prefix.events == [event for event in full.events if event["known_at_bar_index"] <= 5]

    run_ref = run_backtest(
        {
            **payload,
            "run_id": "run-aux-boll",
            "run_signature": "sha256:" + "2" * 64,
            "trace_id": "trace-aux-boll",
            "range": {"warmup_from_bar_index": 0, "from_bar_index": 0, "to_bar_index": 9},
            "execution": {
                "semantic_version": "1.0.0",
                "signal_timing": "bar_close",
                "fill_timing": "next_bar_open",
                "commission": {
                    "mode": "fixed_per_contract",
                    "amount_i64": 0,
                    "money_scale": 100,
                },
                "slippage": {"mode": "ticks", "value": 0},
                "contract_multiplier": 1,
                "contract_multiplier_source": "instrument_config",
                "margin_ratio": 0.1,
                "intrabar_conflict_rule": "worst_case",
                "stress_scenario_id": "baseline",
                "cost_multiplier": 1.0,
                "additional_slippage_ticks": 0.0,
                "additional_delay_bars": 0,
                "fill_mode": "unlimited",
            },
            "capital": {
                "initial_cash_i64": 1_000_000,
                "currency": "CNY",
                "money_scale": 100,
            },
            "random_seed": 20260821,
            "output_path": "runs/run-aux-boll",
        },
        PathGuard(tmp_path),
        threading.Event(),
    )
    run_path = tmp_path / run_ref
    assert pq.read_table(run_path / "trades.parquet").num_rows == 0
    assert pq.read_table(run_path / "trade_signals.parquet").num_rows == 0
    manifest = json.loads((run_path / "run.json").read_text(encoding="utf-8"))
    assert [item["algorithm_id"] for item in manifest["strategy"]["indicator_dependencies"]] == [
        "boll"
    ]
    assert (run_path / "_SUCCESS").is_file()
