from __future__ import annotations

import json
from itertools import pairwise
from pathlib import Path

import pytest

from tvbt.chan.engine import ChanEngine, ChanParameters, Fractal, LineObject, RawBar
from tvbt.chan.reference import ReferenceSegment, ReferenceSegmentAccumulator


def bar(index: int, high: int, low: int) -> RawBar:
    """构造一根只包含缠论测试所需字段的原始 K 线。"""
    middle = (high + low) // 2
    return RawBar(
        index,
        1_700_000_000_000 + index * 60_000,
        middle,
        high,
        low,
        middle,
    )


def test_raw_bar_rejects_open_or_close_outside_high_low_range() -> None:
    """测试原始 K 线是否校验完整 OHLC 价格关系。
    预期: 开盘价或收盘价超出最低价到最高价的闭区间时, 引擎拒绝该 K 线。
    """
    runtime = engine()

    with pytest.raises(ValueError, match="open is outside"):
        runtime.update(RawBar(0, 1_700_000_000_000, 11, 10, 0, 5))
    with pytest.raises(ValueError, match="close is outside"):
        runtime.update(RawBar(0, 1_700_000_000_000, 5, 10, 0, -1))


def test_revised_away_divergence_gets_causal_invalidation_revision() -> None:
    runtime = engine()
    runtime.emitter.upsert(
        10,
        "divergence",
        "candidate-1",
        {"bar_index": 7, "status": "candidate", "confirmed": False},
    )
    retained = runtime._retain_invalidated_divergences([], 12)
    runtime._sync_objects("divergence", retained, 12)
    current = runtime.emitter.current("divergence")
    assert len(current) == 1
    assert current[0]["status"] == "invalidated"
    assert current[0]["invalidation_reason"] == "decomposition_or_strength_revised"
    assert current[0]["known_at_bar_index"] == 12
    assert current[0]["object_revision"] == 2


def wave_bars(count: int = 25) -> list[RawBar]:
    """构造固定波形 K 线,用于稳定触发分型、笔和中枢。"""
    levels = [0, 1, 2, 3, 4, 3, 2, 1]
    result = []
    for index in range(count):
        cycle = index % 8
        level = levels[cycle]
        result.append(bar(index, level * 10 + 5, level * 10))
    return result


def engine() -> ChanEngine:
    """创建检查点间隔较小的缠论引擎,方便测试检查点和事件。"""
    return ChanEngine(ChanParameters(checkpoint_interval=4))


class FullStructureRescanEngine(ChanEngine):
    """测试专用全量基线，每次笔变化都丢弃全部上层增量状态。"""

    def _update_structures(self, known_at_bar_index: int, changed_bi_index: int) -> None:
        """从第 0 笔重建全部结构，用于验证增量优化没有改变任何事件。"""
        self._segment_accumulator = ReferenceSegmentAccumulator()
        self._bi_centers = []
        self._center_values = []
        self._segment_specs = []
        self._segment_records = []
        self._segment_lines = []
        self._all_segment_centers = []
        super()._update_structures(known_at_bar_index, 0)


def assert_bi_use_processed_extremes(runtime: ChanEngine) -> None:
    """断言每一笔都以包含处理后的 K 线闭区间方向极值作为端点。"""
    for value in runtime.bi:
        processed = runtime.included[value.start.normalized_index : value.end.normalized_index + 1]
        range_high = max(item.high_i64 for item in processed)
        range_low = min(item.low_i64 for item in processed)
        if value.direction == "up":
            assert (value.start.price_i64, value.end.price_i64) == (
                range_low,
                range_high,
            )
            assert (
                value.start.extreme_source_bar_index
                == runtime.included[value.start.normalized_index].low_raw_index
            )
            assert (
                value.end.extreme_source_bar_index
                == runtime.included[value.end.normalized_index].high_raw_index
            )
        else:
            assert (value.start.price_i64, value.end.price_i64) == (
                range_high,
                range_low,
            )
            assert (
                value.start.extreme_source_bar_index
                == runtime.included[value.start.normalized_index].high_raw_index
            )
            assert (
                value.end.extreme_source_bar_index
                == runtime.included[value.end.normalized_index].low_raw_index
            )


