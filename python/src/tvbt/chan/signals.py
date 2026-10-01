from __future__ import annotations

from collections.abc import Callable, Mapping, MutableMapping, Sequence
from dataclasses import dataclass, replace
from typing import Literal

from tvbt.chan.reference import LineLike

"""线段级缠论信号生成。

本文件消费 `engine.py` 生成的已确认线段和 `reference.py` 生成的标准线段
中枢，只负责语义信号，不负责订单、成交或收益计算。

信号分两层：

- 背驰：趋势背驰和盘整背驰，使用结构关系加 MACD 同方向柱面积比较。
- 买卖点：标准一二三类点，以及项目显式定义的“类一/类二/类三”生命周期点。
"""

# 背驰类别。trend 对应 a+Z1+b+Z2+c，consolidation 对应 a+Z+c。
DivergenceKind = Literal["trend", "consolidation", "center_oscillation"]
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
MacdExtremeRelation = Literal[
    "both_weaker", "diff_only", "dea_only", "neither_weaker", "unavailable"
]


@dataclass(frozen=True)
class DivergenceContext:
    """A structural comparison opportunity, independent of its strength model."""

    kind: DivergenceKind
    reference: LineLike
    current: LineLike
    current_index: int
    reference_object_id: str
    known_at_bar_index: int
    require_new_extreme: bool
    follow_through_object_id: str | None


@dataclass(frozen=True)
class LegacyStrengthEvidence:
    """The exact 19.3.2 MACD-area judgment; never a trade signal itself."""

    relation: Literal["weaker", "not_weaker", "unknown"]
    reason: str | None
    reference_area: float | None
    current_area: float | None
    diff_reference: float | None
    diff_current: float | None
    dea_reference: float | None
    dea_current: float | None
    extreme_relation: MacdExtremeRelation


PriceTimeRelation = Literal["weaker", "stronger", "equal", "conflict", "unknown"]


@dataclass(frozen=True)
class PriceTimeStrengthEvidence:
    """Indicator-free, integer-exact movement comparison in observed-bar units.

    ``baseline_span_below_80pct`` is a separately visible decision override,
    not a modification of the displacement/speed relation.
    """

    profile: Literal["price_displacement_speed_v1"]
    relation: PriceTimeRelation
    reason: str | None
    reference_displacement_i64: int | None
    current_displacement_i64: int | None
    reference_intervals: int | None
    current_intervals: int | None
    reference_baseline_span_i64: int | None
    current_baseline_span_i64: int | None
    baseline_span_below_80pct: bool | None
    effective_weaker: bool


@dataclass(frozen=True)
class ComparisonAudit:
    """Research-only comparison; not a ChanSignal and never a trade origin."""

    kind: DivergenceKind
    reference_object_id: str
    current_object_id: str
    center_object_id: str
    current_end_bar_index: int
    known_at_bar_index: int
    strength_relation: Literal["weaker", "not_weaker", "unknown"]
    reference_area: float | None
    current_area: float | None
    price_span_passed: bool | None
    new_extreme_satisfied: bool
    accepted_by_legacy: bool
    reason: str | None
    price_time: PriceTimeStrengthEvidence | None = None


@dataclass(frozen=True)
class StructuralCenter:
    """Signal-facing projection of the authoritative local-center decomposition."""

    base_index: int
    seed_end_index: int
    end_index: int
    exit_index: int | None
    start_bar_index: int
    end_bar_index: int
    start_time: int
    end_time: int
    zd_i64: int
    zg_i64: int
    known_at_bar_index: int
    status: Literal["confirmed", "extended", "left"]
    leave_direction: Literal["up", "down"] | None
    formation_dir: Literal["UP", "DOWN"] | None = None
    relative_dir: Literal["UP", "DOWN", "OVERLAP", "UNKNOWN"] = "UNKNOWN"
    previous_center_id: str | None = None
    comparison_dd_i64: int | None = None
    comparison_gg_i64: int | None = None
    comparison_excluded_entry_id: str | None = None


@dataclass(frozen=True)
class BiCenterEvidence:
    """A closed BI center wholly available at its break confirmation."""

    object_id: str
    start_bar_index: int
    end_bar_index: int
    known_at_bar_index: int


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
    status: Literal["forming", "candidate", "confirmed", "invalidated"] = "confirmed"
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
    strength_profile: str | None = None
    strength_relation: PriceTimeRelation | None = None
    strength_trigger: str | None = None
    price_displacement_reference_i64: int | None = None
    price_displacement_current_i64: int | None = None
    observed_intervals_reference: int | None = None
    observed_intervals_current: int | None = None
    baseline_span_reference_i64: int | None = None
    baseline_span_current_i64: int | None = None
    baseline_span_below_80pct: bool | None = None
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
    divergence_profile: (
        Literal["segment_trend_candidate", "standard_trend", "external_range", "center_oscillation"]
        | None
    ) = None
    formation_dir: Literal["UP", "DOWN"] | None = None
    relative_dir: Literal["UP", "DOWN", "OVERLAP", "UNKNOWN"] | None = None
    a_object_id: str | None = None
    b_object_id: str | None = None
    a_center_id: str | None = None
    b_center_id: str | None = None
    macd_area_ratio: float | None = None
    macd_diff_reference_extreme: float | None = None
    macd_diff_current_extreme: float | None = None
    macd_dea_reference_extreme: float | None = None
    macd_dea_current_extreme: float | None = None
    macd_extreme_relation: MacdExtremeRelation | None = None
    macd_parameter_profile: str | None = None
    c_contains_type3: bool | None = None
    c_meets_sublevel: bool | None = None
    c_sublevel_profile: str | None = None
    c_sublevel_center_ids: tuple[str, ...] = ()
    c_type3_departure_id: str | None = None
    c_type3_retest_id: str | None = None
    c_proof_known_at_bar_index: int | None = None

    @property
    def confirmation_latency_bars(self) -> int:
        return self.known_at_bar_index - self.bar_index


