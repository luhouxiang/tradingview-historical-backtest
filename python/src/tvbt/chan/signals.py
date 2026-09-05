from __future__ import annotations

from collections.abc import Mapping, MutableMapping, Sequence
from dataclasses import dataclass, replace
from typing import Literal

from tvbt.chan.reference import LineLike, ReferenceCenter

"""线段级缠论信号生成。

本文件消费 `engine.py` 生成的已确认线段和 `reference.py` 生成的标准线段
中枢，只负责语义信号，不负责订单、成交或收益计算。

信号分两层：

- 背驰：趋势背驰和盘整背驰，使用结构关系加 MACD 同方向柱面积比较。
- 买卖点：标准一二三类点，以及项目显式定义的“类一/类二/类三”生命周期点。
"""

# 背驰类别。trend 对应 a+Z1+b+Z2+c，consolidation 对应 a+Z+c。
DivergenceKind = Literal["trend", "consolidation"]
# 二类点强弱：三类点同点、未突破一类点、突破但自身盘整背驰确认。
SignalStrength = Literal["strongest", "normal", "weakest"]
# standard 是 108 课标准点；class_like 是项目定义的盘整背驰派生点。
SignalClass = Literal["standard", "class_like"]
# 图表和对象树可见的信号全集。
SignalType = Literal[
    "bottom_divergence",
    "top_divergence",
    "buy_1",
    "buy_2",
    "buy_3",
    "sell_1",
    "sell_2",
    "sell_3",
    "class_buy_1",
    "class_buy_2",
    "class_buy_3",
    "class_sell_1",
    "class_sell_2",
    "class_sell_3",
]
MacdAreaKey = tuple[str, int, int, str]


@dataclass(frozen=True)
class ChanSignal:
    """缠论背驰或买卖点。

    字段说明：

    - `signal_type`：最终展示和策略消费的信号类型。
    - `divergence_kind`：仅背驰对象有值；买卖点为 None。
    - `signal_class/strength`：买卖点分层；背驰本身不填。
    - `segment_index`：信号理论端点所在的线段序号。
    - `bar_index/time/price_i64`：理论端点锚点，不是确认 K 线。
    - `reference_object_id`：关联中枢或背驰对象 ID。
    - `macd_area_reference/current`：背驰力度比较面积，买卖点为 None。
    - `known_at_bar_index`：信号最早可见位置，通常是后续反向段确认时刻。
    """

    signal_type: SignalType
    divergence_kind: DivergenceKind | None
    signal_class: SignalClass | None
    strength: SignalStrength | None
    segment_index: int
    bar_index: int
    time: int
    price_i64: int
    reference_object_id: str | None
    macd_area_reference: float | None
    macd_area_current: float | None
    known_at_bar_index: int
    status: Literal["candidate", "confirmed", "invalidated"] = "confirmed"
    invalidation_reason: str | None = None
    level_id: str | None = "L0"
    lower_level_turn_object_id: str | None = None
    catalog_event: (
        Literal[
            "B1_candidate",
            "B1_confirmed",
            "B1_invalidated",
            "S1_candidate",
            "S1_confirmed",
            "S1_invalidated",
        ]
        | None
    ) = None
    catalog_algorithm_id: Literal["ALG-SIG-001"] | None = None
    evidence_profile: str = "chan108_single_scope_v1"
    comparison_reference_object_id: str | None = None
    comparison_current_object_id: str | None = None
    comparison_rule: str | None = None
    new_extreme_satisfied: bool | None = None
    departure_object_id: str | None = None
    return_object_id: str | None = None
    return_ordinal: int | None = None
    boundary_profile: str | None = None
    boundary_relation: str | None = None
    return_depth_to_core_i64: int | None = None
    return_depth_to_outer_i64: int | None = None
    follow_through_object_id: str | None = None
    follow_through_status: Literal["pending", "observed", "not_applicable"] = "not_applicable"
    reference_center_ordinal: int | None = None
    older_center_count: int | None = None
    center_chain_profile: str | None = None

    @property
    def confirmation_latency_bars(self) -> int:
        return self.known_at_bar_index - self.bar_index


