from __future__ import annotations

from dataclasses import dataclass, replace
from itertools import pairwise

import pytest

from tvbt.chan.engine import ChanEngine, RawBar
from tvbt.chan.reference import (
    ReferenceSegmentAccumulator,
    reference_segments,
)


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


def test_bi_cases_cover_confirmed_up_and_down_directions() -> None:
    """测试已完成笔是否覆盖上涨和下降两种方向。

    预期:输出笔同时包含 `up` 和 `down`,每条笔均由已确认分型构成；相邻笔
    只共享一个端点且方向严格交替，不把尚未形成分型的行情预览冒充为笔。
    """
    runtime = run_levels(([0, 1, 2, 3, 4, 3, 2, 1] * 4) + [0])
    rows = runtime.result_rows()["bi"]
    confirmed = [row for row in rows if row["status"] == "confirmed"]

    assert [(row["direction"], row["confirmed"]) for row in confirmed[:5]] == [
        ("down", True),
        ("up", True),
        ("down", True),
        ("up", True),
        ("down", True),
    ]
    assert all(row["confirmed"] is True for row in confirmed)
    assert all(row["confirmed_at_bar_index"] is not None for row in confirmed)
    assert {row["direction"] for row in confirmed} == {"up", "down"}
    assert any(row["status"] in {"candidate", "invalidated"} for row in rows)
    assert_connected_and_alternating(confirmed)


def test_bi_minimum_independent_bar_rule_blocks_too_short_confirmations() -> None:
    """测试五根独立 K 线成笔门槛和同类分型替换。

    预期:不足五根独立 K 线的快速交替分型不会各自成笔；最终只保留一条
    从区间最低底到最高顶、跨度满足门槛的已确认上涨笔。
    """
    runtime = run_levels([0, 3, 1, 4, 2, 5, 1, 6, 0, 7, 1, 8, 0])
    rows = runtime.result_rows()["bi"]

    assert [row["confirmed"] for row in rows] == [True]
    assert rows[0]["direction"] == "up"
    assert rows[0]["start_bar_index"] == 2
    assert rows[0]["end_bar_index"] == 7
    assert rows[0]["start_price_i64"] == min(item.low_i64 for item in runtime.included[2:8])
    assert rows[0]["end_price_i64"] == max(item.high_i64 for item in runtime.included[2:8])


def test_incremental_segment_scan_matches_full_scan_after_append_and_rollback() -> None:
    """测试线段增量扫描在追加笔和修订笔尾后是否等价于全量扫描。

    预期:逐步追加时只处理新增笔；从共同前缀回滚并替换方向尾部后，增量输出
    的每个段字段仍与从第 0 笔重新扫描的结果完全一致。
    """
    lines, bars = swing_components([21, 10, 15, 2, 44, 37, 46, 11, 18, 9, 26, 20, 30, 3, 25, 7])
    accumulator = ReferenceSegmentAccumulator()
    preserved = 0
    for cutoff in (4, 7, 10, len(lines)):
        incremental = accumulator.update(lines[:cutoff], bars, preserved)
        assert incremental == reference_segments(lines[:cutoff], bars)
        preserved = cutoff

    rollback_at = 8
    revised = [
        *lines[:rollback_at],
        *[
            replace(value, direction="down" if value.direction == "up" else "up")
            for value in lines[rollback_at:]
        ],
    ]
    assert accumulator.update(revised, bars, rollback_at) == reference_segments(revised, bars)


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