def _low(line: LineLike) -> int:
    value: int | None = getattr(line, "range_low_i64", None)
    return value if value is not None else min(line.start.price_i64, line.end.price_i64)


def _high(line: LineLike) -> int:
    value: int | None = getattr(line, "range_high_i64", None)
    return value if value is not None else max(line.start.price_i64, line.end.price_i64)


def _body_range(center: StructuralCenter, segments: Sequence[LineLike]) -> tuple[int, int]:
    """Return the complete center body used for local boundary descriptions."""
    components = segments[center.base_index : center.end_index + 1]
    return min(_low(line) for line in components), max(_high(line) for line in components)


def _outer_range(center: StructuralCenter, segments: Sequence[LineLike]) -> tuple[int, int]:
    """Return the center-migration envelope, excluding a shared SEGMENT entry."""
    if center.comparison_dd_i64 is not None and center.comparison_gg_i64 is not None:
        return center.comparison_dd_i64, center.comparison_gg_i64
    return _body_range(center, segments)


def _contiguous_preceding_same_direction(
    segments: Sequence[LineLike], before_index: int, direction: str
) -> int | None:
    """Return an outward a leg only if it directly precedes the center body.

    Skipping an opposite leg would splice an unproved movement into a+B+c.
    """
    index = before_index - 1
    return index if index >= 0 and segments[index].direction == direction else None


def _trend_legs_advance(a: LineLike, b: LineLike, c: LineLike, direction: str) -> bool:
    """Require the three outward legs to advance from their own start prices.

    Strictly separated centers and a new extreme at c's end do not prove this:
    the intervening retracement can take b's start below a's start (or above
    it in a downtrend). Such a chain is not the directed a+A+b+B+c pattern.
    """
    if any(line.direction != direction for line in (a, b, c)):
        return False
    starts = (a.start.price_i64, b.start.price_i64, c.start.price_i64)
    return (
        starts[0] < starts[1] < starts[2]
        if direction == "up"
        else starts[0] > starts[1] > starts[2]
    )


def _center_baseline_span_contracts(
    reference: LineLike,
    current: LineLike,
    direction: str,
    reference_baseline_i64: int,
    current_baseline_i64: int,
) -> bool:
    """Conservatively compare outward price travel from equivalent baselines.

    This is an additional project gate alongside MACD area contraction, not
    a claim that lesson 24 defines a mandatory price-distance formula.
    """
    if reference.direction != direction or current.direction != direction:
        return False
    if direction == "up":
        reference_span = _high(reference) - reference_baseline_i64
        current_span = _high(current) - current_baseline_i64
    else:
        reference_span = reference_baseline_i64 - _low(reference)
        current_span = current_baseline_i64 - _low(current)
    return reference_span > 0 and 0 < current_span < reference_span


def _center_component_known_at(
    center: StructuralCenter,
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
) -> float | None:
    """计算线段同方向 MACD 柱面积。

    向上线段只累计正柱；向下线段只累计负柱绝对值。MACD 只用于结构成立后的
    力度比较，不能单独生成缠论信号。
    """
    key = (line.object_id, line.start.bar_index, line.end.bar_index, line.direction)
    if cache is not None and key in cache:
        return cache[key]
    indexes = range(line.start.bar_index, line.end.bar_index + 1)
    if any(index not in histogram for index in indexes):
        return None
    values = (histogram[index] for index in indexes)
    if line.direction == "up":
        result = sum(max(value, 0.0) for value in values)
    else:
        result = sum(abs(min(value, 0.0)) for value in values)
    if cache is not None:
        cache[key] = result
    return result


def _directional_extreme(line: LineLike, values: Mapping[int, float] | None) -> float | None:
    if values is None:
        return None
    indexes = range(line.start.bar_index, line.end.bar_index + 1)
    if any(index not in values for index in indexes):
        return None
    samples = (values[index] for index in indexes)
    return max(samples) if line.direction == "up" else min(samples)


def _macd_extreme_relation(
    direction: str,
    diff_reference: float | None,
    diff_current: float | None,
    dea_reference: float | None,
    dea_current: float | None,
) -> MacdExtremeRelation:
    """Classify same-direction extrema as audit evidence, not a trading gate."""
    if any(value is None for value in (diff_reference, diff_current, dea_reference, dea_current)):
        return "unavailable"
    assert diff_reference is not None and diff_current is not None
    assert dea_reference is not None and dea_current is not None
    diff_weaker = (
        diff_current < diff_reference if direction == "up" else diff_current > diff_reference
    )
    dea_weaker = dea_current < dea_reference if direction == "up" else dea_current > dea_reference
    if diff_weaker and dea_weaker:
        return "both_weaker"
    if diff_weaker:
        return "diff_only"
    if dea_weaker:
        return "dea_only"
    return "neither_weaker"


