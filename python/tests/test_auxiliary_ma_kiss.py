from __future__ import annotations

import json
import threading
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from tvbt.algorithms import definitions as algorithm_definitions
from tvbt.auxiliary.ma_kiss import (
    AuxMaKissSeries,
    MaKissConfig,
    classify_ma_kisses,
    definition,
)
from tvbt.backtest import run_backtest
from tvbt.storage.path_guard import PathGuard
from tvbt.strategy import StrategyBar, run_strategy


def config(*, macd: bool = True) -> MaKissConfig:
    return MaKissConfig(
        short_period=2,
        long_period=3,
        proximity_i64=1,
        flat_slope_i64=0,
        enable_legacy_b1_macd_proxy=macd,
        macd_fast_period=2,
        macd_slow_period=3,
        macd_signal_period=2,
        legacy_divergence_min_bars=1,
    )


def bars(count: int, lows: list[int] | None = None) -> list[StrategyBar]:
    low_values = lows or [100 - index for index in range(count)]
    return [
        StrategyBar(
            bar_index=index,
            timestamp_utc=1_700_000_000_000 + index * 60_000,
            open_i64=100,
            high_i64=110,
            low_i64=low_values[index],
            close_i64=100,
        )
        for index in range(count)
    ]


def test_definition_is_an_explicit_non_trading_auxiliary_catalog_adapter() -> None:
    value = definition()
    assert any(item["algorithm_id"] == "aux_ma_kiss_legacy" for item in algorithm_definitions())
    assert value["algorithm_id"] == "aux_ma_kiss_legacy"
    assert value["algorithm_version"] == "1.0.0"
    assert value["kind"] == "strategy"
    assert value["name"] == "辅助·均线“吻”旧系统（候选不交易）"
    assert {output["name"] for output in value["outputs"]} == {
        "aux_flying_kiss",
        "aux_lip_kiss",
        "aux_wet_kiss",
        "aux_legacy_B1_candidate",
        "aux_legacy_B2_candidate",
    }
    assert all(output["name"].startswith("aux_") for output in value["outputs"])


def test_flying_lip_and_wet_kisses_are_causally_confirmed_and_only_first_bullish_kiss_is_b2() -> (
    None
):
    values = AuxMaKissSeries(
        short_ma=[12, 12, 13, 10.5, 12, 9.5, 10.5, 12],
        long_ma=[10] * 8,
        macd_histogram=[None] * 8,
    )
    events = classify_ma_kisses(bars(8), values, config())
    assert [event.event_type for event in events] == [
        "aux_flying_kiss",
        "aux_legacy_B2_candidate",
        "aux_lip_kiss",
        "aux_wet_kiss",
    ]
    kisses = [event for event in events if event.details.get("kiss_type")]
    assert [event.known_at_bar_index for event in kisses] == [2, 2, 4, 7]
    assert [event.details["kiss_order"] for event in kisses] == [1, 1, 2, 3]
    candidate = next(event for event in events if event.event_type.endswith("B2_candidate"))
    assert candidate.details == {
        "catalog_algorithm_id": "ALG-AUX-001",
        "semantic_namespace": "auxiliary",
        "evidence_level": "AUXILIARY",
        "legacy_system": True,
        "standard_signal": False,
        "execution_allowed": False,
        "candidate_only": True,
        "legacy_candidate": "B2",
        "regime_id": 1,
        "kiss_order": 1,
        "kiss_type": "flying",
    }


def test_legacy_b1_requires_lower_low_and_weaker_macd_after_latest_bearish_kiss() -> None:
    values = AuxMaKissSeries(
        short_ma=[8, 8, 7, 6, 5],
        long_ma=[10] * 5,
        macd_histogram=[None, None, None, -4, -2],
    )
    events = classify_ma_kisses(bars(5), values, config())
    assert [event.event_type for event in events] == [
        "aux_flying_kiss",
        "aux_legacy_B1_candidate",
    ]
    candidate = events[-1]
    assert candidate.known_at_bar_index == 4
    assert candidate.details["candidate_only"] is True
    assert candidate.details["standard_signal"] is False
    assert candidate.details["reference_macd_histogram"] == -4
    assert candidate.details["current_macd_histogram"] == -2

    disabled = classify_ma_kisses(bars(5), values, config(macd=False))
    assert [event.event_type for event in disabled] == ["aux_flying_kiss"]