def _low(line: LineLike) -> int:
    value: int | None = getattr(line, "range_low_i64", None)
    return value if value is not None else min(line.start.price_i64, line.end.price_i64)


def _high(line: LineLike) -> int:
    value: int | None = getattr(line, "range_high_i64", None)
    return value if value is not None else max(line.start.price_i64, line.end.price_i64)


def _outer_range(center: ReferenceCenter, segments: Sequence[LineLike]) -> tuple[int, int]:
    """返回中枢参与组件完整外包络 `[DD, GG]`。"""
    components = segments[center.base_index : center.end_index + 1]
    return min(_low(line) for line in components), max(_high(line) for line in components)


def _previous_same_direction(
    segments: Sequence[LineLike], before_index: int, direction: str
) -> int | None:
    """从 `before_index` 前向左找最近同向线段，作为盘整背驰的 a 段。"""
    return next(
        (
            index
            for index in range(before_index - 1, -1, -1)
            if segments[index].direction == direction
        ),
        None,
    )


def _center_component_known_at(
    center: ReferenceCenter,
    segments: Sequence[LineLike],
    component_index: int,
) -> int:
    """Return when an oscillation component is known to belong to the center.

    The frozen center is first available after its three seed components.  An
    intervening opposite-direction component is only proven to be part of an
    extension when the following same-parity component overlaps the core.  The
    derived divergence therefore uses the later structural discovery time
    instead of backfilling the component's earlier endpoint time.
    """
    initial_known_at = max(
        segments[index].known_at_bar_index
        for index in range(center.base_index, center.seed_end_index + 1)
    )
    if component_index <= center.seed_end_index:
        return initial_known_at
    membership_index = (
        component_index
        if (component_index - center.base_index) % 2 == 0
        else min(component_index + 1, center.end_index)
    )
    return max(
        initial_known_at,
        segments[component_index].known_at_bar_index,
        segments[membership_index].known_at_bar_index,
    )


def _macd_area(
    line: LineLike,
    histogram: Mapping[int, float],
    cache: MutableMapping[MacdAreaKey, float] | None = None,
) -> float:
    """计算线段同方向 MACD 柱面积。

    向上线段只累计正柱；向下线段只累计负柱绝对值。MACD 只用于结构成立后的
    力度比较，不能单独生成缠论信号。
    """
    key = (line.object_id, line.start.bar_index, line.end.bar_index, line.direction)
    if cache is not None and key in cache:
        return cache[key]
    values = (
        histogram.get(index, 0.0) for index in range(line.start.bar_index, line.end.bar_index + 1)
    )
    if line.direction == "up":
        result = sum(max(value, 0.0) for value in values)
    else:
        result = sum(abs(min(value, 0.0)) for value in values)
    if cache is not None:
        cache[key] = result
    return result


def _divergence(
    kind: DivergenceKind,
    reference: LineLike,
    current: LineLike,
    current_index: int,
    reference_object_id: str,
    known_at_bar_index: int,
    histogram: Mapping[int, float],
    area_cache: MutableMapping[MacdAreaKey, float] | None = None,
    *,
    require_new_extreme: bool = True,
    follow_through_object_id: str | None = None,
) -> ChanSignal | None:
    """比较参考段和当前段，若当前段力度收缩则生成背驰。"""
    if reference.direction != current.direction:
        return None
    reference_area = _macd_area(reference, histogram, area_cache)
    current_area = _macd_area(current, histogram, area_cache)
    if reference_area <= 0.0 or current_area >= reference_area:
        return None
    if current.direction == "up":
        if require_new_extreme and _high(current) <= _high(reference):
            return None
        signal_type: SignalType = "top_divergence"
        price = _high(current)
    else:
        if require_new_extreme and _low(current) >= _low(reference):
            return None
        signal_type = "bottom_divergence"
        price = _low(current)
    return ChanSignal(
        signal_type=signal_type,
        divergence_kind=kind,
        signal_class=None,
        strength=None,
        segment_index=current_index,
        bar_index=current.end.bar_index,
        time=current.end.time,
        price_i64=price,
        reference_object_id=reference_object_id,
        macd_area_reference=reference_area,
        macd_area_current=current_area,
        known_at_bar_index=known_at_bar_index,
        comparison_reference_object_id=reference.object_id,
        comparison_current_object_id=current.object_id,
        comparison_rule=(
            "macd_same_direction_area_contraction_with_new_extreme"
            if require_new_extreme
            else "macd_same_direction_area_contraction"
        ),
        new_extreme_satisfied=True if require_new_extreme else None,
        follow_through_object_id=follow_through_object_id,
        follow_through_status=(
            "observed" if follow_through_object_id is not None else "not_applicable"
        ),
    )


