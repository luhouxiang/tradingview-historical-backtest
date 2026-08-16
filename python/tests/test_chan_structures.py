from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise

import pytest

from tvbt.chan.engine import ChanEngine, RawBar
from tvbt.chan.reference import reference_centers, reference_segments


@dataclass(frozen=True)
class Endpoint:
    """结构测试端点桩,保存时间和定点价格锚点。"""

    bar_index: int
    time: int
    price_i64: int


@dataclass(frozen=True)
class ComponentLine:
    """结构测试线段桩,可同时模拟笔或段组件。"""

    object_id: str
    start: Endpoint
    end: Endpoint
    direction: str
    known_at_bar_index: int


@dataclass(frozen=True)
class ComponentBar:
    """段扫描测试使用的最小原始 K 线桩。"""

    bar_index: int
    time: int
    high_i64: int
    low_i64: int
    close_i64: int


def bar(index: int, level: int) -> RawBar:
    """按整数层级构造一根无包含关系的原始 K 线。"""
    high = level * 10 + 5
    low = level * 10
    middle = (high + low) // 2
    return RawBar(
        index,
        1_700_000_000_000 + index * 60_000,
        middle,
        high,
        low,
        middle,
    )


def run_levels(levels: list[int]) -> ChanEngine:
    """将层级序列逐根送入缠论引擎并返回运行状态。"""
    runtime = ChanEngine()
    for index, level in enumerate(levels):
        runtime.update(bar(index, level))
    return runtime


def component_line(index: int, start_price: int, end_price: int) -> ComponentLine:
    """构造首尾相接的组件线,并自动标记方向。"""
    return ComponentLine(
        f"component-{index}",
        Endpoint(index, index * 60_000, start_price),
        Endpoint(index + 1, (index + 1) * 60_000, end_price),
        "up" if end_price > start_price else "down",
        index + 1,
    )


def swing_components(pivots: list[int]) -> tuple[list[ComponentLine], list[ComponentBar]]:
    """把价格拐点序列转换为段扫描器所需的线和 K 线桩。"""
    lines = [
        component_line(index, start_price, end_price)
        for index, (start_price, end_price) in enumerate(pairwise(pivots))
    ]
    bars = [
        ComponentBar(index, index * 60_000, price, price, price)
        for index, price in enumerate(pivots[:-1])
    ]
    return lines, bars


def center_fixture(*, leave_direction: str = "up", extended: bool = True) -> list[ComponentLine]:
    """构造同奇偶组件,使基点 1 和 3 冻结出 `[2, 10]` 中枢。"""
    values = [
        (15, 20),
        (20, 0),
        (0, 10),
        (10, 2),
        (2, 8),
    ]
    if extended:
        values.extend([(8, 4), (4, 12)])
    values.extend(
        [
            (12, 9),
            (9, 11),
            (12, 20) if leave_direction == "up" else (1, 0),
        ]
    )
    return [component_line(index, start, end) for index, (start, end) in enumerate(values)]


def assert_connected_and_alternating(rows: list[dict[str, object]]) -> None:
    """断言线性对象首尾相接且方向严格交替。"""
    assert all(left["end_bar_index"] == right["start_bar_index"] for left, right in pairwise(rows))
    assert all(left["direction"] != right["direction"] for left, right in pairwise(rows))


def test_bi_cases_cover_confirmed_up_down_and_current_unconfirmed_bi() -> None:
    """测试笔的已确认状态和当前未确认状态。

    预期:已完成笔同时包含 `up` 和 `down`,相邻笔只共享一个端点且方向交替;
    等待后续分型时,只有最后一笔保持未确认。
    """
    runtime = run_levels(([0, 1, 2, 3, 4, 3, 2, 1] * 4) + [0])
    rows = runtime.result_rows()["bi"]

    assert [(row["direction"], row["confirmed"]) for row in rows[:5]] == [
        ("down", True),
        ("up", True),
        ("down", True),
        ("up", True),
        ("down", True),
    ]
    assert rows[-1]["confirmed"] is False
    assert rows[-1]["confirmed_at_bar_index"] is None
    assert {row["direction"] for row in rows if row["confirmed"]} == {"up", "down"}
    assert_connected_and_alternating(rows)


def test_bi_minimum_independent_bar_rule_blocks_too_short_confirmations() -> None:
    """测试五根独立 K 线成笔门槛和同类分型替换。

    预期:快速交替分型不会生成确认笔;引擎仅保留一条向下临时笔,并在满足
    可观察距离后使用更极端的底分型候选作为端点。
    """
    runtime = run_levels([0, 3, 1, 4, 2, 5, 1, 6, 0, 7, 1, 8, 0])
    rows = runtime.result_rows()["bi"]

    assert [row["confirmed"] for row in rows] == [False]
    assert rows[0]["direction"] == "down"
    assert rows[0]["start_bar_index"] == 1
    assert rows[0]["end_bar_index"] == 8


