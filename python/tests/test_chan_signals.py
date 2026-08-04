from __future__ import annotations

from dataclasses import dataclass

from tvbt.chan.reference import ReferenceCenter
from tvbt.chan.signals import chan_divergences, chan_trade_points


@dataclass(frozen=True)
class Endpoint:
    bar_index: int
    time: int
    price_i64: int


@dataclass(frozen=True)
class Line:
    object_id: str
    start: Endpoint
    end: Endpoint
    direction: str
    known_at_bar_index: int


def line(index: int, start: int, end: int) -> Line:
    return Line(
        f"segment-{index}",
        Endpoint(index, index * 60_000, start),
        Endpoint(index + 1, (index + 1) * 60_000, end),
        "up" if end > start else "down",
        index + 1,
    )


def center(
    base: int,
    end: int,
    exit_index: int,
    zd: int,
    zg: int,
    leave_direction: str = "up",
) -> ReferenceCenter:
    return ReferenceCenter(
        base_index=base,
        seed_end_index=base + 2,
        end_index=end,
        exit_index=exit_index,
        start_bar_index=base,
        end_bar_index=end + 1,
        start_time=base * 60_000,
        end_time=(end + 1) * 60_000,
        zd_i64=zd,
        zg_i64=zg,
        known_at_bar_index=exit_index + 1,
        status="left",
        leave_direction=leave_direction,
    )


def test_trend_divergence_compares_b_and_c_and_creates_standard_points() -> None:
    segments = [
        line(0, 10, 4),
        line(1, 4, 8),
        line(2, 8, 5),
        line(3, 5, 9),
        line(4, 9, 6),
        line(5, 6, 12),
        line(6, 12, 10),
        line(7, 10, 14),
        line(8, 14, 12),
        line(9, 12, 15),
        line(10, 15, 13),
        line(11, 13, 16),
        line(12, 16, 14),
        line(13, 14, 15),
    ]
    centers = [center(1, 3, 5, 5, 8), center(7, 9, 11, 12, 14)]
    histogram = {index: 0.0 for index in range(16)}
    histogram.update({5: 6.0, 6: 6.0, 11: 2.0, 12: 2.0})

    divergences = chan_divergences(segments, centers, ["center-1", "center-2"], histogram)
    trend = [value for value in divergences if value.divergence_kind == "trend"]
    assert len(trend) == 1
    assert trend[0].signal_type == "top_divergence"
    assert trend[0].segment_index == 11
    assert trend[0].macd_area_current < trend[0].macd_area_reference

    points = chan_trade_points(
        segments, centers, ["center-1", "center-2"], [("trend-div", trend[0])]
    )
    sell_points = [
        (value.signal_type, value.segment_index, value.strength)
        for value in points
        if value.signal_type.startswith("sell_")
    ]
    assert sell_points == [
        ("sell_1", 11, None),
        ("sell_2", 13, "normal"),
    ]


def test_touching_outer_ranges_are_not_a_strict_trend() -> None:
    segments = [
        line(0, 10, 4),
        line(1, 4, 8),
        line(2, 8, 5),
        line(3, 5, 9),
        line(4, 9, 6),
        line(5, 6, 12),
        line(6, 12, 9),
        line(7, 9, 14),
        line(8, 14, 11),
        line(9, 11, 15),
        line(10, 15, 13),
        line(11, 13, 16),
        line(12, 16, 14),
    ]
    centers = [center(1, 3, 5, 5, 8), center(7, 9, 11, 11, 14)]
    histogram = {5: 6.0, 6: 6.0, 11: 2.0, 12: 2.0}
    assert not [
        value
        for value in chan_divergences(segments, centers, ["c1", "c2"], histogram)
        if value.divergence_kind == "trend"
    ]


def test_consolidation_divergence_creates_class_one_and_normal_class_two() -> None:
    segments = [
        line(0, 10, 0),
        line(1, 10, 4),
        line(2, 4, 9),
        line(3, 9, 5),
        line(4, 5, 8),
        line(5, 8, -2),
        line(6, -2, 6),
        line(7, 6, 0),
    ]
    centers = [center(1, 3, 5, 4, 9, "down")]
    histogram = {0: -8.0, 1: -8.0, 5: -2.0, 6: -2.0}
    divergences = chan_divergences(segments, centers, ["center-1"], histogram)
    consolidation = [v for v in divergences if v.divergence_kind == "consolidation"]
    assert [(v.signal_type, v.segment_index) for v in consolidation] == [("bottom_divergence", 5)]
    points = chan_trade_points(
        segments, centers, ["center-1"], [("consolidation-div", consolidation[0])]
    )
    assert [(v.signal_type, v.signal_class, v.strength) for v in points] == [
        ("class_buy_1", "class_like", None),
        ("class_buy_2", "class_like", "normal"),
    ]