def line(index: int, start_price: int, end_price: int) -> LineObject:
    """构造一条首尾相接的笔对象,供中枢扫描器测试使用。"""
    direction = "up" if end_price > start_price else "down"
    start = Fractal(
        f"f-{index}",
        "bottom" if direction == "up" else "top",
        index,
        index,
        index * 60_000,
        start_price,
        index,
        index,
    )
    end = Fractal(
        f"f-{index + 1}",
        "top" if direction == "up" else "bottom",
        index + 1,
        index + 1,
        (index + 1) * 60_000,
        end_price,
        index + 1,
        index + 1,
    )
    return LineObject(f"bi-{index}", start, end, direction, index + 1, index + 1)


def test_reference_inclusion_merges_in_the_established_direction() -> None:
    """测试包含关系是否按已经建立的方向合并。

    预期:第三根 K 线被第二根包含时,不新增独立 K 线;在向上方向下取高高、
    低高,并保留被合并的原始 K 线索引。
    """
    runtime = engine()
    runtime.update(bar(0, 10, 0))
    runtime.update(bar(1, 12, 2))
    runtime.update(bar(2, 11, 3))
    assert len(runtime.included) == 2
    merged = runtime.included[-1]
    assert merged.direction == "up"
    assert (merged.high_i64, merged.low_i64) == (12, 3)
    assert merged.high_raw_index == 1
    assert merged.low_raw_index == 2
    assert merged.source_raw_indices == [1, 2]


def test_processed_bar_lifecycle_preserves_members_and_revision_events() -> None:
    """包含合并修订同一处理后 K 线，下一独立 K 线只封存而不回写方向。"""
    runtime = engine()
    runtime.update(RawBar(0, 1_000, 6, 10, 5, 9))
    runtime.update(RawBar(1, 2_000, 8, 9, 6, 7))
    runtime.update(RawBar(2, 3_000, 8, 11, 7, 10))

    processed = runtime.result_rows()["processed_bars"]
    assert len(processed) == 2
    first, current = processed
    assert first == {
        **first,
        "normalized_index": 0,
        "start_bar_index": 0,
        "end_bar_index": 1,
        "start_time": 1_000,
        "end_time": 2_000,
        "open_i64": 6,
        "close_i64": 7,
        "high_i64": 10,
        "low_i64": 6,
        "high_source_bar_index": 0,
        "low_source_bar_index": 1,
        "direction": "unknown",
        "source_bar_indices": [0, 1],
        "status": "sealed",
        "sealed_at_bar_index": 2,
        "catalog_event": "processed_bar_revision",
        "known_at_bar_index": 2,
        "object_revision": 3,
    }
    assert current["status"] == "forming"
    assert current["direction"] == "up"
    events = [
        event
        for event in runtime.emitter.events
        if event.object_type == "processed_bar" and event.object_id == first["object_id"]
    ]
    assert [(event.known_at_bar_index, event.object_revision) for event in events] == [
        (0, 1),
        (1, 2),
        (2, 3),
    ]


def test_fractal_candidate_confirmation_invalidation_and_aux_strength_are_causal() -> None:
    """候选等待右 K 线；确认/失效复用 ID，强弱只作为不可交易辅助标签。"""
    confirmed = engine()
    confirmed.update(RawBar(0, 1_000, 8, 10, 5, 9))
    confirmed.update(RawBar(1, 2_000, 8, 12, 7, 10))
    candidate = confirmed.result_rows()["fractals"][0]
    assert candidate["status"] == "candidate"
    assert candidate["confirmed_at_bar_index"] is None
    confirmed.update(RawBar(2, 3_000, 8, 9, 4, 6))
    resolved = next(
        value
        for value in confirmed.result_rows()["fractals"]
        if value["object_id"] == candidate["object_id"]
    )
    assert resolved["status"] == "confirmed"
    assert resolved["confirmed_at_bar_index"] == 2
    assert resolved["aux_strength"] == "strong_reversal"
    assert resolved["body_i64"] == 2
    assert resolved["upper_shadow_i64"] == 2
    assert resolved["lower_shadow_i64"] == 1
    assert resolved["range_i64"] == 5
    assert resolved["close_position_milli"] == 600
    assert resolved["feature_profile"] == "processed_bar_ohlc_v1"
    assert resolved["strength_semantic_namespace"] == "auxiliary"
    assert resolved["standard_signal"] is False
    assert resolved["execution_allowed"] is False

    invalidated = engine()
    invalidated.update(bar(0, 10, 5))
    invalidated.update(bar(1, 12, 7))
    invalidated_id = invalidated.result_rows()["fractals"][0]["object_id"]
    invalidated.update(bar(2, 14, 9))
    old = next(
        value
        for value in invalidated.result_rows()["fractals"]
        if value["object_id"] == invalidated_id
    )
    assert old["status"] == "invalidated"
    assert old["invalidation_reason"] == "right_bar_failed_strict_fractal_relation"