def compare_legacy_strength(
    context: DivergenceContext,
    histogram: Mapping[int, float],
    area_cache: MutableMapping[MacdAreaKey, float] | None = None,
    *,
    diff: Mapping[int, float] | None = None,
    dea: Mapping[int, float] | None = None,
) -> LegacyStrengthEvidence:
    """Measure legacy MACD strength without creating a divergence or trade point."""
    reference, current = context.reference, context.current
    if reference.direction != current.direction:
        return LegacyStrengthEvidence(
            "unknown", "direction_mismatch", None, None, None, None, None, None, "unavailable"
        )
    reference_area = _macd_area(reference, histogram, area_cache)
    current_area = _macd_area(current, histogram, area_cache)
    diff_reference = _directional_extreme(reference, diff)
    diff_current = _directional_extreme(current, diff)
    dea_reference = _directional_extreme(reference, dea)
    dea_current = _directional_extreme(current, dea)
    extreme_relation = _macd_extreme_relation(
        current.direction, diff_reference, diff_current, dea_reference, dea_current
    )
    if reference_area is None or current_area is None:
        relation: Literal["weaker", "not_weaker", "unknown"] = "unknown"
        reason = "missing_macd_histogram"
    elif reference_area <= 0.0 or current_area <= 0.0:
        relation = "unknown"
        reason = "nonpositive_macd_area"
    elif current_area >= reference_area:
        relation = "not_weaker"
        reason = "macd_area_not_contracting"
    else:
        relation = "weaker"
        reason = None
    return LegacyStrengthEvidence(
        relation,
        reason,
        reference_area,
        current_area,
        diff_reference,
        diff_current,
        dea_reference,
        dea_current,
        extreme_relation,
    )


