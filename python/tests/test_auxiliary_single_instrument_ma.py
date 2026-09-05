from __future__ import annotations

from dataclasses import dataclass

from tvbt.algorithms import definitions as algorithm_definitions
from tvbt.auxiliary.single_instrument_ma import (
    SingleMaConfig,
    classify_single_instrument_ma,
    definition,
)


@dataclass(frozen=True)
class Bar:
    bar_index: int
    timestamp_utc: int
    high_i64: int
    low_i64: int
    close_i64: int


def test_single_instrument_adapter_is_discoverable_without_fake_sector_input() -> None:
    value = definition()
    assert value["algorithm_id"] == "aux_single_instrument_ma_observation"
    assert any(item["algorithm_id"] == value["algorithm_id"] for item in algorithm_definitions())
    assert "minimum_sector_coverage_milli" not in value["parameter_schema"]["properties"]


def test_strict_conquest_and_pressure_consistency_are_separate_causal_observations() -> None:
    bars = [
        Bar(0, 0, 11, 9, 10),
        Bar(1, 60_000, 9, 8, 9),
        Bar(2, 120_000, 12, 10, 11),
        Bar(3, 180_000, 11, 10, 11),
        Bar(4, 240_000, 14, 12, 13),
    ]
    ladder = [[10.0] * 5, [12.0] * 5]
    config = SingleMaConfig(0, "up", 1, (5, 10))
    events = classify_single_instrument_ma(bars, ladder, config)
    pressures = [event for event in events if event.event_type == "aux_ma_pressure_consistency"]
    levels = [event for event in events if event.event_type == "aux_single_instrument_ma_level"]
    assert [(event.bar_index, event.known_at_bar_index) for event in pressures] == [(0, 1), (2, 3)]
    assert [event.details["ma_period"] for event in pressures] == [5, 10]
    assert [event.details["same_as_first_pressure"] for event in pressures] == [True, False]
    assert levels[-1].details["conquered_count"] == 2
    assert levels[-1].details["equality_is_conquered"] is False
    assert all(event.details["execution_allowed"] is False for event in events)
    for length in range(1, len(bars) + 1):
        prefix = classify_single_instrument_ma(
            bars[:length],
            [values[:length] for values in ladder],
            config,
        )
        assert prefix == [event for event in events if event.known_at_bar_index < length]


def test_observation_start_and_confirmation_horizon_are_explicit() -> None:
    config = SingleMaConfig.from_parameters(
        {
            "episode_start_bar_index": 20,
            "observation_direction": "down",
            "pressure_confirmation_bars": 3,
            **{
                f"ma_period_{index}": value
                for index, value in enumerate((5, 13, 21, 34, 55, 89, 144, 233), 1)
            },
        }
    )
    assert config.episode_start_bar_index == 20
    assert config.pressure_confirmation_bars == 3
    assert config.observation_direction == "down"
