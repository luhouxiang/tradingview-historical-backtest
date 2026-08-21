from __future__ import annotations

from dataclasses import dataclass

import pytest

from tvbt.chan.zn import classify_zn_components


@dataclass(frozen=True)
class Anchor:
    bar_index: int
    time: int
    price_i64: int


@dataclass(frozen=True)
class Component:
    object_id: str
    direction: str
    start: Anchor
    end: Anchor
    known_at_bar_index: int


def components(ranges: list[tuple[int, int]], known_at: list[int] | None = None) -> list[Component]:
    known = known_at or [index + 1 for index in range(len(ranges))]
    return [
        Component(
            object_id=f"segment-{index}",
            direction="up" if index % 2 == 0 else "down",
            start=Anchor(index * 10, index * 60_000, low if index % 2 == 0 else high),
            end=Anchor(index * 10 + 5, index * 60_000 + 30_000, high if index % 2 == 0 else low),
            known_at_bar_index=known[index],
        )
        for index, (low, high) in enumerate(ranges)
    ]


def test_exact_half_tick_bias_does_not_depend_on_component_direction() -> None:
    values = classify_zn_components(
        core_low_i64=10,
        core_high_i64=15,
        components=components([(10, 17), (10, 13), (10, 15)], [4, 7, 9]),
    )

    assert [value.z_twice_i64 for value in values] == [25, 25, 25]
    assert [value.zn_twice_i64 for value in values] == [27, 23, 25]
    assert [value.z_i64 for value in values] == [12, 12, 12]
    assert [value.zn_i64 for value in values] == [13, 11, 12]
    assert [value.oscillation_bias for value in values] == ["strong", "weak", "neutral"]
    assert [value.relative_position for value in values] == ["above", "below", "equal"]
    assert [value.known_at_bar_index for value in values] == [9, 9, 9]


def test_strict_boundaries_wedges_and_crossing_priority() -> None:
    values = classify_zn_components(
        core_low_i64=0,
        core_high_i64=10,
        components=components([(0, 4), (0, 8), (0, 20), (0, 21), (-1, 0)]),
    )

    assert [value.breakout_warning for value in values] == [
        None,
        None,
        "rising_wedge_below_b",
        "cross_above_b",
        "cross_below_a",
    ]
    assert values[2].zn_twice_i64 == values[2].core_high_i64 * 2
    assert values[2].breakout_warning != "cross_above_b"
    assert [value.known_at_bar_index for value in values] == [3, 3, 3, 5, 5]

    falling = classify_zn_components(
        core_low_i64=0,
        core_high_i64=10,
        components=components([(0, 16), (0, 8), (0, 0)]),
    )
    assert falling[-1].breakout_warning == "falling_wedge_above_a"


def test_prefixes_are_stable_and_monitor_series_stops_at_nine_points() -> None:
    source = components([(0, 10)] * 11, list(range(10, 21)))
    full = classify_zn_components(core_low_i64=0, core_high_i64=10, components=source)

    assert len(full) == 9
    assert [value.component_ordinal for value in full] == list(range(1, 10))
    assert [value.known_at_bar_index for value in full] == [12, 12, 12, 14, 14, 16, 16, 18, 18]
    for length in (3, 5, 7, 9):
        prefix = classify_zn_components(
            core_low_i64=0,
            core_high_i64=10,
            components=source[:length],
        )
        assert prefix == full[:length]


def test_invalid_core_and_configuration_are_rejected() -> None:
    values = components([(0, 1)] * 3)
    with pytest.raises(ValueError, match="core_low_i64"):
        classify_zn_components(core_low_i64=2, core_high_i64=1, components=values)
    with pytest.raises(ValueError, match="maximum_points"):
        classify_zn_components(
            core_low_i64=0,
            core_high_i64=1,
            components=values,
            maximum_points=8,
        )