def compare_price_time_strength(
    context: DivergenceContext,
    raw_positions: Mapping[int, int],
    *,
    reference_baseline_i64: int | None = None,
    current_baseline_i64: int | None = None,
    certified_complete_positions: bool = False,
) -> PriceTimeStrengthEvidence:
    """Compare endpoint displacement and speed over actual observed K intervals.

    ``raw_positions`` must be built from the complete, ordered raw-bar stream
    available at ``context.known_at_bar_index``. Missing endpoint positions,
    incomplete movements, or non-positive measurements stay unknown. The
    optional 80% override uses actual-range-to-center-baseline spans, which
    are deliberately distinct from endpoint displacement.
    """

    reference, current = context.reference, context.current
    empty = (None, None, None, None, None, None, None)

    def unknown(reason: str) -> PriceTimeStrengthEvidence:
        return PriceTimeStrengthEvidence(
            "price_displacement_speed_v1", "unknown", reason, *empty, False
        )

    if reference.direction != current.direction or current.direction not in {"up", "down"}:
        return unknown("direction_mismatch")
    if (
        reference.start.bar_index >= reference.end.bar_index
        or current.start.bar_index >= current.end.bar_index
        or reference.known_at_bar_index > context.known_at_bar_index
        or current.known_at_bar_index > context.known_at_bar_index
        or current.end.bar_index > context.known_at_bar_index
    ):
        return unknown("incomplete_movement")
    anchors = (
        reference.start.bar_index,
        reference.end.bar_index,
        current.start.bar_index,
        current.end.bar_index,
    )
    if any(anchor not in raw_positions for anchor in anchors):
        return unknown("missing_raw_position")
    observed_positions = None if certified_complete_positions else set(raw_positions.values())
    reference_intervals = (
        raw_positions[reference.end.bar_index] - raw_positions[reference.start.bar_index]
    )
    current_intervals = (
        raw_positions[current.end.bar_index] - raw_positions[current.start.bar_index]
    )
    if observed_positions is not None and any(
        position not in observed_positions
        for start, end in (
            (raw_positions[reference.start.bar_index], raw_positions[reference.end.bar_index]),
            (raw_positions[current.start.bar_index], raw_positions[current.end.bar_index]),
        )
        for position in range(start, end + 1)
    ):
        return unknown("incomplete_raw_window")
    sign = 1 if current.direction == "up" else -1
    reference_displacement = sign * (reference.end.price_i64 - reference.start.price_i64)
    current_displacement = sign * (current.end.price_i64 - current.start.price_i64)
    if (
        reference_intervals <= 0
        or current_intervals <= 0
        or reference_displacement <= 0
        or current_displacement <= 0
    ):
        return PriceTimeStrengthEvidence(
            "price_displacement_speed_v1",
            "unknown",
            "nonpositive_measurement",
            reference_displacement,
            current_displacement,
            reference_intervals,
            current_intervals,
            None,
            None,
            None,
            False,
        )

    displacement_cmp = (current_displacement > reference_displacement) - (
        current_displacement < reference_displacement
    )
    current_speed_numerator = current_displacement * reference_intervals
    reference_speed_numerator = reference_displacement * current_intervals
    speed_cmp = (current_speed_numerator > reference_speed_numerator) - (
        current_speed_numerator < reference_speed_numerator
    )
    if displacement_cmp <= 0 and speed_cmp <= 0 and (displacement_cmp or speed_cmp):
        relation: PriceTimeRelation = "weaker"
    elif displacement_cmp >= 0 and speed_cmp >= 0 and (displacement_cmp or speed_cmp):
        relation = "stronger"
    elif displacement_cmp == speed_cmp == 0:
        relation = "equal"
    else:
        relation = "conflict"

    reference_span: int | None = None
    current_span: int | None = None
    below_80pct: bool | None = None
    if reference_baseline_i64 is not None and current_baseline_i64 is not None:
        if current.direction == "up":
            reference_span = _high(reference) - reference_baseline_i64
            current_span = _high(current) - current_baseline_i64
        else:
            reference_span = reference_baseline_i64 - _low(reference)
            current_span = current_baseline_i64 - _low(current)
        if reference_span > 0 and current_span > 0:
            below_80pct = 5 * current_span < 4 * reference_span
    return PriceTimeStrengthEvidence(
        "price_displacement_speed_v1",
        relation,
        None,
        reference_displacement,
        current_displacement,
        reference_intervals,
        current_intervals,
        reference_span,
        current_span,
        below_80pct,
        relation == "weaker" or below_80pct is True,
    )


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
    diff: Mapping[int, float] | None = None,
    dea: Mapping[int, float] | None = None,
    reference_baseline_i64: int | None = None,
    current_baseline_i64: int | None = None,
    legacy_new_extreme_satisfied: bool | None = None,
    comparison_audit: list[ComparisonAudit] | None = None,
    raw_positions: Mapping[int, int] | None = None,
    certified_complete_positions: bool = False,
    strength_profile: Literal["legacy_macd_area_v1", "price_displacement_speed_v1"] = (
        "legacy_macd_area_v1"
    ),
) -> ChanSignal | None:
    """比较参考段和当前段，若当前段力度收缩则生成背驰。"""
    context = DivergenceContext(
        kind,
        reference,
        current,
        current_index,
        reference_object_id,
        known_at_bar_index,
        require_new_extreme,
        follow_through_object_id,
    )
    strength = compare_legacy_strength(context, histogram, area_cache, diff=diff, dea=dea)
    price_time = (
        compare_price_time_strength(
            context,
            raw_positions,
            reference_baseline_i64=reference_baseline_i64,
            current_baseline_i64=current_baseline_i64,
            certified_complete_positions=certified_complete_positions,
        )
        if raw_positions is not None
        and (comparison_audit is not None or strength_profile == "price_displacement_speed_v1")
        else None
    )
    price_span_passed = (
        _center_baseline_span_contracts(
            reference,
            current,
            current.direction,
            reference_baseline_i64,
            current_baseline_i64,
        )
        if reference_baseline_i64 is not None and current_baseline_i64 is not None
        else None
    )
    new_extreme = (
        _high(current) > _high(reference)
        if current.direction == "up"
        else _low(current) < _low(reference)
    )
    structural_extreme_passed = (new_extreme or not require_new_extreme) and (
        legacy_new_extreme_satisfied is not False
    )
    legacy_accepted = (
        strength.relation == "weaker"
        and price_span_passed is not False
        and structural_extreme_passed
    )
    accepted = (
        price_time is not None and price_time.effective_weaker and structural_extreme_passed
        if strength_profile == "price_displacement_speed_v1"
        else legacy_accepted
    )
    if comparison_audit is not None:
        comparison_audit.append(
            ComparisonAudit(
                kind,
                reference.object_id,
                current.object_id,
                reference_object_id,
                current.end.bar_index,
                known_at_bar_index,
                strength.relation,
                strength.reference_area,
                strength.current_area,
                price_span_passed,
                new_extreme
                if legacy_new_extreme_satisfied is None
                else legacy_new_extreme_satisfied,
                legacy_accepted,
                "center_baseline_span_not_contracting"
                if price_span_passed is False
                else strength.reason
                if strength.relation != "weaker"
                else "new_extreme_missing"
                if (require_new_extreme and not new_extreme)
                or legacy_new_extreme_satisfied is False
                else None,
                price_time,
            )
        )
    if not accepted:
        return None
    emitted_price_time = price_time if strength_profile == "price_displacement_speed_v1" else None
    reference_area = strength.reference_area
    current_area = strength.current_area
    if current.direction == "up":
        signal_type: SignalType = "top_divergence"
        price = _high(current)
    else:
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
        macd_area_ratio=(
            current_area / reference_area
            if reference_area is not None and reference_area > 0 and current_area is not None
            else None
        ),
        macd_diff_reference_extreme=strength.diff_reference,
        macd_diff_current_extreme=strength.diff_current,
        macd_dea_reference_extreme=strength.dea_reference,
        macd_dea_current_extreme=strength.dea_current,
        macd_extreme_relation=strength.extreme_relation,
        macd_parameter_profile=(
            "macd_12_26_9_histogram_x2" if diff is not None and dea is not None else None
        ),
        known_at_bar_index=known_at_bar_index,
        comparison_reference_object_id=reference.object_id,
        comparison_current_object_id=current.object_id,
        comparison_rule=(
            "price_displacement_speed_or_baseline_span_below_80pct_v1"
            if strength_profile == "price_displacement_speed_v1"
            else "macd_same_direction_area_contraction_with_new_extreme"
            if require_new_extreme
            else "macd_same_direction_area_contraction"
        ),
        strength_profile=(
            strength_profile if strength_profile == "price_displacement_speed_v1" else None
        ),
        strength_relation=emitted_price_time.relation if emitted_price_time is not None else None,
        strength_trigger=(
            "price_time_joint_weakening"
            if emitted_price_time is not None and emitted_price_time.relation == "weaker"
            else "baseline_span_below_80pct"
            if emitted_price_time is not None and emitted_price_time.baseline_span_below_80pct
            else None
        ),
        price_displacement_reference_i64=(
            emitted_price_time.reference_displacement_i64
            if emitted_price_time is not None
            else None
        ),
        price_displacement_current_i64=(
            emitted_price_time.current_displacement_i64 if emitted_price_time is not None else None
        ),
        observed_intervals_reference=(
            emitted_price_time.reference_intervals if emitted_price_time is not None else None
        ),
        observed_intervals_current=(
            emitted_price_time.current_intervals if emitted_price_time is not None else None
        ),
        baseline_span_reference_i64=(
            emitted_price_time.reference_baseline_span_i64
            if emitted_price_time is not None
            else None
        ),
        baseline_span_current_i64=(
            emitted_price_time.current_baseline_span_i64 if emitted_price_time is not None else None
        ),
        baseline_span_below_80pct=(
            emitted_price_time.baseline_span_below_80pct if emitted_price_time is not None else None
        ),
        new_extreme_satisfied=new_extreme,
        follow_through_object_id=follow_through_object_id,
        follow_through_status=(
            "observed" if follow_through_object_id is not None else "not_applicable"
        ),
    )