def test_bi_candidate_confirmation_and_live_four_state_are_visible_and_prefix_invariant() -> None:
    """合法候选笔在右分型确认前可见，确认后同 ID 升级且在线状态无未来回填。"""
    bars = [
        bar(0, 10, 5),
        bar(1, 8, 3),
        bar(2, 11, 6),
        bar(3, 12, 7),
        bar(4, 13, 8),
        bar(5, 14, 9),
        bar(6, 12, 7),
    ]
    prefix = engine()
    for item in bars[:6]:
        prefix.update(item)
    candidate = next(value for value in prefix.result_rows()["bi"] if not value["confirmed"])
    assert candidate["status"] == "candidate"
    assert candidate["confirmed_at_bar_index"] is None
    assert prefix.result_rows()["bi_states"][0]["state"] == "TOP_FORMING"

    full = engine()
    for item in bars:
        full.update(item)
    confirmed_line = next(
        value for value in full.result_rows()["bi"] if value["object_id"] == candidate["object_id"]
    )
    assert confirmed_line["status"] == "confirmed"
    assert confirmed_line["confirmed_at_bar_index"] == 6
    assert full.result_rows()["bi_states"][0]["state"] == "BOTTOM_FORMING"
    prefix_events = [event.row() for event in prefix.emitter.events]
    full_known_prefix = [
        event.row() for event in full.emitter.events if event.known_at_bar_index <= 5
    ]
    assert full_known_prefix == prefix_events


def test_reference_fractal_is_sealed_by_the_right_independent_bar() -> None:
    """测试分型是否必须等右侧独立 K 线出现后才封存。

    预期:顶部中间 K 线在右侧独立 K 线到来前不发布分型;右侧 K 线出现后,
    顶分型锚定在真实最高价所在 bar,并把确认位置记为右侧 bar。
    """
    runtime = engine()
    values = [0, 1, 2, 3, 4, 3, 2, 1]
    for index, value in enumerate(values[:5]):
        runtime.update(bar(index, value * 10 + 5, value * 10))
    assert runtime.fractals == []
    runtime.update(bar(5, values[5] * 10 + 5, values[5] * 10))
    assert len(runtime.fractals) == 1
    fractal = runtime.fractals[0]
    assert fractal.fractal_type == "top"
    assert fractal.bar_index == 4
    assert fractal.extreme_source_bar_index == 4
    assert fractal.zone_low_i64 == 40
    assert fractal.zone_high_i64 == 45
    assert fractal.payload()["zone_low_i64"] == 40
    assert fractal.payload()["zone_high_i64"] == 45
    assert fractal.confirmed_at_bar_index == 5


def test_reference_extremes_build_alternating_bi_and_local_center() -> None:
    """测试标准波形是否能生成方向交替的笔和已确认笔中枢。

    预期:确认笔至少包含向下、向上、向下三段交替结构,每笔跨度满足
    5 根独立 K 线门槛,并且输出的笔中枢拥有合法价格区间和因果确认时间。
    """
    runtime = engine()
    for item in wave_bars(40):
        runtime.update(item)
    rows = runtime.result_rows()
    confirmed_bi = [item for item in rows["bi"] if item["confirmed"]]
    assert len(confirmed_bi) >= 3
    assert [item["direction"] for item in confirmed_bi[:3]] == ["down", "up", "down"]
    assert all(
        value.end.normalized_index - value.start.normalized_index + 1 >= 5 for value in runtime.bi
    )
    assert_bi_use_processed_extremes(runtime)
    centers = [value for value in rows["local_centers"] if value["unit_kind"] == "BI"]
    assert centers
    center = centers[0]
    assert center["zd_i64"] < center["zg_i64"]
    assert center["status"] in {"ACTIVE", "FORMED", "EXTENDING", "PENDING_BREAK", "CLOSED"}
    assert center["known_at_bar_index"] >= center["formed_at_bar_index"]


