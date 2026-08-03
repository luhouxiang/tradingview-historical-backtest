from __future__ import annotations

import copy
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol


class BarLike(Protocol):
    @property
    def bar_index(self) -> int: ...

    @property
    def time(self) -> int: ...

    @property
    def high_i64(self) -> int: ...

    @property
    def low_i64(self) -> int: ...


class EndpointLike(Protocol):
    @property
    def bar_index(self) -> int: ...

    @property
    def time(self) -> int: ...

    @property
    def price_i64(self) -> int: ...


class LineLike(Protocol):
    @property
    def object_id(self) -> str: ...

    @property
    def start(self) -> EndpointLike: ...

    @property
    def end(self) -> EndpointLike: ...

    @property
    def direction(self) -> Literal["up", "down"]: ...

    @property
    def known_at_bar_index(self) -> int: ...


@dataclass
class ReferenceSegment:
    start_index: int = 0
    end_index: int = 0
    up: bool = False
    confirmed: bool = False
    known_at_bar_index: int = 0
    start_bar_index: int = 0
    end_bar_index: int = 0
    start_time: int = 0
    end_time: int = 0
    start_price_i64: int = 0
    end_price_i64: int = 0


@dataclass(frozen=True)
class ReferenceCenter:
    base_index: int
    seed_end_index: int
    start_bar_index: int
    end_bar_index: int
    start_time: int
    end_time: int
    zd_i64: int
    zg_i64: int
    known_at_bar_index: int
    status: Literal["confirmed", "extended", "left"]
    leave_direction: Literal["up", "down"] | None


def _low(line: LineLike) -> int:
    return min(line.start.price_i64, line.end.price_i64)


def _high(line: LineLike) -> int:
    return max(line.start.price_i64, line.end.price_i64)


def _flipped_side_up(line: LineLike) -> bool:
    # algo-ui/common/chanlun/c_bi.py::_NCHDUAN flips every stBiK.side before scanning.
    return line.direction == "down"


def _find_first_segment(
    current: int,
    lines: Sequence[LineLike],
    bars: dict[int, BarLike],
    maximum: int,
    minimum: int,
    segment: ReferenceSegment,
) -> bool:
    current_bar = bars[lines[current].start.bar_index]
    if _flipped_side_up(lines[current]):
        maximum_bar = bars[lines[maximum].start.bar_index]
        if current_bar.high_i64 > maximum_bar.high_i64:
            maximum = current
        if current - minimum < 3:
            return False
        previous = bars[lines[current - 2].start.bar_index]
        if current_bar.high_i64 > previous.high_i64:
            current_bar = bars[lines[current - 1].start.bar_index]
            previous = bars[lines[current - 3].start.bar_index]
            if current_bar.high_i64 > previous.high_i64:
                segment.start_index = minimum
                segment.end_index = current
                segment.up = True
                return True
    else:
        minimum_bar = bars[lines[minimum].start.bar_index]
        if current_bar.low_i64 < minimum_bar.low_i64:
            minimum = current
        if current - maximum < 3:
            return False
        previous = bars[lines[current - 2].start.bar_index]
        if current_bar.low_i64 < previous.low_i64:
            current_bar = bars[lines[current - 1].start.bar_index]
            previous = bars[lines[current - 3].start.bar_index]
            # This high-to-high comparison intentionally follows algo-ui exactly.
            if current_bar.high_i64 < previous.high_i64:
                segment.start_index = maximum
                segment.end_index = current
                segment.up = False
                return True
    return False


def _is_overlap(current: int, lines: Sequence[LineLike], bars: dict[int, BarLike]) -> bool:
    if current < 3:
        return False
    current_bar = bars[lines[current].start.bar_index]
    previous = bars[lines[current - 3].start.bar_index]
    if _flipped_side_up(lines[current]):
        return current_bar.high_i64 >= previous.low_i64
    return current_bar.low_i64 <= previous.high_i64


