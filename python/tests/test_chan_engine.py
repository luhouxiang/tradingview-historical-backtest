from __future__ import annotations

from dataclasses import replace
from itertools import pairwise
from pathlib import Path

import pytest

from tvbt.chan.engine import ChanEngine, ChanParameters, Fractal, LineObject, RawBar
from tvbt.chan.reference import ReferenceSegmentAccumulator, reference_centers


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


def test_reference_extremes_build_alternating_bi_and_confirmed_center() -> None:
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
    assert rows["zhongshu"]
    center = rows["zhongshu"][0]
    assert center["zd_i64"] < center["zg_i64"]
    assert center["status"] in {"confirmed", "extended", "left"}
    assert center["leave_direction"] in {None, "up", "down"}
    assert center["known_at_bar_index"] >= center["confirmed_at_bar_index"]


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
    assert [
        (item["start_bar_index"], item["end_bar_index"], item["direction"], item["confirmed"])
        for item in rows
    ] == [(3, 55, "up", True)]
    assert_bi_use_processed_extremes(runtime)
    assert all(left["end_bar_index"] == right["start_bar_index"] for left, right in pairwise(rows))
    assert all(left["direction"] != right["direction"] for left, right in pairwise(rows))


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


def test_standard_segment_centers_and_third_points_are_causal_on_aol9() -> None:
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
    assert len(result["segment_zhongshu"]) == 3
    assert all(value["zd_i64"] < value["zg_i64"] for value in result["segment_zhongshu"])
    assert all(
        value["analysis_level"] == "segment"
        and value["component_kind"] == "segment"
        and value["component_count"] >= 3
        and value["dd_i64"] <= value["zd_i64"] < value["zg_i64"] <= value["gg_i64"]
        and value["z_i64"] == (value["zd_i64"] + value["zg_i64"]) // 2
        for value in result["segment_zhongshu"]
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
    assert [
        (value["signal_type"], value["bar_index"])
        for value in result["trade_points"]
        if value["signal_class"] == "standard"
    ] == [("buy_3", 4689)]
    assert any(
        value["signal_class"] == "class_like"
        and value["signal_type"] in {"class_buy_1", "class_sell_1"}
        for value in result["trade_points"]
    )
    assert all(
        value["known_at_bar_index"] >= value["confirmed_at_bar_index"]
        for value in [*result["divergences"], *result["trade_points"]]
    )


def test_algo_ui_center_starts_from_three_same_parity_lines_and_extends() -> None:
    """测试中枢是否从同奇偶三笔形成并可继续延伸。

    预期:初始两条同奇偶笔冻结 `[ZD, ZG]` 核心,后续同奇偶相交笔只延长
    时间范围,不改变核心;首次不相交时标记离开方向。
    """
    lines = [
        line(0, 15, 20),
        line(1, 20, 0),
        line(2, 0, 10),
        line(3, 10, 2),
        line(4, 2, 8),
        line(5, 8, 4),
        line(6, 4, 12),
        line(7, 12, 9),
        line(8, 9, 20),
        line(9, 20, 12),
    ]
    confirmed = reference_centers(lines[:5])[0]
    extended = reference_centers(lines[:7])[0]
    left = reference_centers(lines)[0]
    assert confirmed.status == "confirmed"
    assert (confirmed.zd_i64, confirmed.zg_i64) == (2, 10)
    assert extended.status == "extended"
    assert left.status == "left"
    assert left.leave_direction == "up"
    assert left.known_at_bar_index == 8


def test_algo_ui_center_does_not_require_a_fourth_return_line() -> None:
    """测试笔中枢形成是否不要求第四笔返回。

    预期:只要基准同奇偶两笔存在交集,就能立即生成中枢,不需要额外等待
    第四笔回到该区间。
    """
    lines = [
        line(0, 15, 20),
        line(1, 20, 0),
        line(2, 0, 10),
        line(3, 10, 2),
        line(4, 2, 12),
        line(5, 12, 11),
    ]
    centers = reference_centers(lines)
    assert len(centers) == 1
    assert (centers[0].zd_i64, centers[0].zg_i64) == (2, 10)


def test_algo_ui_center_base_progression_matches_reference_semantics() -> None:
    """测试中枢扫描基点推进规则是否匹配参考语义。

    预期:连续中枢的 `base_index` 与 `seed_end_index` 按参考实现的
    `new_base - 1 / new_base - 2` 规则推进。
    """
    lines = [
        line(0, 15, 20),
        line(1, 20, 0),
        line(2, 0, 10),
        line(3, 10, 2),
        line(4, 2, 8),
        line(5, 8, 4),
        line(6, 4, 12),
        line(7, 12, 9),
        line(8, 9, 20),
        line(9, 20, 12),
        line(10, 12, 18),
        line(11, 18, 14),
    ]
    centers = reference_centers(lines)
    assert [(value.base_index, value.seed_end_index) for value in centers] == [
        (1, 3),
        (7, 9),
        (9, 11),
    ]


def test_center_known_at_is_the_latest_participating_line() -> None:
    """测试中枢可知时间是否取参与笔中的最晚时间。

    预期:若参与中枢的某条笔较晚才可知,中枢 `known_at_bar_index` 必须等于
    该最晚可知位置,不能提前显示。
    """
    lines = [
        line(0, 15, 20),
        line(1, 20, 0),
        line(2, 0, 10),
        line(3, 10, 2),
        line(4, 2, 8),
        replace(line(5, 8, 4), confirmed_at_bar_index=20, known_at_bar_index=20),
        line(6, 4, 12),
        line(7, 12, 9),
    ]
    center = reference_centers(lines)[0]
    assert center.known_at_bar_index == 20


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