def test_later_more_extreme_fractal_revises_existing_bi() -> None:
    """测试同类后顶更高时是否淘汰旧顶并修订已经发布的笔。

    预期:原先的 2714 顶和中间 2709 底不能保留为两条笔；后续 2730 有效顶
    出现后，上涨笔直接延伸到该更高顶，并满足 processed_k 全区间极值规则。
    """
    # AOL9 前 60 根包含近距离波动和包含关系，可复现旧算法不修订已确认笔的问题。
    high_low = [
        (2716, 2702),
        (2715, 2711),
        (2712, 2709),
        (2711, 2706),
        (2713, 2708),
        (2713, 2711),
        (2713, 2710),
        (2712, 2709),
        (2713, 2711),
        (2712, 2710),
        (2714, 2711),
        (2713, 2711),
        (2713, 2710),
        (2712, 2709),
        (2712, 2709),
        (2710, 2708),
        (2711, 2709),
        (2711, 2709),
        (2712, 2710),
        (2712, 2710),
        (2712, 2710),
        (2712, 2710),
        (2711, 2710),
        (2711, 2709),
        (2710, 2709),
        (2710, 2709),
        (2711, 2710),
        (2711, 2710),
        (2711, 2710),
        (2711, 2710),
        (2711, 2710),
        (2711, 2710),
        (2711, 2710),
        (2711, 2710),
        (2711, 2710),
        (2711, 2710),
        (2711, 2710),
        (2711, 2710),
        (2711, 2710),
        (2711, 2710),
        (2711, 2710),
        (2711, 2710),
        (2711, 2710),
        (2710, 2710),
        (2711, 2710),
        (2711, 2710),
        (2711, 2710),
        (2711, 2710),
        (2728, 2708),
        (2725, 2719),
        (2723, 2721),
        (2723, 2713),
        (2719, 2714),
        (2719, 2717),
        (2722, 2717),
        (2730, 2721),
        (2725, 2723),
        (2725, 2723),
        (2725, 2721),
        (2726, 2721),
    ]
    runtime = engine()
    for index, (high, low) in enumerate(high_low):
        runtime.update(bar(index, high, low))
    rows = runtime.result_rows()["bi"]
    confirmed_rows = [item for item in rows if item["status"] == "confirmed"]
    assert [
        (item["start_bar_index"], item["end_bar_index"], item["direction"], item["confirmed"])
        for item in confirmed_rows
    ] == [(3, 55, "up", True)]
    assert {item["status"] for item in rows} == {"confirmed", "invalidated"}
    assert_bi_use_processed_extremes(runtime)
    assert all(
        left["end_bar_index"] == right["start_bar_index"]
        for left, right in pairwise(confirmed_rows)
    )
    assert all(left["direction"] != right["direction"] for left, right in pairwise(confirmed_rows))


def test_yl9_higher_top_replaces_screenshot_lower_top() -> None:
    """测试 YL9 截图区间中 8578 后顶是否替换 8575 旧顶。

    预期:包含处理后保留的 8578 顶成为上涨笔终点和下一下降笔起点；下降笔
    到 8527 底结束，不能从 8575 旧顶出发并穿过更高的有效顶。
    """
    sample = Path(__file__).parents[2] / "trading-data" / "history" / "29#YL9.txt"
    if not sample.is_file():
        pytest.skip(f"唯一历史数据源中不存在完整测试文件：{sample}")
    source_rows = sample.read_text(encoding="gb18030").splitlines()[2:]
    runtime = engine()
    for index in range(57_300, 57_530):
        fields = [value.strip() for value in source_rows[index].split(",")]
        runtime.update(
            RawBar(
                index,
                1_700_000_000_000 + index * 300_000,
                int(fields[2]),
                int(fields[3]),
                int(fields[4]),
                int(fields[5]),
            )
        )

    endpoints = [
        (
            value.start.bar_index,
            value.start.price_i64,
            value.end.bar_index,
            value.end.price_i64,
            value.direction,
        )
        for value in runtime.bi
    ]
    assert (57_453, 8552, 57_477, 8578, "up") in endpoints
    assert (57_477, 8578, 57_515, 8527, "down") in endpoints
    assert not any(
        start_index == 57_469 and start_price == 8575 and direction == "down"
        for start_index, start_price, _, _, direction in endpoints
    )
    assert_bi_use_processed_extremes(runtime)


