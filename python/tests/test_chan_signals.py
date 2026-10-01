from __future__ import annotations

from dataclasses import dataclass, replace

import pytest

from tvbt.chan.engine import ChanEngine
from tvbt.chan.signals import (
    BiCenterEvidence,
    ChanSignal,
    StructuralCenter,
    _macd_extreme_relation,
    _trend_c_sublevel_proof,
    chan_divergences,
    chan_first_point_candidates,
    chan_forming_divergences,
    chan_trade_points,
)


def test_trend_c_sublevel_requires_two_closed_bi_centers_and_first_retest() -> None:
    bi = [
        Line("bi-departure", Endpoint(20, 20, 90), Endpoint(23, 23, 120), "up", 24),
        Line("bi-retest", Endpoint(23, 23, 120), Endpoint(26, 26, 110), "down", 27),
    ]
    centers = [
        BiCenterEvidence("bi-center-1", 20, 23, 28),
        BiCenterEvidence("bi-center-2", 23, 27, 30),
    ]

    def proof(
        supplied: list[BiCenterEvidence],
    ) -> tuple[tuple[str, ...], str | None, str | None, int | None]:
        return _trend_c_sublevel_proof(
            bi,
            supplied,
            start_bar_index=20,
            end_bar_index=30,
            known_at_bar_index=32,
            direction="up",
            boundary_i64=100,
        )

    assert proof(centers) == (
        ("bi-center-1", "bi-center-2"),
        "bi-departure",
        "bi-retest",
        30,
    )
    assert proof(centers[:1])[0] == ("bi-center-1",)
    assert proof([centers[0], replace(centers[1], known_at_bar_index=33)])[0] == ("bi-center-1",)
    assert proof([centers[0], replace(centers[1], end_bar_index=31)])[0] == ("bi-center-1",)
    failed_first_return = [
        bi[0],
        replace(bi[1], end=Endpoint(26, 26, 99)),
        Line("bi-later-departure", Endpoint(26, 26, 99), Endpoint(28, 28, 120), "up", 29),
        Line("bi-later-retest", Endpoint(28, 28, 120), Endpoint(30, 30, 110), "down", 31),
    ]
    assert (
        _trend_c_sublevel_proof(
            failed_first_return,
            centers,
            start_bar_index=20,
            end_bar_index=30,
            known_at_bar_index=32,
            direction="up",
            boundary_i64=100,
        )[2]
        is None
    )
    boundary_touch = [bi[0], replace(bi[1], end=Endpoint(26, 26, 100))]
    assert (
        _trend_c_sublevel_proof(
            boundary_touch,
            centers,
            start_bar_index=20,
            end_bar_index=30,
            known_at_bar_index=32,
            direction="up",
            boundary_i64=100,
        )[2]
        == "bi-retest"
    )


