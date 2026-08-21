from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol, cast

RelativePosition = Literal["above", "below", "equal"]
OscillationBias = Literal["strong", "weak", "neutral"]
BreakoutWarning = Literal[
    "cross_above_b",
    "cross_below_a",
    "rising_wedge_below_b",
    "falling_wedge_above_a",
]


class AnchorLike(Protocol):
    @property
    def bar_index(self) -> int: ...

    @property
    def time(self) -> int: ...

    @property
    def price_i64(self) -> int: ...


class ComponentLike(Protocol):
    @property
    def object_id(self) -> str: ...

    @property
    def direction(self) -> str: ...

    @property
    def start(self) -> AnchorLike: ...

    @property
    def end(self) -> AnchorLike: ...

    @property
    def known_at_bar_index(self) -> int: ...


@dataclass(frozen=True)
class ZnObservation:
    component_object_id: str
    component_ordinal: int
    bar_index: int
    time: int
    z_i64: int
    zn_i64: int
    z_twice_i64: int
    zn_twice_i64: int
    core_low_i64: int
    core_high_i64: int
    range_high_i64: int
    range_low_i64: int
    component_direction: Literal["up", "down"]
    relative_position: RelativePosition
    oscillation_bias: OscillationBias
    breakout_warning: BreakoutWarning | None
    known_at_bar_index: int


def _group_confirmation_index(position: int) -> int:
    """Return the last component needed before a Zn position is structurally known."""
    ordinal = position + 1
    if ordinal <= 3:
        return 2
    return position if ordinal % 2 == 1 else position + 1


def classify_zn_components(
    *,
    core_low_i64: int,
    core_high_i64: int,
    components: Sequence[ComponentLike],
    maximum_points: int = 9,
    wedge_points: int = 3,
) -> list[ZnObservation]:
    """Build the causal ALG-AUX-004 Z/Zn series from confirmed center components.

    Midpoints remain exact in doubled fixed-point integers. The compatibility
    ``*_i64`` values are lower tick-grid projections and never drive comparisons.
    A warning is auxiliary evidence only; it cannot confirm a third point.
    """
    if core_low_i64 > core_high_i64:
        raise ValueError("center core must satisfy core_low_i64 <= core_high_i64")
    if maximum_points < 3 or maximum_points > 9 or maximum_points % 2 == 0:
        raise ValueError("maximum_points must be an odd integer between 3 and 9")
    if wedge_points < 3 or wedge_points > maximum_points:
        raise ValueError("wedge_points must be between 3 and maximum_points")
    if len(components) < 3:
        return []

    bounded = list(components[:maximum_points])
    # Center extensions accept two alternating components together. Refuse an
    # unfinished pair instead of assigning its earlier member retroactively.
    if len(bounded) % 2 == 0:
        bounded.pop()
    z_twice_i64 = core_low_i64 + core_high_i64
    boundary_low_twice = core_low_i64 * 2
    boundary_high_twice = core_high_i64 * 2
    zn_twice_values: list[int] = []
    observations: list[ZnObservation] = []

    for position, component in enumerate(bounded):
        if component.direction not in {"up", "down"}:
            raise ValueError("center component direction must be up or down")
        direction = cast(Literal["up", "down"], component.direction)
        range_low_i64 = min(component.start.price_i64, component.end.price_i64)
        range_high_i64 = max(component.start.price_i64, component.end.price_i64)
        zn_twice_i64 = range_low_i64 + range_high_i64
        zn_twice_values.append(zn_twice_i64)
        relative_position: RelativePosition = (
            "above"
            if zn_twice_i64 > z_twice_i64
            else "below"
            if zn_twice_i64 < z_twice_i64
            else "equal"
        )
        oscillation_bias: OscillationBias = (
            "strong"
            if relative_position == "above"
            else "weak"
            if relative_position == "below"
            else "neutral"
        )
        breakout_warning: BreakoutWarning | None = None
        if zn_twice_i64 > boundary_high_twice:
            breakout_warning = "cross_above_b"
        elif zn_twice_i64 < boundary_low_twice:
            breakout_warning = "cross_below_a"
        elif len(zn_twice_values) >= wedge_points:
            tail = zn_twice_values[-wedge_points:]
            if all(tail[index] < tail[index + 1] for index in range(len(tail) - 1)):
                breakout_warning = "rising_wedge_below_b"
            elif all(tail[index] > tail[index + 1] for index in range(len(tail) - 1)):
                breakout_warning = "falling_wedge_above_a"

        confirmation_index = _group_confirmation_index(position)
        known_at_bar_index = max(
            value.known_at_bar_index for value in bounded[: confirmation_index + 1]
        )
        observations.append(
            ZnObservation(
                component_object_id=component.object_id,
                component_ordinal=position + 1,
                bar_index=component.end.bar_index,
                time=component.end.time,
                z_i64=z_twice_i64 // 2,
                zn_i64=zn_twice_i64 // 2,
                z_twice_i64=z_twice_i64,
                zn_twice_i64=zn_twice_i64,
                core_low_i64=core_low_i64,
                core_high_i64=core_high_i64,
                range_high_i64=range_high_i64,
                range_low_i64=range_low_i64,
                component_direction=direction,
                relative_position=relative_position,
                oscillation_bias=oscillation_bias,
                breakout_warning=breakout_warning,
                known_at_bar_index=known_at_bar_index,
            )
        )
    return observations