def test_algo_ui_segment_golden_for_aol9_prefix_is_exact() -> None:
    """测试 AOL9 前 300 根 K 线的首个段金样本。

    预期:首个段的起止 K 线、起止价格和方向与 `algo-ui` 参考实现一致,
    并且所有输出段保持方向交替。
    """
    sample = Path(__file__).parents[2] / "trading-data" / "history" / "30#AOL9.txt"
    if not sample.is_file():
        pytest.skip(f"唯一历史数据源中不存在完整测试文件：{sample}")
    runtime = engine()
    rows = sample.read_text(encoding="gb18030").splitlines()[2:302]
    for index, raw in enumerate(rows):
        fields = [value.strip() for value in raw.split(",")]
        runtime.update(
            RawBar(
                index,
                1_700_000_000_000 + index * 300_000,
                int(fields[2]),
                int(fields[3]),
                int(fields[4]),
                int(fields[5]),
            )
        )
    segments = runtime.result_rows()["segments"]
    assert [
        (
            value["start_bar_index"],
            value["end_bar_index"],
            value["start_price_i64"],
            value["end_price_i64"],
            value["direction"],
        )
        for value in segments
    ] == [(141, 237, 2706, 2826, "up")]
    assert all(left["direction"] != right["direction"] for left, right in pairwise(segments))
    assert all(
        value["start_bar_index"] <= value["range_low_source_bar_index"] <= value["end_bar_index"]
        and value["start_bar_index"]
        <= value["range_high_source_bar_index"]
        <= value["end_bar_index"]
        for value in segments
    )


def test_segment_actual_range_excludes_bi_starting_at_end_pivot() -> None:
    """终点索引指向下一笔；下一笔的后续低点不能倒灌进上一段。"""
    first = Fractal("p0", "bottom", 0, 25677, 0, 3191, 0, 0)
    pivot = Fractal("p1", "top", 1, 25699, 1, 3251, 1, 1)
    future = Fractal("p2", "bottom", 2, 25756, 2, 3155, 2, 2)
    bi = [
        LineObject("first", first, pivot, "up", 1, 1),
        LineObject("next", pivot, future, "down", 2, 2),
    ]
    segment = ReferenceSegment(start_index=0, end_index=1)
    components = ChanEngine._segment_component_bi(bi, segment)
    assert [line.object_id for line in components] == ["first"]
    assert min(line.range_low_i64 for line in components) == 3191
    assert max(line.range_high_i64 for line in components) == 3251


def test_forming_segment_observation_uses_current_bar_not_stale_candidate_endpoint() -> None:
    runtime = engine()
    start = Fractal("start", "bottom", 0, 0, 0, 8, 0, 0)
    pivot = Fractal("pivot", "top", 1, 2, 2, 10, 2, 2)
    runtime.bi = [LineObject("bi-0", start, pivot, "up", 2, 2)]
    runtime._segment_lines = [LineObject("prior", start, pivot, "up", 2, 2)]
    runtime._segment_specs = [
        ReferenceSegment(
            start_index=0,
            end_index=1,
            up=True,
            confirmed=False,
            known_at_bar_index=3,
            start_bar_index=2,
            end_bar_index=3,
            start_time=2,
            end_time=3,
            start_price_i64=10,
            end_price_i64=11,
        )
    ]
    runtime.raw_bars = [
        RawBar(0, 1, 8, 9, 7, 8),
        RawBar(1, 2, 8, 10, 8, 9),
        RawBar(2, 3, 10, 10, 9, 10),
        RawBar(3, 4, 10, 11, 10, 11),
        RawBar(4, 5, 11, 13, 10, 12),
    ]
    forming = runtime._forming_segment_observation()
    assert forming is not None
    assert forming.start.bar_index == 2
    assert forming.end.bar_index == 4
    assert forming.range_high_i64 == 13
    assert forming.range_high_source_bar_index == 4
    assert forming.range_profile == "forming_observed_bars_v1"