def chan_divergences(
    segments: Sequence[LineLike],
    centers: list[ReferenceCenter],
    center_ids: list[str],
    histogram: Mapping[int, float],
    area_cache: MutableMapping[MacdAreaKey, float] | None = None,
) -> list[ChanSignal]:
    """识别已确认的线段级趋势背驰和盘整背驰。

    中枢真正的离开段是 `exit_index`。`end_index + 1` 只是奇偶交错序列中的
    相邻段，不能拿来做 MACD 力度比较。
    """
    result: list[ChanSignal] = []
    seen: set[tuple[DivergenceKind, int]] = set()

    # Active-center oscillation: compare each completed component with the
    # immediately preceding component in the same direction and center.  This
    # is the confirmed lower-level exhaustion consumed by ALG-STR-004; Zn is
    # deliberately absent from the structural decision.
    for center_position, (center, center_id) in enumerate(zip(centers, center_ids, strict=True)):
        previous_by_direction: dict[str, int] = {}
        for current_index in range(center.base_index, center.end_index + 1):
            current = segments[current_index]
            reference_index = previous_by_direction.get(current.direction)
            previous_by_direction[current.direction] = current_index
            if reference_index is None:
                continue
            value = _divergence(
                "consolidation",
                segments[reference_index],
                current,
                current_index,
                center_id,
                _center_component_known_at(center, segments, current_index),
                histogram,
                area_cache,
                require_new_extreme=False,
            )
            if value is not None:
                value = replace(
                    value,
                    reference_center_ordinal=center_position + 1,
                    older_center_count=center_position,
                    center_chain_profile="confirmed_same_level_centers_known_at_signal_v1",
                )
                result.append(value)
                seen.add(("consolidation", current_index))

    # One center: a + Z + c.  A completed counter leg after c confirms c's endpoint.
    for center_position, (center, center_id) in enumerate(zip(centers, center_ids, strict=True)):
        if center.status != "left" or center.exit_index is None or center.base_index < 1:
            continue
        current_index = center.exit_index
        confirmation_index = current_index + 1
        if confirmation_index >= len(segments):
            continue
        reference_index = _previous_same_direction(
            segments, center.base_index, segments[current_index].direction
        )
        if reference_index is None:
            continue
        value = _divergence(
            "consolidation",
            segments[reference_index],
            segments[current_index],
            current_index,
            center_id,
            segments[confirmation_index].known_at_bar_index,
            histogram,
            area_cache,
            require_new_extreme=False,
            follow_through_object_id=segments[confirmation_index].object_id,
        )
        if value is not None:
            value = replace(
                value,
                reference_center_ordinal=center_position + 1,
                older_center_count=center_position,
                center_chain_profile="confirmed_same_level_centers_known_at_signal_v1",
            )
            result.append(value)
            seen.add(("consolidation", current_index))

    # Trend: b + Z2 + c.  Centers must be separate in both time and their full
    # DD/GG outer ranges; strength is b versus c, not a versus c.
    for index in range(1, len(centers)):
        first = centers[index - 1]
        second = centers[index]
        if first.exit_index is None or second.exit_index is None:
            continue
        if first.end_index >= second.base_index or second.exit_index + 1 >= len(segments):
            continue
        first_dd, first_gg = _outer_range(first, segments)
        second_dd, second_gg = _outer_range(second, segments)
        if second_dd > first_gg:
            direction = "up"
        elif second_gg < first_dd:
            direction = "down"
        else:
            continue
        reference_index = first.exit_index
        current_index = second.exit_index
        if (
            segments[reference_index].direction != direction
            or segments[current_index].direction != direction
            or second.leave_direction != direction
        ):
            continue
        value = _divergence(
            "trend",
            segments[reference_index],
            segments[current_index],
            current_index,
            center_ids[index],
            segments[current_index + 1].known_at_bar_index,
            histogram,
            area_cache,
            follow_through_object_id=segments[current_index + 1].object_id,
        )
        if value is not None and ("trend", current_index) not in seen:
            value = replace(
                value,
                reference_center_ordinal=index + 1,
                older_center_count=index,
                center_chain_profile="confirmed_same_level_centers_known_at_signal_v1",
            )
            result.append(value)
            seen.add(("trend", current_index))
    trend_segments = {value.segment_index for value in result if value.divergence_kind == "trend"}
    return [
        value
        for value in result
        if value.divergence_kind == "trend" or value.segment_index not in trend_segments
    ]


