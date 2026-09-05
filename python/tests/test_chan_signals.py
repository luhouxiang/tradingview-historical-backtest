from __future__ import annotations

from dataclasses import dataclass

from tvbt.chan.reference import ReferenceCenter
from tvbt.chan.signals import (
    chan_divergences,
    chan_first_point_candidates,
    chan_trade_points,
)


@dataclass(frozen=True)
class Endpoint:
    """线段端点测试桩,只保留信号算法读取的锚点字段。"""

    bar_index: int
    time: int
    price_i64: int


@dataclass(frozen=True)
class Line:
    """线段测试桩,模拟已确认线段的最小字段集合。"""

    object_id: str
    start: Endpoint
    end: Endpoint
    direction: str
    known_at_bar_index: int


def line(index: int, start: int, end: int) -> Line:
    """构造一条测试线段,并根据起止价格自动确定方向。"""
    return Line(
        f"segment-{index}",
        Endpoint(index, index * 60_000, start),
        Endpoint(index + 1, (index + 1) * 60_000, end),
        "up" if end > start else "down",
        index + 1,
    )


def line_known_at(index: int, start: int, end: int, known_at_bar_index: int) -> Line:
    """构造确认时点晚于理论端点的线段测试桩。"""
    value = line(index, start, end)
    return Line(value.object_id, value.start, value.end, value.direction, known_at_bar_index)


def center(
    base: int,
    end: int,
    exit_index: int,
    zd: int,
    zg: int,
    leave_direction: str = "up",
) -> ReferenceCenter:
    """构造一个已经离开的标准线段中枢测试桩。"""
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


def active_center(base: int, end: int, zd: int, zg: int) -> ReferenceCenter:
    """构造尚未离开的活动标准线段中枢测试桩。"""
    return ReferenceCenter(
        base_index=base,
        seed_end_index=base + 2,
        end_index=end,
        exit_index=None,
        start_bar_index=base,
        end_bar_index=end + 1,
        start_time=base * 60_000,
        end_time=(end + 1) * 60_000,
        zd_i64=zd,
        zg_i64=zg,
        known_at_bar_index=end + 1,
        status="extended" if end > base + 2 else "confirmed",
        leave_direction=None,
    )


def test_trend_divergence_compares_b_and_c_and_creates_standard_points() -> None:
    """测试趋势背驰是否比较 b 段和 c 段并生成标准买卖点。

    预期:两个同向且外包络严格分离的中枢构成趋势,MACD 面积收缩的 c 段
    生成顶背驰,并进一步生成标准一卖和普通强度二卖。
    """
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
    assert trend[0].comparison_reference_object_id == "segment-5"
    assert trend[0].comparison_current_object_id == "segment-11"
    assert trend[0].comparison_rule == "macd_same_direction_area_contraction_with_new_extreme"
    assert trend[0].new_extreme_satisfied is True
    assert trend[0].follow_through_object_id == "segment-12"
    assert trend[0].follow_through_status == "observed"
    assert trend[0].reference_center_ordinal == 2
    assert trend[0].older_center_count == 1
    assert trend[0].center_chain_profile == "confirmed_same_level_centers_known_at_signal_v1"

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
    first = next(value for value in points if value.signal_type == "sell_1")
    assert first.status == "confirmed"
    assert first.catalog_event == "S1_confirmed"
    assert first.catalog_algorithm_id == "ALG-SIG-001"
    assert first.lower_level_turn_object_id == "segment-12"


def test_first_point_candidate_precedes_lower_level_turn_confirmation() -> None:
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
    ]
    centers = [center(1, 3, 5, 5, 8), center(7, 9, 11, 12, 14)]
    histogram = {5: 6.0, 6: 6.0, 11: 2.0}

    candidates = chan_first_point_candidates(segments, centers, ["center-1", "center-2"], histogram)

    assert len(candidates) == 1
    assert candidates[0].signal_type == "sell_1"
    assert candidates[0].status == "candidate"
    assert candidates[0].known_at_bar_index == segments[11].known_at_bar_index
    assert candidates[0].catalog_event == "S1_candidate"
    assert candidates[0].lower_level_turn_object_id is None

    confirmed = chan_first_point_candidates(
        [*segments, line(12, 16, 14)],
        centers,
        ["center-1", "center-2"],
        {**histogram, 12: 2.0},
        level_id="L1",
    )
    assert len(confirmed) == 1
    assert confirmed[0].status == "confirmed"
    assert confirmed[0].level_id == "L1"
    assert confirmed[0].known_at_bar_index == 13
    assert confirmed[0].lower_level_turn_object_id == "segment-12"
    assert confirmed[0].catalog_event == "S1_confirmed"