def test_segment_local_centers_and_third_points_are_causal_on_aol9() -> None:
    """测试 AOL9 前缀上的标准线段中枢与三买信号因果性。

    预期:标准线段中枢均满足 `ZD < ZG`,中枢监视对象具有合法强弱和相对位置,
    三买信号出现在固定金样本位置,且背驰与买卖点不会早于确认 K 线发布。
    """
    sample = Path(__file__).parents[2] / "trading-data" / "history" / "30#AOL9.txt"
    if not sample.is_file():
        pytest.skip(f"唯一历史数据源中不存在完整测试文件：{sample}")
    runtime = engine()
    rows = sample.read_text(encoding="gb18030").splitlines()[2:5002]
    for index, raw in enumerate(rows):
        fields = [value.strip() for value in raw.split(",")]
        runtime.update(
            RawBar(
                index,
                1_700_000_000_000 + index * 300_000,
                int(fields[2]),
                int(fields[3]),
                int(fields[4]),
                int(fields[5]),
            )
        )
    result = runtime.result_rows()
    segment_centers = [
        value for value in result["local_centers"] if value["unit_kind"] == "SEGMENT"
    ]
    assert segment_centers
    assert all(value["zd_i64"] < value["zg_i64"] for value in segment_centers)
    assert all(
        value["structural_level"] == "segment"
        and len(value["seed_ids"]) == 3
        and value["observed_low_i64"]
        <= value["zd_i64"]
        < value["zg_i64"]
        <= value["observed_high_i64"]
        and value["known_at_bar_index"] >= value["formed_at_bar_index"]
        for value in segment_centers
    )
    assert result["movement_states"]
    assert result["center_monitors"]
    assert all(
        value["known_at_bar_index"] >= value["confirmed_at_bar_index"]
        and value["relative_position"] in {"above", "below", "equal"}
        and value["oscillation_bias"] in {"strong", "weak", "neutral"}
        and value["z_twice_i64"] == value["core_low_i64"] + value["core_high_i64"]
        and value["zn_twice_i64"] == value["range_low_i64"] + value["range_high_i64"]
        and value["component_ordinal"] <= 9
        and value["catalog_algorithm_id"] == "ALG-AUX-004"
        and value["semantic_namespace"] == "auxiliary"
        and value["standard_signal"] is False
        and value["execution_allowed"] is False
        and value["confirms_third_point"] is False
        and value["breakout_warning"]
        in {
            None,
            "cross_above_b",
            "cross_below_a",
            "rising_wedge_below_b",
            "falling_wedge_above_a",
        }
        for value in result["center_monitors"]
    )
    # 15.0.0 使用线段全部组成笔的实际区间；旧版只看端点时产生的 4689 三买
    # 在新范围下首次回试进入核心，因此不能继续发布。
    assert any(
        value["range_low_i64"] < min(value["start_price_i64"], value["end_price_i64"])
        or value["range_high_i64"] > max(value["start_price_i64"], value["end_price_i64"])
        for value in result["segments"]
    )
    standard_points = [
        (value["signal_type"], value["bar_index"])
        for value in result["trade_points"]
        if value["signal_class"] == "standard"
    ]
    assert standard_points
    assert all(signal_type == "buy_3" for signal_type, _ in standard_points)
    assert any(
        value["signal_class"] == "class_like"
        and value["signal_type"] in {"class_buy_1", "class_sell_1"}
        for value in result["trade_points"]
    )
    assert all(
        value["confirmed_at_bar_index"] is None
        or value["known_at_bar_index"] >= value["confirmed_at_bar_index"]
        for value in [*result["divergences"], *result["trade_points"]]
    )


def test_chan_event_stream_is_prefix_invariant_for_multiple_cutoffs() -> None:
    """测试缠论事件流的多前缀不变性。

    预期:任意前缀运行产生的事件,等于全量运行中在该前缀截止点以前已经
    可知的事件集合,防止未来结构倒灌到更早回放时点。
    """
    bars = wave_bars(30)
    full = engine()
    for item in bars:
        full.update(item)
    full_rows = [event.row() for event in full.emitter.events]
    for cutoff in (8, 12, 20, 27):
        prefix = engine()
        for item in bars[:cutoff]:
            prefix.update(item)
        expected = [row for row in full_rows if row["known_at_bar_index"] < cutoff]
        assert [event.row() for event in prefix.emitter.events] == expected