def chan_first_point_candidates(
    segments: Sequence[LineLike],
    centers: list[ReferenceCenter],
    center_ids: list[str],
    histogram: Mapping[int, float],
    area_cache: MutableMapping[MacdAreaKey, float] | None = None,
    *,
    level_id: str = "L0",
) -> list[ChanSignal]:
    """Publish B1/S1 as soon as the fixed trend leg is complete.

    Structure and force are fixed at the completed exit leg ``c``.  The point
    remains a candidate until the following opposite segment confirms the
    lower-level turn.  At that point this function returns the same semantic
    point as confirmed; callers merge revisions by a stable level-and-leg ID.
    """
    result: list[ChanSignal] = []
    for index in range(1, len(centers)):
        first = centers[index - 1]
        second = centers[index]
        if first.exit_index is None or second.exit_index is None:
            continue
        if first.end_index >= second.base_index:
            continue
        first_dd, first_gg = _outer_range(first, segments)
        second_dd, second_gg = _outer_range(second, segments)
        if second_dd > first_gg:
            direction = "up"
        elif second_gg < first_dd:
            direction = "down"
        else:
            continue
        reference_index = first.exit_index
        current_index = second.exit_index
        reference = segments[reference_index]
        current = segments[current_index]
        if (
            reference.direction != direction
            or current.direction != direction
            or second.leave_direction != direction
        ):
            continue
        divergence = _divergence(
            "trend",
            reference,
            current,
            current_index,
            center_ids[index],
            current.known_at_bar_index,
            histogram,
            area_cache,
        )
        if divergence is None:
            continue
        buy_side = direction == "down"
        turn_index = current_index + 1
        turn = segments[turn_index] if turn_index < len(segments) else None
        confirmed = turn is not None and turn.direction != current.direction
        result.append(
            ChanSignal(
                signal_type="buy_1" if buy_side else "sell_1",
                divergence_kind=None,
                signal_class="standard",
                strength=None,
                segment_index=current_index,
                bar_index=divergence.bar_index,
                time=divergence.time,
                price_i64=divergence.price_i64,
                reference_object_id=center_ids[index],
                macd_area_reference=None,
                macd_area_current=None,
                known_at_bar_index=(
                    turn.known_at_bar_index
                    if confirmed and turn is not None
                    else current.known_at_bar_index
                ),
                status="confirmed" if confirmed else "candidate",
                level_id=level_id,
                lower_level_turn_object_id=(
                    turn.object_id if confirmed and turn is not None else None
                ),
                catalog_event=("B1_confirmed" if buy_side else "S1_confirmed")
                if confirmed
                else ("B1_candidate" if buy_side else "S1_candidate"),
                catalog_algorithm_id="ALG-SIG-001",
                comparison_reference_object_id=divergence.comparison_reference_object_id,
                comparison_current_object_id=divergence.comparison_current_object_id,
                comparison_rule=divergence.comparison_rule,
                new_extreme_satisfied=divergence.new_extreme_satisfied,
                follow_through_object_id=turn.object_id if confirmed and turn is not None else None,
                follow_through_status="observed" if confirmed else "pending",
                reference_center_ordinal=index + 1,
                older_center_count=index,
                center_chain_profile="confirmed_same_level_centers_known_at_signal_v1",
            )
        )
    return result