def test_equal_proximity_boundary_stays_inside_lip_episode_and_prefixes_are_invariant() -> None:
    values = AuxMaKissSeries(
        short_ma=[12, 11, 12, 9, 8],
        long_ma=[10] * 5,
        macd_histogram=[None] * 5,
    )
    complete = classify_ma_kisses(bars(5), values, config())
    assert complete[0].event_type == "aux_lip_kiss"
    assert complete[0].known_at_bar_index == 2
    for length in range(1, 6):
        prefix_values = AuxMaKissSeries(
            short_ma=values.short_ma[:length],
            long_ma=values.long_ma[:length],
            macd_histogram=values.macd_histogram[:length],
        )
        prefix = classify_ma_kisses(bars(length), prefix_values, config())
        expected = [event for event in complete if event.known_at_bar_index < length]
        assert prefix == expected


def test_runner_reuses_indicator_series_and_publishes_only_aux_chart_events(tmp_path: Path) -> None:
    dataset = tmp_path / "normalized" / "TEST.AUX.1m" / "revision"
    dataset.mkdir(parents=True)
    closes = [10, 12, 14, 12, 16, 14, 12, 14, 16]
    pq.write_table(
        pa.table(
            {
                "bar_index": pa.array(range(len(closes)), type=pa.int64()),
                "timestamp_utc": pa.array(
                    [1_700_000_000_000 + index * 60_000 for index in range(len(closes))],
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
        json.dumps({"price": {"price_scale": 1, "tick_size_i64": 1}}), encoding="utf-8"
    )
    algorithm = definition()
    parameters = {
        "short_period": 2,
        "long_period": 3,
        "proximity_ticks": 0,
        "flat_slope_ticks": 0,
        "enable_legacy_b1_macd_proxy": True,
        "macd_fast_period": 2,
        "macd_slow_period": 3,
        "macd_signal_period": 2,
        "legacy_divergence_min_bars": 1,
    }
    payload = {
        "dataset": {
            "dataset_id": "TEST.AUX.1m",
            "data_revision": "sha256:" + "1" * 64,
            "bars_path": "normalized/TEST.AUX.1m/revision/bars.parquet",
            "meta_path": "normalized/TEST.AUX.1m/revision/meta.json",
        },
        "algorithm": {
            key: algorithm[key]
            for key in ("kind", "algorithm_id", "algorithm_version", "source_hash")
        },
        "parameters": parameters,
    }
    full = run_strategy(payload, PathGuard(tmp_path), threading.Event())
    prefix = run_strategy(payload, PathGuard(tmp_path), threading.Event(), last_bar_index=4)
    assert full.chart_events
    assert full.trade_signals == full.strategy_states == full.stage_signals == []
    assert {event["object_type"] for event in full.events} == {"chart_event"}
    assert all(event["event_type"].startswith("aux_") for event in full.chart_events)
    assert all(event["standard_signal"] is False for event in full.chart_events)
    assert prefix.events == [event for event in full.events if event["known_at_bar_index"] <= 4]
    assert [row["ma"] for row in full.indicator_values[:2]] == [None, 11.0]

    run_ref = run_backtest(
        {
            **payload,
            "run_id": "run-aux-ma-kiss",
            "run_signature": "sha256:" + "2" * 64,
            "trace_id": "trace-aux-ma-kiss",
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
            "output_path": "runs/run-aux-ma-kiss",
        },
        PathGuard(tmp_path),
        threading.Event(),
    )
    run_path = tmp_path / run_ref
    assert pq.read_table(run_path / "trades.parquet").num_rows == 0
    assert pq.read_table(run_path / "trade_signals.parquet").num_rows == 0
    manifest = json.loads((run_path / "run.json").read_text(encoding="utf-8"))
    assert [item["algorithm_id"] for item in manifest["strategy"]["indicator_dependencies"]] == [
        "ma",
        "macd",
    ]
    assert (run_path / "_SUCCESS").is_file()


@pytest.mark.parametrize(
    ("patch", "message"),
    [
        ({"short_period": 10}, "short_period must be less than long_period"),
        ({"macd_fast_period": 26}, "macd_fast_period must be less than macd_slow_period"),
        ({"enable_legacy_b1_macd_proxy": 1}, "must be a boolean"),
    ],
)
def test_parameter_relations_fail_explicitly(patch: dict[str, object], message: str) -> None:
    parameters: dict[str, object] = {
        "short_period": 5,
        "long_period": 10,
        "proximity_ticks": 1,
        "flat_slope_ticks": 1,
        "enable_legacy_b1_macd_proxy": True,
        "macd_fast_period": 12,
        "macd_slow_period": 26,
        "macd_signal_period": 9,
        "legacy_divergence_min_bars": 1,
    }
    parameters.update(patch)
    with pytest.raises(ValueError, match=message):
        MaKissConfig.from_parameters(parameters, 1)