def _trend_c_sublevel_proof(
    bi_lines: Sequence[LineLike],
    bi_centers: Sequence[BiCenterEvidence],
    *,
    start_bar_index: int,
    end_bar_index: int,
    known_at_bar_index: int,
    direction: str,
    boundary_i64: int,
) -> tuple[tuple[str, ...], str | None, str | None, int | None]:
    """Project lesson-37 sublevel evidence onto confirmed BI objects.

    This is an explicitly named engineering mapping, not an assertion that a
    BI center is identical to every original-text lower-level movement.
    """
    eligible = sorted(
        (
            center
            for center in bi_centers
            if start_bar_index <= center.start_bar_index
            and center.end_bar_index <= end_bar_index
            and center.known_at_bar_index <= known_at_bar_index
        ),
        key=lambda center: (center.start_bar_index, center.end_bar_index, center.object_id),
    )
    # Closed centers must be distinct and sequential; a shared boundary is legal.
    chain: list[BiCenterEvidence] = []
    for center in eligible:
        if not chain or center.start_bar_index >= chain[-1].end_bar_index:
            chain.append(center)
    selected = tuple(center.object_id for center in chain[:2])

    departure = None
    retest = None
    for line in bi_lines:
        if (
            line.start.bar_index < start_bar_index
            or line.end.bar_index > end_bar_index
            or line.known_at_bar_index > known_at_bar_index
        ):
            continue
        if departure is None:
            if line.direction != direction:
                continue
            outside = (
                line.start.price_i64 <= boundary_i64 < line.end.price_i64
                if direction == "up"
                else line.start.price_i64 >= boundary_i64 > line.end.price_i64
            )
            if outside:
                departure = line
            continue
        if line.start.bar_index != departure.end.bar_index:
            continue
        if line.direction == direction:
            continue
        holds = _low(line) >= boundary_i64 if direction == "up" else _high(line) <= boundary_i64
        if holds:
            retest = line
        break  # The first return cannot be replaced by a later successful one.
    proof_time = (
        max(
            [center.known_at_bar_index for center in chain[:2]]
            + ([retest.known_at_bar_index] if retest is not None else [])
        )
        if len(chain) >= 2 and retest is not None
        else None
    )
    return (
        selected,
        None if departure is None else departure.object_id,
        None if retest is None else retest.object_id,
        proof_time,
    )