def _point_from_divergence(
    divergence_id: str, divergence: ChanSignal, signal_type: SignalType, signal_class: SignalClass
) -> ChanSignal:
    """把背驰端点转换成一类买卖点端点。"""
    standard_first = signal_class == "standard" and signal_type in {"buy_1", "sell_1"}
    return ChanSignal(
        signal_type=signal_type,
        divergence_kind=None,
        signal_class=signal_class,
        strength=None,
        segment_index=divergence.segment_index,
        bar_index=divergence.bar_index,
        time=divergence.time,
        price_i64=divergence.price_i64,
        reference_object_id=divergence_id,
        macd_area_reference=None,
        macd_area_current=None,
        known_at_bar_index=divergence.known_at_bar_index,
        catalog_event=("B1_confirmed" if signal_type == "buy_1" else "S1_confirmed")
        if standard_first
        else None,
        catalog_algorithm_id="ALG-SIG-001" if standard_first else None,
        comparison_reference_object_id=divergence.comparison_reference_object_id,
        comparison_current_object_id=divergence.comparison_current_object_id,
        comparison_rule=divergence.comparison_rule,
        new_extreme_satisfied=divergence.new_extreme_satisfied,
        follow_through_object_id=divergence.follow_through_object_id,
        follow_through_status=divergence.follow_through_status,
        reference_center_ordinal=divergence.reference_center_ordinal,
        older_center_count=divergence.older_center_count,
        center_chain_profile=divergence.center_chain_profile,
    )


def _third_points(
    segments: Sequence[LineLike], centers: list[ReferenceCenter], center_ids: list[str]
) -> list[ChanSignal]:
    """扫描严格三买/三卖。

    三买要求向上离开中枢后的第一次已完成向下回试，其低点不低于 `ZG`；
    三卖是镜像规则，回试高点不高于 `ZD`。边界接触按闭区间确认三类点。
    """
    result: list[ChanSignal] = []
    for center_position, (center, center_id) in enumerate(zip(centers, center_ids, strict=True)):
        if center.status != "left" or center.exit_index is None:
            continue
        leave_index = center.exit_index
        return_index = leave_index + 1
        if return_index >= len(segments):
            continue
        leaving = segments[leave_index]
        returning = segments[return_index]
        outer_low, outer_high = _outer_range(center, segments)
        if center.leave_direction == "up":
            if (
                leaving.direction != "up"
                or returning.direction != "down"
                or _low(returning) < center.zg_i64
            ):
                continue
            signal_type: SignalType = "buy_3"
            price = _low(returning)
            relation = (
                "outside_outer"
                if price > outer_high
                else "touch_outer"
                if price == outer_high
                else "outside_core"
                if price > center.zg_i64
                else "touch_core"
            )
            depth_to_core = price - center.zg_i64
            depth_to_outer = price - outer_high
        else:
            if (
                leaving.direction != "down"
                or returning.direction != "up"
                or _high(returning) > center.zd_i64
            ):
                continue
            signal_type = "sell_3"
            price = _high(returning)
            relation = (
                "outside_outer"
                if price < outer_low
                else "touch_outer"
                if price == outer_low
                else "outside_core"
                if price < center.zd_i64
                else "touch_core"
            )
            depth_to_core = center.zd_i64 - price
            depth_to_outer = outer_low - price
        follow_index = return_index + 1
        follow = segments[follow_index] if follow_index < len(segments) else None
        result.append(
            ChanSignal(
                signal_type=signal_type,
                divergence_kind=None,
                signal_class="standard",
                strength=None,
                segment_index=return_index,
                bar_index=returning.end.bar_index,
                time=returning.end.time,
                price_i64=price,
                reference_object_id=center_id,
                macd_area_reference=None,
                macd_area_current=None,
                known_at_bar_index=returning.known_at_bar_index,
                departure_object_id=leaving.object_id,
                return_object_id=returning.object_id,
                return_ordinal=1,
                boundary_profile="lesson20_inclusive_v1",
                boundary_relation=relation,
                return_depth_to_core_i64=depth_to_core,
                return_depth_to_outer_i64=depth_to_outer,
                follow_through_object_id=follow.object_id if follow is not None else None,
                follow_through_status="observed" if follow is not None else "pending",
                reference_center_ordinal=center_position + 1,
                older_center_count=center_position,
                center_chain_profile="confirmed_same_level_centers_known_at_signal_v1",
            )
        )
    return result