def test_consolidation_divergence_does_not_require_a_new_extreme() -> None:
    segments = [
        line(0, 10, 0),
        line(1, 10, 4),
        line(2, 4, 9),
        line(3, 9, 5),
        line(4, 5, 8),
        line(5, 8, 2),
        line(6, 2, 6),
    ]
    values = chan_divergences(
        segments,
        [center(1, 3, 5, 4, 9, "down")],
        ["center-1"],
        {0: -8.0, 1: -8.0, 5: -2.0, 6: -2.0},
    )
    assert [(value.signal_type, value.divergence_kind) for value in values] == [
        ("bottom_divergence", "consolidation")
    ]


def test_consolidation_uses_previous_same_direction_not_adjacent_opposite_leg() -> None:
    segments = [
        line(0, 10, 0),
        line(1, 0, 8),
        line(2, 8, 2),
        line(3, 2, 9),
        line(4, 9, 4),
        line(5, 4, 7),
        line(6, 7, 3),
        line(7, 3, 6),
    ]
    values = chan_divergences(
        segments,
        [center(2, 4, 6, 4, 8, "down")],
        ["center-1"],
        {0: -8.0, 1: -8.0, 6: -2.0, 7: -2.0},
    )
    assert [(value.signal_type, value.segment_index) for value in values] == [
        ("bottom_divergence", 6)
    ]


def test_third_buy_uses_first_return_after_leaving_segment() -> None:
    segments = [
        line(0, 10, 0),
        line(1, 0, 6),
        line(2, 6, 2),
        line(3, 2, 8),
        line(4, 8, 7),
        line(5, 7, 10),
        line(6, 10, 7),
    ]
    points = chan_trade_points(segments, [center(1, 3, 5, 2, 6)], ["center-up"], [])
    assert [(value.signal_type, value.segment_index) for value in points] == [("buy_3", 6)]


def test_second_point_new_extreme_requires_consolidation_divergence_confirmation() -> None:
    segments = [line(0, 10, 0), line(1, 0, 8), line(2, 8, -1)]
    from tvbt.chan.signals import ChanSignal

    first = ChanSignal(
        "bottom_divergence",
        "trend",
        None,
        None,
        0,
        1,
        60_000,
        0,
        "center",
        9.0,
        3.0,
        1,
    )
    points = chan_trade_points(segments, [], [], [("trend-div", first)])
    assert [point.signal_type for point in points] == ["buy_1"]


def test_second_point_strength_is_strongest_when_it_overlaps_a_third_point() -> None:
    from tvbt.chan.signals import ChanSignal

    segments = [
        line(0, 10, 0),
        line(1, 0, 6),
        line(2, 6, 2),
        line(3, 2, 8),
        line(4, 8, 0),
        line(5, 0, 10),
        line(6, 10, 7),
    ]
    first = ChanSignal(
        "bottom_divergence",
        "consolidation",
        None,
        None,
        4,
        5,
        300_000,
        0,
        "center",
        9.0,
        3.0,
        5,
    )
    points = chan_trade_points(segments, [center(1, 3, 5, 2, 6)], ["center-up"], [("div", first)])
    assert ("class_buy_2", "strongest") in [(point.signal_type, point.strength) for point in points]
    assert {point.signal_type for point in points} >= {"buy_3", "class_buy_3"}


def test_weakest_second_point_requires_its_own_consolidation_divergence() -> None:
    from tvbt.chan.signals import ChanSignal

    segments = [line(0, 10, 0), line(1, 0, 8), line(2, 8, -1)]
    trend = ChanSignal(
        "bottom_divergence",
        "trend",
        None,
        None,
        0,
        1,
        60_000,
        0,
        "trend-center",
        9.0,
        3.0,
        1,
    )
    retrace = ChanSignal(
        "bottom_divergence",
        "consolidation",
        None,
        None,
        2,
        3,
        180_000,
        -1,
        "retrace-center",
        5.0,
        2.0,
        3,
    )
    points = chan_trade_points(segments, [], [], [("trend-div", trend), ("retrace-div", retrace)])
    assert ("buy_2", "weakest") in [(point.signal_type, point.strength) for point in points]