def chan_divergences(
    segments: Sequence[LineLike],
    centers: list[StructuralCenter],
    center_ids: list[str],
    histogram: Mapping[int, float],
    area_cache: MutableMapping[MacdAreaKey, float] | None = None,
    *,
    diff: Mapping[int, float] | None = None,
    dea: Mapping[int, float] | None = None,
    bi_lines: Sequence[LineLike] = (),
    bi_centers: Sequence[BiCenterEvidence] | Callable[[], Sequence[BiCenterEvidence]] = (),
    comparison_audit: list[ComparisonAudit] | None = None,
    raw_positions: Mapping[int, int] | None = None,
    certified_complete_positions: bool = False,
    strength_profile: Literal["legacy_macd_area_v1", "price_displacement_speed_v1"] = (
        "legacy_macd_area_v1"
    ),
) -> list[ChanSignal]:
    """Classify segment-level trend, external range and center oscillation separately.

    A completed single segment ``c`` is only a *candidate* for original-text
    trend divergence: its internal third point and sublevel trend are not proven.
    All comparisons use confirmed same-direction segments and complete MACD bars.
    """
    result: list[ChanSignal] = []

    # Ai / Ai+2 belongs to the center's own oscillation, not external a+B+c.
    for center_position, (center, center_id) in enumerate(zip(centers, center_ids, strict=True)):
        previous_by_direction: dict[str, int] = {}
        for current_index in range(center.base_index, center.end_index + 1):
            current = segments[current_index]
            reference_index = previous_by_direction.get(current.direction)
            previous_by_direction[current.direction] = current_index
            if reference_index is None:
                continue
            value = _divergence(
                "center_oscillation",
                segments[reference_index],
                current,
                current_index,
                center_id,
                _center_component_known_at(center, segments, current_index),
                histogram,
                area_cache,
                require_new_extreme=False,
                diff=diff,
                dea=dea,
                comparison_audit=comparison_audit,
                raw_positions=raw_positions,
                certified_complete_positions=certified_complete_positions,
                strength_profile=strength_profile,
            )
            if value is not None:
                value = replace(
                    value,
                    divergence_profile="center_oscillation",
                    formation_dir=center.formation_dir,
                    relative_dir=center.relative_dir,
                    a_object_id=segments[reference_index].object_id,
                    b_center_id=center_id,
                    reference_center_ordinal=center_position + 1,
                    older_center_count=center_position,
                    center_chain_profile="confirmed_same_level_centers_known_at_signal_v1",
                )
                result.append(value)

    # Each local a+B+c can be monitored. Only the first center has a proven
    # one-center context here; with older centers, the movement boundary is
    # not established and the range result must stay a structure candidate.
    for center_position, center in enumerate(centers):
        if (
            center.status == "left"
            and center.exit_index is not None
            and center.base_index > 0
            # A third seed may itself confirm the break. It remains a body
            # component and cannot also be the external c of a+B+c.
            and center.exit_index > center.end_index
            and center.exit_index + 1 < len(segments)
        ):
            a_index = _contiguous_preceding_same_direction(
                segments, center.base_index, segments[center.exit_index].direction
            )
            c_index = center.exit_index
            if a_index is not None:
                value = _divergence(
                    "consolidation",
                    segments[a_index],
                    segments[c_index],
                    c_index,
                    center_ids[center_position],
                    segments[c_index + 1].known_at_bar_index,
                    histogram,
                    area_cache,
                    require_new_extreme=False,
                    follow_through_object_id=segments[c_index + 1].object_id,
                    diff=diff,
                    dea=dea,
                    reference_baseline_i64=segments[a_index].start.price_i64,
                    current_baseline_i64=(
                        center.zd_i64 if segments[c_index].direction == "up" else center.zg_i64
                    ),
                    comparison_audit=comparison_audit,
                    raw_positions=raw_positions,
                    certified_complete_positions=certified_complete_positions,
                    strength_profile=strength_profile,
                )
                if value is not None:
                    result.append(
                        replace(
                            value,
                            status="confirmed" if center_position == 0 else "candidate",
                            divergence_profile="external_range",
                            comparison_rule=(
                                "price_displacement_speed_or_baseline_span_below_80pct_v1"
                                if strength_profile == "price_displacement_speed_v1"
                                else "macd_area_and_center_baseline_price_span_contraction"
                            ),
                            formation_dir=center.formation_dir,
                            relative_dir=center.relative_dir,
                            a_object_id=segments[a_index].object_id,
                            b_center_id=center_ids[center_position],
                            reference_center_ordinal=center_position + 1,
                            older_center_count=center_position,
                            center_chain_profile="confirmed_same_level_centers_known_at_signal_v1",
                        )
                    )

    # a+A+b+B+c: compare b with c, but verify a exists and all three travel
    # in the center migration direction. A shared exit/B-first-seed is legal.
    for index in range(1, len(centers)):
        first = centers[index - 1]
        second = centers[index]
        if (
            first.status != "left"
            or second.status != "left"
            or first.exit_index is None
            or second.exit_index is None
            or first.base_index < 1
        ):
            continue
        if (
            first.end_index >= second.base_index
            or second.base_index not in {first.exit_index, first.exit_index + 1}
            or second.exit_index <= second.seed_end_index
            or second.exit_index + 1 >= len(segments)
        ):
            continue
        first_dd, first_gg = _outer_range(first, segments)
        second_dd, second_gg = _outer_range(second, segments)
        if second.zd_i64 > first.zg_i64 and second_dd > first_gg:
            direction = "up"
        elif second.zg_i64 < first.zd_i64 and second_gg < first_dd:
            direction = "down"
        else:
            continue
        if second.relative_dir != direction.upper():
            continue
        a_index = _contiguous_preceding_same_direction(segments, first.base_index, direction)
        if a_index is None:
            continue
        reference_index = first.exit_index
        current_index = second.exit_index
        if (
            segments[a_index].direction != direction
            or segments[a_index].known_at_bar_index > segments[reference_index].known_at_bar_index
            or segments[reference_index].direction != direction
            or segments[current_index].direction != direction
            or segments[reference_index].known_at_bar_index
            > segments[current_index].known_at_bar_index
            or second.leave_direction != direction
            or not _trend_legs_advance(
                segments[a_index], segments[reference_index], segments[current_index], direction
            )
        ):
            continue
        prior_extreme = (
            max(_high(line) for line in segments[a_index:current_index])
            if direction == "up"
            else min(_low(line) for line in segments[a_index:current_index])
        )
        new_extreme = (
            _high(segments[current_index]) > prior_extreme
            if direction == "up"
            else _low(segments[current_index]) < prior_extreme
        )
        value = _divergence(
            "trend",
            segments[reference_index],
            segments[current_index],
            current_index,
            center_ids[index],
            segments[current_index + 1].known_at_bar_index,
            histogram,
            area_cache,
            require_new_extreme=False,
            follow_through_object_id=segments[current_index + 1].object_id,
            diff=diff,
            dea=dea,
            reference_baseline_i64=first.zd_i64 if direction == "up" else first.zg_i64,
            current_baseline_i64=second.zd_i64 if direction == "up" else second.zg_i64,
            legacy_new_extreme_satisfied=new_extreme,
            comparison_audit=comparison_audit,
            raw_positions=raw_positions,
            certified_complete_positions=certified_complete_positions,
            strength_profile=strength_profile,
        )
        if value is not None:
            proof_centers, proof_departure, proof_retest, proof_time = _trend_c_sublevel_proof(
                bi_lines,
                bi_centers() if callable(bi_centers) else bi_centers,
                start_bar_index=segments[current_index].start.bar_index,
                end_bar_index=segments[current_index].end.bar_index,
                known_at_bar_index=value.known_at_bar_index,
                direction=direction,
                boundary_i64=second.zg_i64 if direction == "up" else second.zd_i64,
            )
            proof_checked = bool(bi_lines)
            standard = len(proof_centers) >= 2 and proof_retest is not None
            value = replace(
                value,
                status="confirmed" if standard else "candidate",
                divergence_profile="standard_trend" if standard else "segment_trend_candidate",
                formation_dir=second.formation_dir,
                relative_dir="UP" if direction == "up" else "DOWN",
                a_object_id=segments[a_index].object_id,
                b_object_id=segments[reference_index].object_id,
                a_center_id=center_ids[index - 1],
                b_center_id=center_ids[index],
                new_extreme_satisfied=True,
                comparison_rule=(
                    "price_displacement_speed_or_baseline_span_below_80pct_v1"
                    if strength_profile == "price_displacement_speed_v1"
                    else "macd_area_center_baseline_price_span_and_trend_new_extreme"
                ),
                c_contains_type3=proof_retest is not None if proof_checked else None,
                c_meets_sublevel=len(proof_centers) >= 2 if proof_checked else None,
                c_sublevel_profile="bi_two_confirmed_centers_type3_v1" if proof_checked else None,
                c_sublevel_center_ids=proof_centers,
                c_type3_departure_id=proof_departure,
                c_type3_retest_id=proof_retest,
                c_proof_known_at_bar_index=proof_time,
                reference_center_ordinal=index + 1,
                older_center_count=index,
                center_chain_profile="confirmed_same_level_centers_known_at_signal_v1",
            )
            result.append(value)
    trend_segments = {value.segment_index for value in result if value.divergence_kind == "trend"}
    return [
        value
        for value in result
        if value.divergence_kind != "consolidation" or value.segment_index not in trend_segments
    ]