def chan_trade_points(
    segments: Sequence[LineLike],
    centers: list[ReferenceCenter],
    center_ids: list[str],
    divergences: list[tuple[str, ChanSignal]],
) -> list[ChanSignal]:
    """生成标准和项目定义的类一/二/三买卖点。

    类信号链以盘整背驰为起点；若后续严格三类点之前没有同向标准一类点取代
    这个起点，则额外暴露类三生命周期标记。
    """
    result: list[ChanSignal] = []
    thirds = _third_points(segments, centers, center_ids)
    consolidation_by_segment = {
        signal.segment_index: signal
        for _, signal in divergences
        if signal.divergence_kind == "consolidation"
    }
    origins: list[ChanSignal] = []
    ordered_divergences = sorted(
        divergences,
        key=lambda item: (item[1].bar_index, item[1].known_at_bar_index, item[0]),
    )

    for divergence_id, divergence in ordered_divergences:
        buy_side = divergence.signal_type == "bottom_divergence"
        if divergence.divergence_kind == "trend":
            first_type: SignalType = "buy_1" if buy_side else "sell_1"
            signal_class: SignalClass = "standard"
        else:
            first_type = "class_buy_1" if buy_side else "class_sell_1"
            signal_class = "class_like"
        first = _point_from_divergence(divergence_id, divergence, first_type, signal_class)
        if signal_class == "standard" and divergence.segment_index + 1 < len(segments):
            first = replace(
                first,
                lower_level_turn_object_id=segments[divergence.segment_index + 1].object_id,
            )
        result.append(first)
        origins.append(first)

        # First completed counter-trend and return after the first point.  A
        # confirmed segment already carries the later bar that made its endpoint known.
        second_index = divergence.segment_index + 2
        if second_index >= len(segments):
            continue
        second = segments[second_index]
        expected = "down" if buy_side else "up"
        if second.direction != expected:
            continue
        second_price = _low(second) if buy_side else _high(second)
        overlaps_third = any(
            point.signal_type == ("buy_3" if buy_side else "sell_3")
            and point.bar_index == second.end.bar_index
            and point.price_i64 == second_price
            for point in thirds
        )
        if overlaps_third:
            strength: SignalStrength = "strongest"
        elif (buy_side and second_price >= first.price_i64) or (
            not buy_side and second_price <= first.price_i64
        ):
            strength = "normal"
        elif second_index in consolidation_by_segment:
            strength = "weakest"
        else:
            # A new extreme without its own consolidation-divergence ending
            # confirmation is not a valid weak second point.
            continue
        second_type: SignalType
        if signal_class == "standard":
            second_type = "buy_2" if buy_side else "sell_2"
        else:
            second_type = "class_buy_2" if buy_side else "class_sell_2"
        result.append(
            ChanSignal(
                signal_type=second_type,
                divergence_kind=None,
                signal_class=signal_class,
                strength=strength,
                segment_index=second_index,
                bar_index=second.end.bar_index,
                time=second.end.time,
                price_i64=second_price,
                reference_object_id=divergence_id,
                macd_area_reference=None,
                macd_area_current=None,
                known_at_bar_index=second.known_at_bar_index,
                comparison_reference_object_id=divergence_id,
                comparison_current_object_id=second.object_id,
                comparison_rule=f"second_point_{strength}",
                follow_through_status="pending",
                reference_center_ordinal=divergence.reference_center_ordinal,
                older_center_count=divergence.older_center_count,
                center_chain_profile=divergence.center_chain_profile,
            )
        )

    result.extend(thirds)

    # Preserve the strict third-point label and additionally expose its
    # class-like lifecycle when the most recent same-side origin was a class 1.
    for third in thirds:
        buy_side = third.signal_type == "buy_3"
        prior = [
            point
            for point in origins
            if point.bar_index < third.bar_index
            and (
                (buy_side and "buy" in point.signal_type)
                or (not buy_side and "sell" in point.signal_type)
            )
        ]
        if not prior or prior[-1].signal_class != "class_like":
            continue
        result.append(
            replace(
                third,
                signal_type="class_buy_3" if buy_side else "class_sell_3",
                signal_class="class_like",
                reference_object_id=prior[-1].reference_object_id,
            )
        )
    return result