def _confirm_low_segment(
    segment: ReferenceSegment,
    current: int,
    lines: Sequence[LineLike],
    bars: dict[int, BarLike],
) -> int:
    current_bar = bars[lines[current].start.bar_index]
    end_bar = bars[lines[segment.end_index].start.bar_index]
    if not _flipped_side_up(lines[current]):
        if current_bar.low_i64 < end_bar.low_i64:
            segment.end_index = current
        return -1
    if current - segment.end_index < 3:
        return -1
    end_position = segment.end_index + 1
    for index in range(segment.end_index + 3, current + 1, 2):
        candidate = bars[lines[index].start.bar_index]
        end_candidate = bars[lines[end_position].start.bar_index]
        if candidate.high_i64 <= end_candidate.high_i64:
            end_position = index
            continue
        maximum = bars[lines[segment.start_index + 2].start.bar_index].high_i64
        minimum = bars[lines[segment.start_index + 1].start.bar_index].low_i64
        for cursor in range(segment.start_index + 3, segment.end_index, 2):
            first = bars[lines[cursor].start.bar_index]
            second = bars[lines[cursor + 1].start.bar_index]
            if minimum < first.low_i64:
                if maximum < second.high_i64:
                    minimum = first.low_i64
                maximum = second.high_i64
            else:
                minimum = first.low_i64
                maximum = second.high_i64
        return 1 if bars[lines[end_position].start.bar_index].high_i64 < minimum else 0
    return -1


def _confirm_up_segment(
    segment: ReferenceSegment,
    current: int,
    lines: Sequence[LineLike],
    bars: dict[int, BarLike],
) -> int:
    current_bar = bars[lines[current].start.bar_index]
    end_bar = bars[lines[segment.end_index].start.bar_index]
    if _flipped_side_up(lines[current]):
        if current_bar.high_i64 > end_bar.high_i64:
            segment.end_index = current
        return -1
    if current - segment.end_index < 3:
        return -1
    end_position = segment.end_index + 1
    for index in range(segment.end_index + 3, current + 1, 2):
        candidate = bars[lines[index].start.bar_index]
        end_candidate = bars[lines[end_position].start.bar_index]
        if candidate.low_i64 >= end_candidate.low_i64:
            end_position = index
            continue
        maximum = bars[lines[segment.start_index + 1].start.bar_index].high_i64
        minimum = bars[lines[segment.start_index + 2].start.bar_index].low_i64
        for cursor in range(segment.start_index + 3, segment.end_index, 2):
            first = bars[lines[cursor].start.bar_index]
            second = bars[lines[cursor + 1].start.bar_index]
            if maximum > first.high_i64:
                if minimum > second.low_i64:
                    maximum = first.high_i64
                minimum = second.low_i64
            else:
                maximum = first.high_i64
                minimum = second.low_i64
        return 1 if bars[lines[end_position].start.bar_index].low_i64 > maximum else 0
    return -1


def _confirm_segment(
    segment: ReferenceSegment,
    current: int,
    lines: Sequence[LineLike],
    bars: dict[int, BarLike],
) -> int:
    if segment.up:
        return _confirm_up_segment(segment, current, lines, bars)
    return _confirm_low_segment(segment, current, lines, bars)


def _append_segment(
    target: list[ReferenceSegment],
    segment: ReferenceSegment,
    lines: Sequence[LineLike],
    known_at: int,
) -> None:
    value = copy.deepcopy(segment)
    value.known_at_bar_index = known_at
    target.append(value)


def _update_segment(
    current: int,
    segment: ReferenceSegment,
    temporary: ReferenceSegment,
    lines: Sequence[LineLike],
    result: list[ReferenceSegment],
    bars: dict[int, BarLike],
) -> None:
    known_at = lines[current].known_at_bar_index
    if temporary.start_index == temporary.end_index:
        status = _confirm_segment(segment, current, lines, bars)
        if status == -1:
            return
        if status == 0:
            segment.confirmed = True
            _append_segment(result, segment, lines, known_at)
            segment.start_index = segment.end_index
            segment.end_index = current
            segment.confirmed = False
            segment.up = not segment.up
        else:
            temporary.start_index = segment.end_index
            temporary.end_index = current
            temporary.confirmed = False
            temporary.up = not segment.up
        return

    status = _confirm_segment(temporary, current, lines, bars)
    if status == -1:
        current_bar = bars[lines[current].start.bar_index]
        temporary_start = bars[lines[temporary.start_index].start.bar_index]
        if _flipped_side_up(lines[current]) and not temporary.up:
            if current_bar.high_i64 > temporary_start.high_i64:
                segment.end_index = current
                temporary.start_index = temporary.end_index = 0
        elif temporary.up and current_bar.low_i64 < temporary_start.low_i64:
            segment.end_index = current
            temporary.start_index = temporary.end_index = 0
        return

    segment.confirmed = True
    _append_segment(result, segment, lines, known_at)
    segment.confirmed = False
    if status == 0:
        temporary.confirmed = True
        _append_segment(result, temporary, lines, known_at)
        segment.start_index = temporary.end_index
        segment.end_index = current
        segment.up = not temporary.up
        temporary.start_index = temporary.end_index = 0
    else:
        segment.start_index = temporary.start_index
        segment.end_index = temporary.end_index
        segment.up = temporary.up
        temporary.start_index = segment.end_index
        temporary.end_index = current
        temporary.up = not segment.up