def test_first_point_candidate_rejects_one_center_overlap_and_nonweaker_force() -> None:
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
    ]
    first = center(1, 3, 5, 5, 8)
    touching = center(7, 9, 11, 11, 14)
    weak_histogram = {5: 6.0, 6: 6.0, 11: 2.0}
    strong_histogram = {5: 2.0, 6: 2.0, 11: 6.0}

    assert not chan_first_point_candidates(segments, [first], ["c1"], weak_histogram)
    assert not chan_first_point_candidates(
        segments, [first, touching], ["c1", "c2"], weak_histogram
    )
    separated = center(7, 9, 11, 12, 14)
    assert not chan_first_point_candidates(
        segments, [first, separated], ["c1", "c2"], strong_histogram
    )
    no_new_extreme = [*segments]
    no_new_extreme[5] = line(5, 6, 20)
    assert not chan_first_point_candidates(
        no_new_extreme, [first, separated], ["c1", "c2"], weak_histogram
    )


def test_touching_outer_ranges_are_not_a_strict_trend() -> None:
    """测试外包络仅接触时不会被判定为严格趋势。

    预期:两个中枢的 DD/GG 外包络没有严格脱离时,即使 MACD 面积收缩,
    也不生成趋势背驰。
    """
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
    """测试盘整背驰是否生成类一和普通类二买点。

    预期:单中枢 `a+Z+c` 中,离开段 c 相对前一同向段力度减弱时生成
    盘整底背驰,并派生类一买与普通强度类二买。
    """
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
    consolidation = [
        value
        for value in divergences
        if value.divergence_kind == "consolidation" and value.segment_index == 5
    ]
    assert [(v.signal_type, v.segment_index) for v in consolidation] == [("bottom_divergence", 5)]
    points = chan_trade_points(
        segments, centers, ["center-1"], [("consolidation-div", consolidation[0])]
    )
    assert [(v.signal_type, v.signal_class, v.strength) for v in points] == [
        ("class_buy_1", "class_like", None),
        ("class_buy_2", "class_like", "normal"),
    ]


def test_active_center_oscillation_emits_confirmed_lower_level_divergence() -> None:
    """活动中枢内相邻同向震荡段力度收缩时发布盘整背驰。

    当前下降段属于中枢延伸的事实要等下一条同奇偶线段确认，因此信号确认
    时点必须晚于其理论端点；Zn 不参与这个结构判定。
    """
    segments = [
        line(0, 12, 8),
        line(1, 8, 14),
        line(2, 14, 7),
        line(3, 7, 13),
        line(4, 13, 9),
        line(5, 9, 12),
        line(6, 12, 10),
    ]
    values = chan_divergences(
        segments,
        [active_center(1, 5, 8, 12)],
        ["active-center"],
        {2: -5.0, 3: -5.0, 4: -1.0, 5: -1.0},
    )
    assert [
        (
            value.signal_type,
            value.divergence_kind,
            value.segment_index,
            value.reference_object_id,
            value.known_at_bar_index,
        )
        for value in values
    ] == [("bottom_divergence", "consolidation", 4, "active-center", 6)]
    assert values[0].known_at_bar_index > values[0].bar_index


def test_consolidation_divergence_does_not_require_a_new_extreme() -> None:
    """测试盘整背驰是否不要求离开段创出新极值。

    预期:只要结构关系成立且 MACD 面积减弱,即使 c 段没有跌破 a 段低点,
    仍然可以生成盘整底背驰。
    """
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
    exit_values = [value for value in values if value.segment_index == 5]
    assert [(value.signal_type, value.divergence_kind) for value in exit_values] == [
        ("bottom_divergence", "consolidation")
    ]


