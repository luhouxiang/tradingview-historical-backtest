from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tvbt.chan.engine import ChanEngine, Fractal, IncludedBar, RawBar

FIXTURE_PATH = (
    Path(__file__).parents[2]
    / "docs"
    / "chanlun_bi_108_testcases_20260817"
    / "fixtures"
    / "bi_cases.json"
)
FIXTURE = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
CASES = {case["id"]: case for case in FIXTURE["cases"]}


def _raw(index: int, high: int, low: int) -> RawBar:
    middle = (high + low) // 2
    return RawBar(index, 1_700_000_000_000 + index * 60_000, middle, high, low, middle)


def _run_processed(rows: list[dict[str, Any]]) -> ChanEngine:
    runtime = ChanEngine()
    for index, row in enumerate(rows):
        runtime.update(_raw(index, int(row["high"]), int(row["low"])))
    assert len(runtime.included) == len(rows), "fixture marked processed K-lines must not merge"
    return runtime


@pytest.mark.parametrize(
    "case_id",
    ["L62_F1_TOP_FRACTAL", "L62_F2_BOTTOM_FRACTAL"],
)
def test_108_fixture_strict_fractal_detection(case_id: str) -> None:
    """第62课图1/图2：分型必须在无包含K线上按高低点同时严格比较。"""
    case = CASES[case_id]
    runtime = _run_processed(case["input"]["processed_klines"])
    expected = case["expected"]["fractals"]

    assert [
        {
            "type": value.fractal_type,
            "center": case["input"]["processed_klines"][value.normalized_index]["id"],
            "price": value.price_i64,
        }
        for value in runtime.fractals
    ] == [
        {"type": value["type"], "center": value["center"], "price": value["price"]}
        for value in expected
    ]


def test_108_fixture_three_k_complete_classification() -> None:
    """第62课图7：连续上升/下降不能误报为分型。"""
    case = CASES["L62_F7_THREE_K_CLASSIFICATION"]
    observed: dict[str, str] = {}
    for name, values in case["input"]["samples"].items():
        runtime = _run_processed(
            [
                {"id": f"{name}-{index}", "high": high, "low": low}
                for index, (high, low) in enumerate(values)
            ]
        )
        observed[name] = runtime.fractals[0].fractal_type if runtime.fractals else "none"
    assert observed == case["expected"]["classification"]


@pytest.mark.parametrize("case_id", ["L62_F6_UP_INCLUSION", "L62_F6_DOWN_INCLUSION"])
def test_108_fixture_directional_inclusion_and_raw_extreme_sources(case_id: str) -> None:
    """第62/65课：顺序包含合并必须保留高低点各自的原始K线来源。"""
    case = CASES[case_id]
    raw_rows = case["input"]["raw_klines"]
    runtime = ChanEngine()
    for index, row in enumerate(raw_rows):
        runtime.update(_raw(index, row["high"], row["low"]))
    merged = runtime.included[-1]
    expected = case["expected"]["merged_kline"]
    raw_ids = [row["id"] for row in raw_rows]

    assert {
        "high": merged.high_i64,
        "low": merged.low_i64,
        "members": [raw_ids[index] for index in merged.source_raw_indices],
        "high_source": raw_ids[merged.high_raw_index],
        "low_source": raw_ids[merged.low_raw_index],
    } == expected


@pytest.mark.parametrize(
    "case_id",
    [
        "L62_F3_SHARED_K_INVALID_BI",
        "L62_F4_NO_FREE_K_INVALID_BI",
        "L62_F5_MINIMAL_VALID_DOWN_BI",
        "L62_F5_MINIMAL_VALID_UP_BI",
    ],
)
def test_108_fixture_strict_bi_assembly(case_id: str) -> None:
    """第62/77课：共享K、无独立K拒绝，中心相隔四位是最小有效笔。"""
    case = CASES[case_id]
    runtime = _run_processed(case["input"]["processed_klines"])
    ids = [row["id"] for row in case["input"]["processed_klines"]]
    observed = [
        {
            "direction": line.direction,
            "start": {
                "fractal": line.start.fractal_type,
                "center": ids[line.start.normalized_index],
                "price": line.start.price_i64,
            },
            "end": {
                "fractal": line.end.fractal_type,
                "center": ids[line.end.normalized_index],
                "price": line.end.price_i64,
            },
        }
        for line in runtime.bi
    ]
    assert observed == case["expected"].get("completed_bi", [])


def _included(index: int, high: int = 10, low: int = 7) -> IncludedBar:
    return IncludedBar(
        normalized_index=index,
        start_raw_index=index,
        end_raw_index=index,
        high_i64=high,
        low_i64=low,
        high_time=index,
        low_time=index,
        high_raw_index=index,
        low_raw_index=index,
        confirm_time=index,
        direction="up" if index else "unknown",
        source_raw_indices=[index],
    )


def _fractal(candidate: dict[str, Any]) -> Fractal:
    kind = candidate["type"]
    index = int(candidate["index"])
    return Fractal(
        object_id=candidate["id"],
        fractal_type=kind,
        normalized_index=index,
        bar_index=index,
        time=index,
        price_i64=int(candidate["price"]),
        confirmed_at_bar_index=index + 1,
        known_at_bar_index=index + 1,
        extreme_source_bar_index=index,
    )