def chan_forming_divergences(
    segments: Sequence[LineLike],
    centers: list[StructuralCenter],
    center_ids: list[str],
    forming_segment: LineLike,
    histogram: Mapping[int, float],
    area_cache: MutableMapping[MacdAreaKey, float] | None = None,
    *,
    diff: Mapping[int, float] | None = None,
    dea: Mapping[int, float] | None = None,
    comparison_audit: list[ComparisonAudit] | None = None,
    raw_positions: Mapping[int, int] | None = None,
    certified_complete_positions: bool = False,
    strength_profile: Literal["legacy_macd_area_v1", "price_displacement_speed_v1"] = (
        "legacy_macd_area_v1"
    ),
) -> list[ChanSignal]:
    """Monitor an unconfirmed outward leg without treating it as a center unit.

    The caller supplies the observed leg through the current bar. Its changing
    area and extrema may remove the signal on the next bar. Only completed
    segment centers and completed reference legs are used as structural facts.
    """
    if not segments or not centers or len(centers) != len(center_ids):
        return []
    center = centers[-1]
    if (
        center.status == "left"
        or center.end_index != len(segments) - 1
        or forming_segment.start.bar_index != segments[-1].end.bar_index
        or forming_segment.end.bar_index <= forming_segment.start.bar_index
    ):
        return []
    direction = forming_segment.direction
    if direction == "up":
        outward = _high(forming_segment) > center.zg_i64
    else:
        outward = _low(forming_segment) < center.zd_i64
    if not outward:
        return []

    result: list[ChanSignal] = []
    current_index = len(segments)
    known_at = forming_segment.known_at_bar_index
    if center.base_index > 0:
        a_index = _contiguous_preceding_same_direction(segments, center.base_index, direction)
        if a_index is not None:
            value = _divergence(
                "consolidation",
                segments[a_index],
                forming_segment,
                current_index,
                center_ids[-1],
                known_at,
                histogram,
                area_cache,
                require_new_extreme=False,
                diff=diff,
                dea=dea,
                reference_baseline_i64=segments[a_index].start.price_i64,
                current_baseline_i64=center.zd_i64 if direction == "up" else center.zg_i64,
                comparison_audit=comparison_audit,
                raw_positions=raw_positions,
                certified_complete_positions=certified_complete_positions,
                strength_profile=strength_profile,
            )
            if value is not None:
                result.append(
                    replace(
                        value,
                        status="forming",
                        divergence_profile="external_range",
                        comparison_rule=(
                            "price_displacement_speed_or_baseline_span_below_80pct_v1"
                            if strength_profile == "price_displacement_speed_v1"
                            else "macd_area_and_center_baseline_price_span_contraction"
                        ),
                        formation_dir=center.formation_dir,
                        relative_dir=center.relative_dir,
                        a_object_id=segments[a_index].object_id,
                        b_center_id=center_ids[-1],
                        follow_through_status="pending",
                        reference_center_ordinal=len(centers),
                        older_center_count=len(centers) - 1,
                        center_chain_profile="confirmed_same_level_centers_known_at_signal_v1",
                    )
                )

    if len(centers) < 2:
        return result
    first = centers[-2]
    second = center
    if (
        first.status != "left"
        or first.exit_index is None
        or first.base_index < 1
        or first.end_index >= second.base_index
        or second.base_index not in {first.exit_index, first.exit_index + 1}
    ):
        return result
    first_dd, first_gg = _outer_range(first, segments)
    second_dd, second_gg = _outer_range(second, segments)
    if direction == "up":
        strict_migration = second.zd_i64 > first.zg_i64 and second_dd > first_gg
    else:
        strict_migration = second.zg_i64 < first.zd_i64 and second_gg < first_dd
    if not strict_migration or second.relative_dir != direction.upper():
        return result
    a_index = _contiguous_preceding_same_direction(segments, first.base_index, direction)
    if a_index is None:
        return result
    b_index = first.exit_index
    if not _trend_legs_advance(segments[a_index], segments[b_index], forming_segment, direction):
        return result
    prior_extreme = (
        max(_high(line) for line in segments[a_index:])
        if direction == "up"
        else min(_low(line) for line in segments[a_index:])
    )
    new_extreme = (
        _high(forming_segment) > prior_extreme
        if direction == "up"
        else _low(forming_segment) < prior_extreme
    )
    value = _divergence(
        "trend",
        segments[b_index],
        forming_segment,
        current_index,
        center_ids[-1],
        known_at,
        histogram,
        area_cache,
        require_new_extreme=False,
        diff=diff,
        dea=dea,
        reference_baseline_i64=first.zd_i64 if direction == "up" else first.zg_i64,
        current_baseline_i64=second.zd_i64 if direction == "up" else second.zg_i64,
        legacy_new_extreme_satisfied=new_extreme,
        comparison_audit=comparison_audit,
        raw_positions=raw_positions,
        certified_complete_positions=certified_complete_positions,
        strength_profile=strength_profile,
    )
    if value is not None:
        relative_direction: Literal["UP", "DOWN"] = "UP" if direction == "up" else "DOWN"
        result.append(
            replace(
                value,
                status="forming",
                divergence_profile="segment_trend_candidate",
                formation_dir=second.formation_dir,
                relative_dir=relative_direction,
                a_object_id=segments[a_index].object_id,
                b_object_id=segments[b_index].object_id,
                a_center_id=center_ids[-2],
                b_center_id=center_ids[-1],
                new_extreme_satisfied=True,
                comparison_rule=(
                    "price_displacement_speed_or_baseline_span_below_80pct_v1"
                    if strength_profile == "price_displacement_speed_v1"
                    else "macd_area_center_baseline_price_span_and_trend_new_extreme"
                ),
                c_contains_type3=None,
                c_meets_sublevel=None,
                follow_through_status="pending",
                reference_center_ordinal=len(centers),
                older_center_count=len(centers) - 1,
                center_chain_profile="confirmed_same_level_centers_known_at_signal_v1",
            )
        )
        return [signal for signal in result if signal.divergence_kind == "trend"]
    return result


def chan_first_point_candidates(
    segments: Sequence[LineLike],
    centers: list[StructuralCenter],
    center_ids: list[str],
    histogram: Mapping[int, float],
    area_cache: MutableMapping[MacdAreaKey, float] | None = None,
    *,
    level_id: str = "L0",
) -> list[ChanSignal]:
    """Do not infer a standard B1/S1 from a single segment c without proof."""
    # The segment-only center projection has no evidence that c contains B's
    # third point or a complete sublevel trend. Retain this API for the later
    # proof-bearing caller, but never upgrade a segment candidate by inference.
    return []


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
    segments: Sequence[LineLike], centers: list[StructuralCenter], center_ids: list[str]
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
        outer_low, outer_high = _body_range(center, segments)
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
    centers: list[StructuralCenter],
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
        if divergence.status != "confirmed":
            continue
        if divergence.divergence_kind == "center_oscillation":
            continue
        if divergence.divergence_kind == "trend" and (
            divergence.divergence_profile != "standard_trend"
            or divergence.status != "confirmed"
            or divergence.c_contains_type3 is not True
            or divergence.c_meets_sublevel is not True
        ):
            continue
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