@pytest.mark.parametrize(
    ("pivots", "expected"),
    [
        pytest.param(
            [7, 34, 30, 46, 19, 22, 4, 11],
            [(3, 6, "down", False)],
            id="single-current-down-segment",
        ),
        pytest.param(
            [20, 10, 16, 8, 14, 9, 24, 2],
            [(3, 6, "up", False)],
            id="single-current-up-segment",
        ),
        pytest.param(
            [9, 22, 15, 31, 10, 12, 0, 12, 7, 14, 7],
            [(3, 6, "down", True), (6, 9, "up", False)],
            id="confirmed-down-then-current-up",
        ),
        pytest.param(
            [29, 8, 13, 3, 38, 30, 45, 9, 25, 4, 29],
            [(3, 6, "up", True), (6, 9, "down", False)],
            id="confirmed-up-then-current-down",
        ),
        pytest.param(
            [21, 10, 15, 2, 44, 37, 46, 11, 18, 9, 26, 20, 30, 3],
            [(3, 6, "up", True), (6, 9, "down", True), (9, 12, "up", False)],
            id="multi-segment-alternating-reversal",
        ),
    ],
)
def test_segment_cases_cover_direction_confirmation_and_reversal(
    pivots: list[int], expected: list[tuple[int, int, str, bool]]
) -> None:
    """测试段扫描器的主要状态类型。

    预期:参考段输出覆盖向上当前段、向下当前段、已确认段和多段交替反转,
    同时保持段端点连续。
    """
    lines, bars = swing_components(pivots)
    rows = reference_segments(lines, bars)

    assert [
        (
            row.start_index,
            row.end_index,
            "up" if row.up else "down",
            row.confirmed,
        )
        for row in rows
    ] == expected
    assert all(left.end_index == right.start_index for left, right in pairwise(rows))
    assert all(left.up != right.up for left, right in pairwise(rows))


@pytest.mark.parametrize(
    ("lines", "expected"),
    [
        pytest.param(
            center_fixture(extended=False)[:5],
            [("confirmed", None, 1, 3, 2, 10)],
            id="bi-center-confirmed",
        ),
        pytest.param(
            center_fixture(extended=True)[:7],
            [("extended", None, 1, 5, 2, 10)],
            id="bi-center-extended",
        ),
        pytest.param(
            center_fixture(leave_direction="up"),
            [("left", "up", 1, 7, 2, 10)],
            id="bi-center-left-up",
        ),
        pytest.param(
            center_fixture(leave_direction="down"),
            [("left", "down", 1, 7, 2, 10)],
            id="bi-center-left-down",
        ),
    ],
)
def test_bi_zhongshu_cases_cover_confirmed_extended_and_leave_directions(
    lines: list[ComponentLine], expected: list[tuple[str, str | None, int, int, int, int]]
) -> None:
    """测试笔中枢状态转换。

    预期:扫描器冻结初始 ZD/ZG 核心,可以在不改变核心的前提下延长时间范围;
    当首个不相交同奇偶笔出现时,记录向上和向下两类离开方向。
    """
    centers = reference_centers(lines)

    observed = [
        (
            center.status,
            center.leave_direction,
            center.base_index,
            center.end_index,
            center.zd_i64,
            center.zg_i64,
        )
        for center in centers
    ]
    assert observed[: len(expected)] == expected


def test_bi_zhongshu_allows_zero_width_reference_overlap() -> None:
    """测试笔中枢扫描器对点重叠的兼容性。

    预期:当 ZD 等于 ZG 时仍返回笔中枢,因为遗留笔中枢契约允许零宽交集。
    """
    centers = reference_centers(
        [
            component_line(0, 0, 10),
            component_line(1, 2, 0),
            component_line(2, 0, 8),
            component_line(3, 8, 2),
            component_line(4, 2, 8),
        ]
    )

    assert len(centers) == 1
    assert centers[0].zd_i64 == centers[0].zg_i64 == 2
    assert centers[0].status == "confirmed"


def test_segment_zhongshu_cases_use_positive_width_segment_core() -> None:
    """测试标准线段中枢的正宽核心和段级语义。

    预期:段中枢在调用方保持 `analysis_level=segment` 语义,暴露给图表和存储前
    必须满足正宽核心,并在后续段延伸或离开时保持冻结核心不变。
    """
    lines = center_fixture(leave_direction="up")
    centers = [center for center in reference_centers(lines) if center.zd_i64 < center.zg_i64]

    assert [
        (center.status, center.leave_direction, center.zd_i64, center.zg_i64)
        for center in centers
    ] == [("left", "up", 2, 10)]
    components = lines[centers[0].base_index : centers[0].end_index + 1]
    dd_i64 = min(min(line.start.price_i64, line.end.price_i64) for line in components)
    gg_i64 = max(max(line.start.price_i64, line.end.price_i64) for line in components)
    assert (dd_i64, centers[0].zd_i64, centers[0].zg_i64, gg_i64) == (0, 2, 10, 20)


def test_segment_zhongshu_rejects_touch_only_core_before_exposure() -> None:
    """测试标准线段中枢是否过滤仅点接触的核心。

    预期:原始中枢扫描器可以识别点接触交集,但标准线段中枢暴露规则要求
    `ZD < ZG`,因此会过滤该零宽中枢。
    """
    raw_centers = reference_centers(
        [
            component_line(0, 0, 10),
            component_line(1, 2, 0),
            component_line(2, 0, 8),
            component_line(3, 8, 2),
            component_line(4, 2, 8),
        ]
    )

    assert [(center.zd_i64, center.zg_i64) for center in raw_centers] == [(2, 2)]
    assert [center for center in raw_centers if center.zd_i64 < center.zg_i64] == []