def test_engine_state_restore_matches_uninterrupted_events_and_objects() -> None:
    """测试检查点恢复后的事件和对象是否等同于不间断运行。

    预期:前缀导出状态再恢复继续运行后,事件序列和最终对象快照都与
    从头连续运行完全一致。
    """
    bars = wave_bars(30)
    full = engine()
    for item in bars:
        full.update(item)

    prefix = engine()
    for item in bars[:17]:
        prefix.update(item)
    prefix_events = [event.row() for event in prefix.emitter.events]
    restored = ChanEngine.from_state(prefix.export_state())
    for item in bars[17:]:
        restored.update(item)
    combined = [*prefix_events, *(event.row() for event in restored.emitter.events)]
    assert combined == [event.row() for event in full.emitter.events]
    assert restored.result_rows() == full.result_rows()


def test_json_checkpoint_keeps_same_anchor_object_order_stable() -> None:
    runtime = engine()
    for object_type, anchor in (
        ("bi", "start_bar_index"),
        ("center_monitor", "bar_index"),
        ("trade_point", "bar_index"),
    ):
        for object_id in ("object-z", "object-a"):
            runtime.emitter.upsert(5, object_type, object_id, {anchor: 5})
    restored = ChanEngine.from_state(json.loads(json.dumps(runtime.export_state(), sort_keys=True)))
    assert restored.result_rows() == runtime.result_rows()


def test_incremental_upper_structures_match_forced_full_rescan_events() -> None:
    """测试上层结构增量更新是否与每次强制全量重扫完全等价。

    预期:AOL9 前缀上，增量引擎和全量基线产生完全相同的事件顺序、修订号、
    可知时间及最终对象，证明稳定前缀复用只优化计算量而不改变缠论事实。
    """
    sample = Path(__file__).parents[2] / "trading-data" / "history" / "30#AOL9.txt"
    if not sample.is_file():
        pytest.skip(f"唯一历史数据源中不存在完整测试文件：{sample}")
    rows = sample.read_text(encoding="gb18030").splitlines()[2:5002]
    incremental = engine()
    full_rescan = FullStructureRescanEngine(ChanParameters(checkpoint_interval=4))
    for index, raw in enumerate(rows):
        fields = [value.strip() for value in raw.split(",")]
        value = RawBar(
            index,
            1_700_000_000_000 + index * 300_000,
            int(fields[2]),
            int(fields[3]),
            int(fields[4]),
            int(fields[5]),
        )
        incremental.update(value)
        full_rescan.update(value)

    assert [event.row() for event in incremental.emitter.events] == [
        event.row() for event in full_rescan.emitter.events
    ]
    assert incremental.result_rows() == full_rescan.result_rows()


def test_first_point_candidate_invalidation_is_retained_as_revision() -> None:
    engine_value = ChanEngine(ChanParameters())
    payload = {
        "bar_index": 10,
        "time": 600_000,
        "price_i64": 100,
        "signal_type": "buy_1",
        "divergence_kind": None,
        "signal_class": "standard",
        "strength": None,
        "reference_object_id": "center-2",
        "macd_area_reference": None,
        "macd_area_current": None,
        "status": "candidate",
        "invalidation_reason": None,
        "level_id": "L0",
        "lower_level_turn_object_id": None,
        "catalog_event": "B1_candidate",
        "catalog_algorithm_id": "ALG-SIG-001",
        "confirmed": False,
        "confirmed_at_bar_index": None,
    }
    engine_value.emitter.upsert(10, "trade_point", "trade-point-candidate", payload)

    retained = engine_value._retain_invalidated_first_points([], 12)
    engine_value._sync_objects("trade_point", retained, 12)

    value = engine_value.result_rows()["trade_points"][0]
    assert value["object_id"] == "trade-point-candidate"
    assert value["status"] == "invalidated"
    assert value["invalidation_reason"] == "trend_structure_revised"
    assert value["catalog_event"] == "B1_invalidated"
    assert value["known_at_bar_index"] == 12
    assert value["object_revision"] == 2