def test_trend_candidate_upgrades_only_with_two_bi_centers_and_type3() -> None:
    segments = [
        line(0, 0, 10),
        line(1, 10, 4),
        line(2, 4, 8),
        line(3, 8, 5),
        line(4, 5, 9),
        line(5, 9, 6),
        line(6, 6, 12),
        line(7, 12, 11),
        line(8, 11, 15),
        line(9, 15, 11),
        Line("segment-10", Endpoint(10, 10, 11), Endpoint(20, 20, 16), "up", 20),
        Line("segment-11", Endpoint(20, 20, 16), Endpoint(21, 21, 14), "down", 21),
    ]
    centers = [center(1, 5, 6, 5, 8), replace(center(7, 9, 10, 11, 12), relative_dir="UP")]
    histogram = {index: 0.0 for index in range(22)}
    histogram.update({6: 6.0, 7: 6.0, 15: 2.0, 16: 2.0})
    bi = [
        Line("bi-departure", Endpoint(10, 10, 11), Endpoint(13, 13, 14), "up", 14),
        Line("bi-retest", Endpoint(13, 13, 14), Endpoint(16, 16, 13), "down", 17),
    ]
    proof_centers = [
        BiCenterEvidence("bi-center-1", 10, 14, 18),
        BiCenterEvidence("bi-center-2", 14, 19, 20),
    ]

    def trend(evidence: list[BiCenterEvidence]) -> list[ChanSignal]:
        return [
            value
            for value in chan_divergences(
                segments,
                centers,
                ["center-1", "center-2"],
                histogram,
                bi_lines=bi,
                bi_centers=evidence,
            )
            if value.divergence_kind == "trend"
        ]

    candidate = trend(proof_centers[:1])[0]
    assert candidate.status == "candidate"
    assert candidate.c_contains_type3 is True
    assert candidate.c_meets_sublevel is False
    late_center = trend([proof_centers[0], replace(proof_centers[1], known_at_bar_index=22)])[0]
    assert late_center.status == "candidate"
    assert late_center.c_sublevel_center_ids == ("bi-center-1",)
    confirmed = trend(proof_centers)[0]
    assert confirmed.status == "confirmed"
    assert confirmed.divergence_profile == "standard_trend"
    assert confirmed.c_sublevel_center_ids == ("bi-center-1", "bi-center-2")
    assert confirmed.c_proof_known_at_bar_index == 20
    assert confirmed.known_at_bar_index == 21


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
) -> StructuralCenter:
    """构造一个已经离开的标准线段中枢测试桩。"""
    return StructuralCenter(
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


def active_center(base: int, end: int, zd: int, zg: int) -> StructuralCenter:
    """构造尚未离开的活动标准线段中枢测试桩。"""
    return StructuralCenter(
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


def test_trend_divergence_compares_b_and_c_but_does_not_invent_standard_proof() -> None:
    """a/A/b/B/c 同向且严格上移时，只能证成线段趋势候选。"""
    segments = [
        line(0, 0, 10),
        line(1, 10, 4),
        line(2, 4, 8),
        line(3, 8, 5),
        line(4, 5, 9),
        line(5, 9, 6),
        line(6, 6, 12),
        line(7, 12, 11),
        line(8, 11, 15),
        line(9, 15, 11),
        line(10, 11, 16),
        line(11, 16, 14),
    ]
    centers = [center(1, 5, 6, 5, 8), replace(center(7, 9, 10, 11, 12), relative_dir="UP")]
    histogram = {index: 0.0 for index in range(13)}
    histogram.update({6: 6.0, 7: 6.0, 10: 2.0, 11: 2.0})
    diff = {index: 0.0 for index in range(13)}
    diff.update({6: 4.0, 7: 5.0, 10: 1.0, 11: 2.0})
    dea = {index: 0.0 for index in range(13)}
    dea.update({6: 3.0, 7: 3.5, 10: 0.5, 11: 1.0})

    assert not [
        value
        for value in chan_divergences(segments[:11], centers, ["center-1", "center-2"], histogram)
        if value.divergence_kind == "trend"
    ]
    divergences = chan_divergences(
        segments, centers, ["center-1", "center-2"], histogram, diff=diff, dea=dea
    )
    trend = [value for value in divergences if value.divergence_kind == "trend"]
    assert len(trend) == 1
    for unresolved in ("UNKNOWN", "OVERLAP", "DOWN"):
        assert not [
            value
            for value in chan_divergences(
                segments,
                [centers[0], replace(centers[1], relative_dir=unresolved)],
                ["center-1", "center-2"],
                histogram,
            )
            if value.divergence_kind == "trend"
        ]
    assert trend[0].signal_type == "top_divergence"
    assert trend[0].segment_index == 10
    assert trend[0].macd_area_current < trend[0].macd_area_reference
    assert trend[0].comparison_reference_object_id == "segment-6"
    assert trend[0].comparison_current_object_id == "segment-10"
    assert trend[0].comparison_rule == "macd_same_direction_area_contraction_with_trend_new_extreme"
    assert trend[0].new_extreme_satisfied is True
    assert trend[0].follow_through_object_id == "segment-11"
    assert trend[0].follow_through_status == "observed"
    assert trend[0].known_at_bar_index == segments[11].known_at_bar_index
    assert trend[0].divergence_profile == "segment_trend_candidate"
    assert trend[0].status == "candidate"
    assert trend[0].relative_dir == "UP"
    assert (trend[0].a_object_id, trend[0].b_object_id) == ("segment-0", "segment-6")
    assert (trend[0].a_center_id, trend[0].b_center_id) == ("center-1", "center-2")
    assert trend[0].c_contains_type3 is None
    assert trend[0].c_meets_sublevel is None
    assert trend[0].macd_diff_reference_extreme == 5.0
    assert trend[0].macd_diff_current_extreme == 2.0
    assert trend[0].macd_dea_reference_extreme == 3.5
    assert trend[0].macd_dea_current_extreme == 1.0
    assert trend[0].macd_parameter_profile == "macd_12_26_9_histogram_x2"
    assert trend[0].macd_extreme_relation == "both_weaker"
    assert trend[0].reference_center_ordinal == 2
    assert trend[0].older_center_count == 1
    assert trend[0].center_chain_profile == "confirmed_same_level_centers_known_at_signal_v1"

    points = chan_trade_points(
        segments, centers, ["center-1", "center-2"], [("trend-div", trend[0])]
    )
    assert not [value for value in points if value.signal_type in {"sell_1", "sell_2"}]


@pytest.mark.parametrize(
    ("direction", "diff_reference", "diff_current", "dea_reference", "dea_current", "expected"),
    [
        ("up", 5.0, 2.0, 3.5, 1.0, "both_weaker"),
        ("down", -5.0, -2.0, -3.5, -1.0, "both_weaker"),
        ("up", 5.0, 2.0, 3.5, 4.0, "diff_only"),
        ("down", -5.0, -6.0, -3.5, -1.0, "dea_only"),
        ("up", 5.0, 5.0, 3.5, 4.0, "neither_weaker"),
        ("down", -5.0, -5.0, -3.5, -3.5, "neither_weaker"),
        ("up", None, 2.0, 3.5, 1.0, "unavailable"),
    ],
)
def test_macd_directional_extreme_relation_is_auditable_not_a_signal_gate(
    direction: str,
    diff_reference: float | None,
    diff_current: float | None,
    dea_reference: float | None,
    dea_current: float | None,
    expected: str,
) -> None:
    assert (
        _macd_extreme_relation(direction, diff_reference, diff_current, dea_reference, dea_current)
        == expected
    )


def test_forming_external_range_divergence_is_provisional_and_can_disappear() -> None:
    confirmed = [line(0, 0, 10), line(1, 10, 4), line(2, 4, 8), line(3, 8, 5)]
    forming = line(4, 5, 9)
    centers = [active_center(1, 3, 4, 8)]
    histogram = {index: 0.0 for index in range(6)}
    histogram.update({0: 10.0, 1: 10.0, 4: 2.0, 5: 2.0})
    result = chan_forming_divergences(confirmed, centers, ["B"], forming, histogram)
    assert len(result) == 1
    assert result[0].status == "forming"
    assert result[0].divergence_kind == "consolidation"
    assert result[0].divergence_profile == "external_range"
    assert result[0].relative_dir == "UNKNOWN"
    assert result[0].follow_through_status == "pending"
    assert result[0].known_at_bar_index == forming.known_at_bar_index
    histogram.update({4: 12.0, 5: 12.0})
    assert chan_forming_divergences(confirmed, centers, ["B"], forming, histogram) == []


def test_forming_divergence_revision_is_emitted_when_strength_recovers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = ChanEngine()
    runtime._segment_lines = [line(0, 0, 10), line(1, 10, 4), line(2, 4, 8), line(3, 8, 5)]
    forming = line(4, 5, 9)
    monkeypatch.setattr(runtime, "_forming_segment_observation", lambda: forming)
    monkeypatch.setattr(
        runtime, "_segment_structural_centers", lambda: ([active_center(1, 3, 4, 8)], ["B"])
    )
    runtime._macd_histogram = {index: 0.0 for index in range(6)}
    runtime._macd_histogram.update({0: 10.0, 1: 10.0, 4: 2.0, 5: 2.0})
    runtime._refresh_forming_divergences(5)
    current = runtime.emitter.current("divergence")
    assert len(current) == 1
    assert current[0]["status"] == "forming"
    assert current[0]["known_at_bar_index"] == 5
    runtime._macd_histogram.update({4: 12.0, 5: 12.0})
    runtime._refresh_forming_divergences(6)
    revised = runtime.emitter.current("divergence")
    assert len(revised) == 1
    assert revised[0]["status"] == "invalidated"
    assert revised[0]["invalidation_reason"] == "forming_leg_revised_or_strength_recovered"
    assert revised[0]["known_at_bar_index"] == 6
    prefix = ChanEngine()
    prefix._segment_lines = list(runtime._segment_lines)
    monkeypatch.setattr(prefix, "_forming_segment_observation", lambda: forming)
    monkeypatch.setattr(
        prefix, "_segment_structural_centers", lambda: ([active_center(1, 3, 4, 8)], ["B"])
    )
    prefix._macd_histogram = {index: 0.0 for index in range(6)}
    prefix._macd_histogram.update({0: 10.0, 1: 10.0, 4: 2.0, 5: 2.0})
    prefix._refresh_forming_divergences(5)
    assert [event.row() for event in prefix.emitter.events] == [
        event.row() for event in runtime.emitter.events if event.known_at_bar_index <= 5
    ]


def test_forming_trend_candidate_requires_strict_migration_and_new_extreme() -> None:
    segments = [
        line(0, 0, 10),
        line(1, 10, 4),
        line(2, 4, 8),
        line(3, 8, 5),
        line(4, 5, 9),
        line(5, 9, 6),
        line(6, 6, 12),
        line(7, 12, 11),
        line(8, 11, 15),
        line(9, 15, 11),
    ]
    centers = [center(1, 5, 6, 5, 8), replace(active_center(7, 9, 11, 12), relative_dir="UP")]
    forming = line(10, 11, 16)
    histogram = {index: 0.0 for index in range(12)}
    histogram.update({6: 6.0, 7: 6.0, 10: 2.0, 11: 2.0})
    result = chan_forming_divergences(segments, centers, ["A", "B"], forming, histogram)
    assert len(result) == 1
    assert result[0].status == "forming"
    assert result[0].divergence_kind == "trend"
    assert result[0].divergence_profile == "segment_trend_candidate"
    assert result[0].a_object_id == "segment-0"
    assert result[0].b_object_id == "segment-6"
    assert result[0].comparison_current_object_id == "segment-10"
    assert result[0].new_extreme_satisfied is True
    assert result[0].c_contains_type3 is None
    assert result[0].c_meets_sublevel is None
    assert not [
        signal
        for signal in chan_forming_divergences(
            segments,
            [centers[0], replace(centers[1], relative_dir="UNKNOWN")],
            ["A", "B"],
            forming,
            histogram,
        )
        if signal.divergence_kind == "trend"
    ]
    overlap = replace(centers[1], comparison_dd_i64=8, comparison_gg_i64=15)
    assert not [
        signal
        for signal in chan_forming_divergences(
            segments, [centers[0], overlap], ["A", "B"], forming, histogram
        )
        if signal.divergence_kind == "trend"
    ]


def test_third_point_boundary_uses_complete_body_not_migration_envelope() -> None:
    segments = [
        line(0, 0, 15),
        line(1, 15, 11),
        line(2, 11, 12),
        line(3, 12, 16),
        line(4, 16, 13),
    ]
    shared = replace(
        center(0, 2, 3, 10, 11),
        comparison_dd_i64=11,
        comparison_gg_i64=12,
        comparison_excluded_entry_id="segment-0",
    )
    points = chan_trade_points(segments, [shared], ["B"], [])
    third = next(value for value in points if value.signal_type == "buy_3")
    assert third.boundary_relation == "outside_core"
    assert third.return_depth_to_outer_i64 == -2


def test_trend_candidate_requires_full_envelope_new_extreme_and_complete_macd() -> None:
    segments = [
        line(0, 0, 10),
        line(1, 10, 4),
        line(2, 4, 8),
        line(3, 8, 5),
        line(4, 5, 9),
        line(5, 9, 6),
        line(6, 6, 12),
        line(7, 12, 11),
        line(8, 11, 15),
        line(9, 15, 11),
        line(10, 11, 16),
        line(11, 16, 14),
    ]
    centers = [center(1, 5, 6, 5, 8), replace(center(7, 9, 10, 11, 12), relative_dir="UP")]
    histogram = {index: 0.0 for index in range(13)}
    histogram.update({6: 6.0, 7: 6.0, 10: 2.0, 11: 2.0})

    def trend(values: list[Line], macd: dict[int, float]) -> list[object]:
        return [
            value
            for value in chan_divergences(values, centers, ["A", "B"], macd)
            if value.divergence_kind == "trend"
        ]

    assert len(trend(segments, histogram)) == 1
    overlap = list(segments)
    overlap[7] = line(7, 12, 9)  # B core is separate, but DD touches A's GG.
    assert trend(overlap, histogram) == []
    no_extreme = list(segments)
    no_extreme[10] = line(10, 11, 14)  # Below B's earlier high of 15.
    assert trend(no_extreme, histogram) == []
    wrong_a = list(segments)
    wrong_a[0] = line(0, 10, 0)
    assert trend(wrong_a, histogram) == []
    incomplete_macd = dict(histogram)
    incomplete_macd.pop(10)
    assert trend(segments, incomplete_macd) == []


def test_shared_b_segment_can_seed_b_center_without_blocking_comparison() -> None:
    """The full shared b/B-first-seed range is audited but not used as B DD/GG."""
    segments = [
        line(0, 0, 10),
        line(1, 10, 4),
        line(2, 4, 8),
        line(3, 8, 5),
        line(4, 5, 9),
        line(5, 9, 6),
        line(6, 6, 12),
        line(7, 12, 11),
        line(8, 11, 15),
        line(9, 15, 11),
        line(10, 11, 16),
        line(11, 16, 14),
    ]
    first = center(1, 5, 6, 5, 8)
    second = center(6, 9, 10, 11, 12)
    histogram = {index: 0.0 for index in range(13)}
    histogram.update({6: 6.0, 7: 6.0, 10: 2.0, 11: 2.0})
    assert not [
        signal
        for signal in chan_divergences(segments, [first, second], ["A", "B"], histogram)
        if signal.divergence_kind == "trend"
    ]
    separated = replace(
        second,
        relative_dir="UP",
        comparison_dd_i64=11,
        comparison_gg_i64=15,
        comparison_excluded_entry_id="segment-6",
    )
    candidates = [
        signal
        for signal in chan_divergences(segments, [first, separated], ["A", "B"], histogram)
        if signal.divergence_kind == "trend"
    ]
    assert len(candidates) == 1
    assert candidates[0].status == "candidate"
    assert candidates[0].b_object_id == separated.comparison_excluded_entry_id


def test_segment_only_trend_cannot_publish_standard_first_point() -> None:
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

    assert candidates == []

    confirmed = chan_first_point_candidates(
        [*segments, line(12, 16, 14)],
        centers,
        ["center-1", "center-2"],
        {**histogram, 12: 2.0},
        level_id="L1",
    )
    assert confirmed == []


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
    assert consolidation[0].divergence_profile == "external_range"
    assert consolidation[0].relative_dir == "UNKNOWN"
    assert consolidation[0].macd_area_ratio is not None
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
    ] == [("bottom_divergence", "center_oscillation", 4, "active-center", 6)]
    assert values[0].divergence_profile == "center_oscillation"
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
    assert exit_values[0].new_extreme_satisfied is False


def test_center_third_seed_departure_is_not_external_range_c() -> None:
    """A seed/exit role overlap must not fabricate an external a+B+c leg."""
    segments = [
        line(0, 10, 0),
        line(1, 0, 8),
        line(2, 8, 4),
        line(3, 4, 1),
        line(4, 1, 6),
    ]
    values = chan_divergences(
        segments,
        [center(1, 3, 3, 1, 8, "down")],
        ["center-1"],
        {0: -8.0, 1: -8.0, 3: -2.0, 4: -2.0},
    )
    assert not [value for value in values if value.divergence_profile == "external_range"]
    assert not chan_trade_points(
        segments,
        [center(1, 3, 3, 1, 8, "down")],
        ["center-1"],
        [(f"divergence-{index}", value) for index, value in enumerate(values)],
    )


def test_external_range_rejects_noncontiguous_a_leg() -> None:
    """a 与 B 之间隔着另一段时，不能跳过它拼接 a+B+c。"""
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
    assert not [value for value in values if value.divergence_kind == "consolidation"]


def test_forming_external_range_does_not_skip_an_opposite_leg_to_find_a() -> None:
    """The provisional path must keep the same a+B+c ownership rule."""
    segments = [
        line(0, 10, 0),
        line(1, 0, 8),
        line(2, 8, 2),
        line(3, 2, 9),
        line(4, 9, 4),
    ]
    values = chan_forming_divergences(
        segments,
        [active_center(2, 4, 4, 8)],
        ["center-1"],
        line(5, 4, 1),
        {index: (-8.0 if index <= 1 else -2.0) for index in range(7)},
    )
    assert not [value for value in values if value.divergence_profile == "external_range"]


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
        divergence_profile="standard_trend",
        c_contains_type3=True,
        c_meets_sublevel=True,
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
        divergence_profile="standard_trend",
        c_contains_type3=True,
        c_meets_sublevel=True,
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
