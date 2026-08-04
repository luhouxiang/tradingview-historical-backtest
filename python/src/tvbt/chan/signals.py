from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Literal

from tvbt.chan.reference import LineLike, ReferenceCenter

DivergenceKind = Literal["trend", "consolidation"]
SignalStrength = Literal["strongest", "normal", "weakest"]
SignalClass = Literal["standard", "class_like"]
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


@dataclass(frozen=True)
class ChanSignal:
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


def _low(line: LineLike) -> int:
    return min(line.start.price_i64, line.end.price_i64)


def _high(line: LineLike) -> int:
    return max(line.start.price_i64, line.end.price_i64)


def _outer_range(center: ReferenceCenter, segments: Sequence[LineLike]) -> tuple[int, int]:
    components = segments[center.base_index : center.end_index + 1]
    return min(_low(line) for line in components), max(_high(line) for line in components)


def _previous_same_direction(
    segments: Sequence[LineLike], before_index: int, direction: str
) -> int | None:
    return next(
        (
            index
            for index in range(before_index - 1, -1, -1)
            if segments[index].direction == direction
        ),
        None,
    )


def _macd_area(line: LineLike, histogram: Mapping[int, float]) -> float:
    values = (
        histogram.get(index, 0.0) for index in range(line.start.bar_index, line.end.bar_index + 1)
    )
    if line.direction == "up":
        return sum(max(value, 0.0) for value in values)
    return sum(abs(min(value, 0.0)) for value in values)


def _divergence(
    kind: DivergenceKind,
    reference: LineLike,
    current: LineLike,
    current_index: int,
    reference_object_id: str,
    known_at_bar_index: int,
    histogram: Mapping[int, float],
    *,
    require_new_extreme: bool = True,
) -> ChanSignal | None:
    if reference.direction != current.direction:
        return None
    reference_area = _macd_area(reference, histogram)
    current_area = _macd_area(current, histogram)
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
    )


def chan_divergences(
    segments: Sequence[LineLike],
    centers: list[ReferenceCenter],
    center_ids: list[str],
    histogram: Mapping[int, float],
) -> list[ChanSignal]:
    """Identify confirmed segment-level trend and consolidation divergence.

    A center's leaving leg is ``exit_index``.  The interleaved leg at
    ``end_index + 1`` is not the leaving trend leg and must never be used for
    MACD strength comparison.
    """
    result: list[ChanSignal] = []
    seen: set[tuple[DivergenceKind, int]] = set()

    # One center: a + Z + c.  A completed counter leg after c confirms c's endpoint.
    for center, center_id in zip(centers, center_ids, strict=True):
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
            require_new_extreme=False,
        )
        if value is not None:
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
        )
        if value is not None and ("trend", current_index) not in seen:
            result.append(value)
            seen.add(("trend", current_index))
    trend_segments = {value.segment_index for value in result if value.divergence_kind == "trend"}
    return [
        value
        for value in result
        if value.divergence_kind == "trend" or value.segment_index not in trend_segments
    ]


def _point_from_divergence(
    divergence_id: str, divergence: ChanSignal, signal_type: SignalType, signal_class: SignalClass
) -> ChanSignal:
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
    )


def _third_points(
    segments: Sequence[LineLike], centers: list[ReferenceCenter], center_ids: list[str]
) -> list[ChanSignal]:
    result: list[ChanSignal] = []
    for center, center_id in zip(centers, center_ids, strict=True):
        if center.status != "left" or center.exit_index is None:
            continue
        leave_index = center.exit_index
        return_index = leave_index + 1
        if return_index >= len(segments):
            continue
        leaving = segments[leave_index]
        returning = segments[return_index]
        if center.leave_direction == "up":
            if (
                leaving.direction != "up"
                or returning.direction != "down"
                or _low(returning) <= center.zg_i64
            ):
                continue
            signal_type: SignalType = "buy_3"
            price = _low(returning)
        else:
            if (
                leaving.direction != "down"
                or returning.direction != "up"
                or _high(returning) >= center.zd_i64
            ):
                continue
            signal_type = "sell_3"
            price = _high(returning)
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
            )
        )
    return result


def chan_trade_points(
    segments: Sequence[LineLike],
    centers: list[ReferenceCenter],
    center_ids: list[str],
    divergences: list[tuple[str, ChanSignal]],
) -> list[ChanSignal]:
    """Create standard and project-defined class-like 1/2/3 points.

    Class-like chains use a consolidation divergence as their origin.  A class
    third point is the first strict third point after that origin when no later
    standard first point of the same side supersedes it.
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