def reference_segments(
    lines: Sequence[LineLike], raw_bars: Sequence[BarLike]
) -> list[ReferenceSegment]:
    """Faithful port of algo-ui ``_NCHDUAN`` without mutating the input lines."""
    if len(lines) < 4:
        return []
    bars = {bar.bar_index: bar for bar in raw_bars}
    result: list[ReferenceSegment] = []
    minimum = -1
    maximum = -1
    segment = ReferenceSegment()
    temporary = ReferenceSegment()
    status = 0
    for current in range(3, len(lines)):
        if status == 0:
            if not _is_overlap(current, lines, bars):
                minimum = maximum = -1
                continue
            if minimum == -1:
                minimum = maximum = current - 3
                for cursor in range(current - 2, current):
                    candidate = bars[lines[cursor].start.bar_index]
                    if candidate.high_i64 > bars[lines[maximum].start.bar_index].high_i64:
                        maximum = cursor
                    if candidate.low_i64 < bars[lines[minimum].start.bar_index].low_i64:
                        minimum = cursor
            if not _find_first_segment(current, lines, bars, maximum, minimum, segment):
                continue
            status = 1
            minimum = maximum = 1
            continue
        _update_segment(current, segment, temporary, lines, result, bars)

    final_known_at = lines[-1].known_at_bar_index
    if segment.start_index != segment.end_index:
        _append_segment(result, segment, lines, final_known_at)
    if temporary.start_index != temporary.end_index:
        _append_segment(result, temporary, lines, final_known_at)

    for value in result:
        start_line = lines[value.start_index]
        end_line = lines[value.end_index]
        value.start_bar_index = start_line.start.bar_index
        value.end_bar_index = end_line.start.bar_index
        value.start_time = start_line.start.time
        value.end_time = end_line.start.time
        if value.up:
            value.start_price_i64 = _low(start_line)
            value.end_price_i64 = _high(end_line)
        else:
            value.start_price_i64 = _high(start_line)
            value.end_price_i64 = _low(end_line)
    if result:
        result.pop(0)
    return result


def reference_centers(lines: Sequence[LineLike]) -> list[ReferenceCenter]:
    """Faithful port of algo-ui ``compute_bi_pivots``/``process_down_up``."""
    result: list[ReferenceCenter] = []
    if len(lines) < 5:
        return result
    base = 1
    while base < len(lines) - 2:
        seed_end = base + 2
        if max(_low(lines[base]), _low(lines[seed_end])) > min(
            _high(lines[base]), _high(lines[seed_end])
        ):
            new_base = min(base + 2, len(lines))
        else:
            low = max(_low(lines[base]), _low(lines[seed_end]))
            high = min(_high(lines[base]), _high(lines[seed_end]))
            cursor = base + 4
            end_index = seed_end
            while cursor < len(lines) and max(low, _low(lines[cursor])) <= min(
                high, _high(lines[cursor])
            ):
                end_index = cursor
                cursor += 2
            leave_direction: Literal["up", "down"] | None = None
            status: Literal["confirmed", "extended", "left"] = (
                "extended" if end_index > seed_end else "confirmed"
            )
            if cursor < len(lines):
                status = "left"
                leave_direction = "up" if _low(lines[cursor]) > high else "down"
            used = lines[base : end_index + 1]
            result.append(
                ReferenceCenter(
                    base_index=base,
                    seed_end_index=seed_end,
                    start_bar_index=lines[base].start.bar_index,
                    end_bar_index=lines[end_index].end.bar_index,
                    start_time=lines[base].start.time,
                    end_time=lines[end_index].end.time,
                    zd_i64=low,
                    zg_i64=high,
                    known_at_bar_index=max(line.known_at_bar_index for line in used),
                    status=status,
                    leave_direction=leave_direction,
                )
            )
            new_base = cursor
        base = new_base - 1 if base == new_base - 2 else new_base - 2
    return result