def test_consolidation_uses_previous_same_direction_not_adjacent_opposite_leg() -> None:
    """测试盘整背驰参考段是否取前一同向段而非相邻反向段。

    预期:单中枢离开段 c 的力度比较对象是中枢前最近同向段 a,
    不能误用相邻反向段。
    """
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
    """测试三买是否使用中枢离开后的第一次回试段。

    预期:向上离开中枢后,第一条已完成向下回试段低点不低于 ZG 时,
    生成标准三买,信号位置锚定在该回试段。
    """
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


def test_third_buy_accepts_equal_zg_and_preserves_confirmation_time() -> None:
    """测试三买等于 ZG 的闭区间边界与事件可见时点。"""
    segments = [
        line(0, 10, 0),
        line(1, 0, 6),
        line(2, 6, 2),
        line(3, 2, 8),
        line(4, 8, 7),
        line(5, 7, 10),
        line_known_at(6, 10, 6, 11),
    ]
    points = chan_trade_points(segments, [center(1, 3, 5, 2, 6)], ["center-up"], [])
    assert len(points) == 1
    assert points[0].signal_type == "buy_3"
    assert points[0].price_i64 == 6
    assert points[0].bar_index == 7
    assert points[0].known_at_bar_index == 11
    assert points[0].known_at_bar_index >= points[0].bar_index
    assert points[0].confirmation_latency_bars == 4
    assert points[0].departure_object_id == "segment-5"
    assert points[0].return_object_id == "segment-6"
    assert points[0].return_ordinal == 1
    assert points[0].boundary_profile == "lesson20_inclusive_v1"
    assert points[0].boundary_relation == "touch_core"
    assert points[0].return_depth_to_core_i64 == 0
    assert points[0].follow_through_status == "pending"


def test_third_sell_accepts_equal_zd() -> None:
    """测试三卖第一次回抽高点等于 ZD 时仍按闭区间确认。"""
    segments = [
        line(0, 0, 10),
        line(1, 10, 4),
        line(2, 4, 8),
        line(3, 8, 2),
        line(4, 2, 3),
        line(5, 3, 0),
        line(6, 0, 4),
    ]
    points = chan_trade_points(
        segments,
        [center(1, 3, 5, 4, 8, "down")],
        ["center-down"],
        [],
    )
    assert [(value.signal_type, value.price_i64) for value in points] == [("sell_3", 4)]


def test_third_points_reject_crossed_boundaries_and_unfinished_return() -> None:
    """测试越过中枢边界与尚无已完成回试时都不发布三类点。"""
    crossed_buy = [
        line(0, 10, 0),
        line(1, 0, 6),
        line(2, 6, 2),
        line(3, 2, 8),
        line(4, 8, 7),
        line(5, 7, 10),
        line(6, 10, 5),
    ]
    crossed_sell = [
        line(0, 0, 10),
        line(1, 10, 4),
        line(2, 4, 8),
        line(3, 8, 2),
        line(4, 2, 3),
        line(5, 3, 0),
        line(6, 0, 5),
    ]
    unfinished = crossed_buy[:-1]
    assert chan_trade_points(crossed_buy, [center(1, 3, 5, 2, 6)], ["center-up"], []) == []
    assert (
        chan_trade_points(
            crossed_sell,
            [center(1, 3, 5, 4, 8, "down")],
            ["center-down"],
            [],
        )
        == []
    )
    assert chan_trade_points(unfinished, [center(1, 3, 5, 2, 6)], ["center-up"], []) == []


def test_second_point_new_extreme_requires_consolidation_divergence_confirmation() -> None:
    """测试二类点创新极值时必须有自身盘整背驰确认。

    预期:一买后回试段若继续创新低,且该回试段没有自己的盘整背驰确认,
    只保留一买,不生成二买。
    """
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
    """测试二类点与三类点同点时是否标记为最强。

    预期:类一买后的二买若与严格三买同一端点重合,则二类点强度为
    `strongest`,同时保留标准三买和类三买生命周期标记。
    """
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
    """测试最弱二买是否必须由回试段自身盘整背驰支持。

    预期:标准一买后回试段创新低,但该回试段也产生盘整底背驰时,
    允许生成 `weakest` 强度的二买。
    """
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