@pytest.mark.parametrize(
    "case_id",
    [
        "L77_KEEP_LATER_HIGHER_TOP",
        "L77_KEEP_LATER_LOWER_BOTTOM",
        "L77_EQUAL_TOPS_KEEP_FIRST_ON_OPPOSITE",
    ],
)
def test_108_fixture_same_type_fractal_reduction(case_id: str) -> None:
    """第77课：后顶更高、后底更低；同价时反向分型到来后保留先出现者。"""
    case = CASES[case_id]
    candidates = case["input"]["fractal_candidates"]
    runtime = ChanEngine()
    runtime.included = [
        _included(index) for index in range(max(item["index"] for item in candidates) + 1)
    ]
    for candidate in candidates:
        index = int(candidate["index"])
        if candidate["type"] == "top":
            runtime.included[index].high_i64 = int(candidate["price"])
        else:
            runtime.included[index].low_i64 = int(candidate["price"])
        endpoint = _fractal(candidate)
        runtime.fractals.append(endpoint)
        runtime._consume_fractal(endpoint)

    expected = case["expected"]["completed_bi"]
    assert [
        {"direction": line.direction, "start": line.start.object_id, "end": line.end.object_id}
        for line in runtime.bi
    ] == expected


def test_108_fixture_exact_tick_comparison_uses_no_float_epsilon() -> None:
    """第81课：相差8个最小跳动单位的底分型必须保留更低者。"""
    case = CASES["L81_EXACT_TICK_COMPARISON"]
    first, second = case["input"]["bottom_candidates"]
    first_fractal = _fractal(
        {"id": first["id"], "type": "bottom", "index": 3, "price": first["price_ticks"]}
    )
    second_fractal = _fractal(
        {"id": second["id"], "type": "bottom", "index": 7, "price": second["price_ticks"]}
    )

    assert (
        first_fractal.price_i64 - second_fractal.price_i64 == case["expected"]["difference_ticks"]
    )
    assert ChanEngine._is_more_extreme(second_fractal, first_fractal)


def test_108_fixture_provisional_endpoint_is_revised_by_later_higher_top() -> None:
    """第69课：尾部上涨笔的顶端在反向底到来前必须允许后顶抬高。"""
    case = CASES["L69_PROVISIONAL_ENDPOINT_REVISION"]
    candidates = [
        {"id": "b0", "type": "bottom", "index": 1, "price": 4},
        {"id": "t1", "type": "top", "index": 5, "price": 12},
        {"id": "t2", "type": "top", "index": 9, "price": 14},
        {"id": "b1", "type": "bottom", "index": 13, "price": 5},
    ]
    runtime = ChanEngine()
    runtime.included = [_included(index) for index in range(15)]
    observed: list[str] = []
    for candidate in candidates:
        index = candidate["index"]
        if candidate["type"] == "top":
            runtime.included[index].high_i64 = candidate["price"]
        else:
            runtime.included[index].low_i64 = candidate["price"]
        endpoint = _fractal(candidate)
        runtime.fractals.append(endpoint)
        runtime._consume_fractal(endpoint)
        if runtime.bi:
            observed.append(runtime.bi[0].end.object_id)

    assert observed[:2] == [
        case["expected"]["after_event_2"]["candidate_up_bi"]["end"],
        case["expected"]["after_event_3"]["candidate_up_bi"]["end"],
    ]
    assert (
        runtime.bi[0].start.object_id
        == case["expected"]["after_event_4"]["confirmed_up_bi"]["start"]
    )
    assert (
        runtime.bi[0].end.object_id == case["expected"]["after_event_4"]["confirmed_up_bi"]["end"]
    )


def test_108_fixture_top_fractal_alone_does_not_emit_down_bi() -> None:
    """第79课：只有顶分型且没有有效底分型时不得输出向下笔。"""
    runtime = _run_processed(CASES["L62_F1_TOP_FRACTAL"]["input"]["processed_klines"])
    assert runtime.fractals[0].fractal_type == "top"
    assert runtime.bi == []


def test_108_fixture_segment_exception_cannot_relax_bi_interval_extremes() -> None:
    """第78课边界：内部更高点存在时，不得套用线段例外生成下降笔。"""
    case = CASES["L78_SEGMENT_RULE_MUST_NOT_RELAX_BI"]
    runtime = ChanEngine()
    runtime.included = [_included(index) for index in range(7)]
    runtime.included[1].high_i64 = 12
    runtime.included[3].high_i64 = 14
    runtime.included[5].low_i64 = 2
    for candidate in (
        {"id": "t1", "type": "top", "index": 1, "price": 12},
        {"id": "b1", "type": "bottom", "index": 5, "price": 2},
    ):
        endpoint = _fractal(candidate)
        runtime.fractals.append(endpoint)
        runtime._consume_fractal(endpoint)

    assert case["expected"]["accepted"] is False
    assert runtime.bi == []
