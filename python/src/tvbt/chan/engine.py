from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, Literal

from tvbt.chan.events import EventEmitter
from tvbt.chan.reference import reference_centers, reference_segments

Direction = Literal["up", "down", "unknown"]
FractalKind = Literal["top", "bottom"]
REFERENCE_MIN_INDEPENDENT_BARS = 5


def _stable_id(prefix: str, *parts: object) -> str:
    data = json.dumps(parts, ensure_ascii=False, separators=(",", ":"), default=str).encode()
    return f"{prefix}-{hashlib.sha256(data).hexdigest()[:20]}"


@dataclass(frozen=True)
class RawBar:
    bar_index: int
    time: int
    high_i64: int
    low_i64: int
    close_i64: int


@dataclass
class IncludedBar:
    normalized_index: int
    start_raw_index: int
    end_raw_index: int
    high_i64: int
    low_i64: int
    high_time: int
    low_time: int
    high_raw_index: int
    low_raw_index: int
    confirm_time: int
    direction: Direction
    source_raw_indices: list[int]


@dataclass(frozen=True)
class Fractal:
    object_id: str
    fractal_type: FractalKind
    normalized_index: int
    bar_index: int
    time: int
    price_i64: int
    confirmed_at_bar_index: int
    known_at_bar_index: int

    def payload(self) -> dict[str, Any]:
        return {
            "bar_index": self.bar_index,
            "time": self.time,
            "price_i64": self.price_i64,
            "fractal_type": self.fractal_type,
            "confirmed": True,
            "confirmed_at_bar_index": self.confirmed_at_bar_index,
        }


@dataclass(frozen=True)
class LineObject:
    object_id: str
    start: Fractal
    end: Fractal
    direction: Literal["up", "down"]
    confirmed_at_bar_index: int
    known_at_bar_index: int

    def payload(self, *, confirmed: bool = True) -> dict[str, Any]:
        return {
            "start_bar_index": self.start.bar_index,
            "start_time": self.start.time,
            "start_price_i64": self.start.price_i64,
            "end_bar_index": self.end.bar_index,
            "end_time": self.end.time,
            "end_price_i64": self.end.price_i64,
            "direction": self.direction,
            "confirmed": confirmed,
            "confirmed_at_bar_index": self.confirmed_at_bar_index if confirmed else None,
        }


@dataclass(frozen=True)
class ChanParameters:
    checkpoint_interval: int = 1024

    def __post_init__(self) -> None:
        if self.checkpoint_interval < 1:
            raise ValueError("bar-count parameters must be positive")


class ChanEngine:
    algorithm_version = "3.0.0"

    def __init__(self, parameters: ChanParameters | None = None) -> None:
        self.parameters = parameters or ChanParameters()
        self.raw_bars: list[RawBar] = []
        self.included: list[IncludedBar] = []
        self.fractals: list[Fractal] = []
        self.bi: list[LineObject] = []
        self._stroke_start: Fractal | None = None
        self._pending_fractals: list[Fractal] = []
        self._provisional_bi_id: str | None = None
        self.emitter = EventEmitter()

    def update(self, bar: RawBar) -> None:
        if self.raw_bars and bar.bar_index != self.raw_bars[-1].bar_index + 1:
            raise ValueError("raw bars must have contiguous bar_index")
        if self.raw_bars and bar.time <= self.raw_bars[-1].time:
            raise ValueError("raw bar times must be strictly increasing")
        if bar.low_i64 > bar.high_i64:
            raise ValueError("raw bar low exceeds high")
        self.raw_bars.append(bar)
        appended = self._update_inclusion(bar)
        bi_count = len(self.bi)
        if appended:
            fractal = self._seal_fractal()
            if fractal is not None:
                self.fractals.append(fractal)
                self.emitter.upsert(
                    fractal.known_at_bar_index,
                    "fractal",
                    fractal.object_id,
                    fractal.payload(),
                )
                self._consume_fractal(fractal)
        self._update_provisional_bi(bar.bar_index)
        if len(self.bi) != bi_count:
            self._update_structures(bar.bar_index)

    def _update_inclusion(self, bar: RawBar) -> bool:
        current = IncludedBar(
            normalized_index=len(self.included),
            start_raw_index=bar.bar_index,
            end_raw_index=bar.bar_index,
            high_i64=bar.high_i64,
            low_i64=bar.low_i64,
            high_time=bar.time,
            low_time=bar.time,
            high_raw_index=bar.bar_index,
            low_raw_index=bar.bar_index,
            confirm_time=bar.time,
            direction="down",
            source_raw_indices=[bar.bar_index],
        )
        if not self.included:
            self.included.append(current)
            return True
        previous = self.included[-1]
        if current.high_i64 > previous.high_i64 and current.low_i64 > previous.low_i64:
            current.direction = "up"
            self.included.append(current)
            return True
        if current.high_i64 < previous.high_i64 and current.low_i64 < previous.low_i64:
            current.direction = "down"
            self.included.append(current)
            return True
        direction = previous.direction
        first_pair = len(self.included) == 1 and previous.start_raw_index == previous.end_raw_index
        if first_pair:
            right_contains = (
                current.high_i64 > previous.high_i64 or current.low_i64 < previous.low_i64
            )
            if right_contains:
                high = current.high_i64
                low = previous.low_i64
                high_from_current = current.high_i64 != previous.high_i64
                low_from_current = False
            else:
                high = previous.high_i64
                low = current.low_i64
                high_from_current = False
                low_from_current = current.low_i64 != previous.low_i64
        elif direction == "up":
            high_from_current = current.high_i64 > previous.high_i64
            low_from_current = current.low_i64 > previous.low_i64
            high = max(previous.high_i64, current.high_i64)
            low = max(previous.low_i64, current.low_i64)
        else:
            high_from_current = current.high_i64 < previous.high_i64
            low_from_current = current.low_i64 < previous.low_i64
            high = min(previous.high_i64, current.high_i64)
            low = min(previous.low_i64, current.low_i64)
        self.included[-1] = IncludedBar(
            normalized_index=previous.normalized_index,
            start_raw_index=previous.start_raw_index,
            end_raw_index=bar.bar_index,
            high_i64=high,
            low_i64=low,
            high_time=current.high_time if high_from_current else previous.high_time,
            low_time=current.low_time if low_from_current else previous.low_time,
            high_raw_index=current.high_raw_index if high_from_current else previous.high_raw_index,
            low_raw_index=current.low_raw_index if low_from_current else previous.low_raw_index,
            confirm_time=bar.time,
            direction=direction,
            source_raw_indices=[*previous.source_raw_indices, bar.bar_index],
        )
        return False

    def _seal_fractal(self) -> Fractal | None:
        # kline-chart/c_bi.py defines a strict fractal from exactly three independent
        # bars. The newly appended independent bar seals the middle bar immediately.
        index = len(self.included) - 2
        if index < 1:
            return None
        center = self.included[index]
        window = self.included[index - 1 : index + 2]
        others = [window[0], window[2]]
        top = all(
            center.high_i64 > item.high_i64 and center.low_i64 > item.low_i64 for item in others
        )
        bottom = all(
            center.high_i64 < item.high_i64 and center.low_i64 < item.low_i64 for item in others
        )
        if not top and not bottom:
            return None
        kind: FractalKind = "top" if top else "bottom"
        pivot_index = center.high_raw_index if top else center.low_raw_index
        pivot_time = center.high_time if top else center.low_time
        price = center.high_i64 if top else center.low_i64
        confirmed_at = self.included[index + 1].end_raw_index
        return Fractal(
            object_id=_stable_id("fractal", kind, pivot_time, confirmed_at, price),
            fractal_type=kind,
            normalized_index=index,
            bar_index=pivot_index,
            time=pivot_time,
            price_i64=price,
            confirmed_at_bar_index=confirmed_at,
            known_at_bar_index=confirmed_at,
        )

    def _consume_fractal(self, endpoint: Fractal) -> None:
        self._pending_fractals.append(endpoint)
        if self._stroke_start is None:
            self._stroke_start = endpoint
        while len(self._pending_fractals) >= 3:
            selection = self._reference_selection()
            if selection is None:
                return
            endpoint_position, known_at = selection
            if known_at is None:
                return
            # One newly sealed fractal can release several pending lines.  None of
            # those lines was knowable before the fractal that released the batch,
            # and their causal timestamps must not move backwards within the batch.
            known_at = max(
                known_at,
                endpoint.known_at_bar_index,
                self.bi[-1].known_at_bar_index if self.bi else 0,
            )
            start = self._pending_fractals[0]
            selected = self._pending_fractals[endpoint_position]
            direction: Literal["up", "down"] = "up" if start.fractal_type == "bottom" else "down"
            if self.bi and self.bi[-1].direction == direction:
                raise AssertionError("reference bi directions must alternate")
            line = LineObject(
                object_id=_stable_id("bi", start.object_id, selected.object_id),
                start=start,
                end=selected,
                direction=direction,
                confirmed_at_bar_index=known_at,
                known_at_bar_index=known_at,
            )
            if self._provisional_bi_id is not None:
                self.emitter.delete(known_at, "bi", self._provisional_bi_id)
                self._provisional_bi_id = None
            self.bi.append(line)
            self.emitter.upsert(known_at, "bi", line.object_id, line.payload())
            self._pending_fractals = self._pending_fractals[endpoint_position:]
            self._stroke_start = selected

    def _reference_selection(self) -> tuple[int, int | None] | None:
        """Port of kline-chart c_bi.get_node/satisfy_the_number for one pending base."""
        values = self._pending_fractals
        if len(values) < 2:
            return None
        base = values[0]
        next_position = next(
            (
                position
                for position in range(1, len(values))
                if values[position].fractal_type != base.fractal_type
            ),
            -1,
        )
        while next_position >= 0:
            independent_count = values[next_position].normalized_index - base.normalized_index + 1
            if independent_count < REFERENCE_MIN_INDEPENDENT_BARS:
                next_position += 1
                while (
                    next_position < len(values)
                    and values[next_position].fractal_type == base.fractal_type
                ):
                    next_position += 1
                if next_position >= len(values):
                    return None
                continue
            return self._reference_satisfy(next_position)
        return None

    def _reference_satisfy(self, endpoint_position: int) -> tuple[int, int | None]:
        values = self._pending_fractals
        base_is_top = values[0].fractal_type == "top"
        baseline_position = endpoint_position
        cursor = endpoint_position + 1
        while cursor < len(values):
            candidate = values[cursor]
            endpoint = values[endpoint_position]
            if candidate.fractal_type == endpoint.fractal_type:
                candidate_bar = self.included[candidate.normalized_index]
                endpoint_bar = self.included[endpoint.normalized_index]
                more_extreme = (base_is_top and candidate_bar.low_i64 < endpoint_bar.low_i64) or (
                    not base_is_top and candidate_bar.high_i64 > endpoint_bar.high_i64
                )
                if more_extreme:
                    endpoint_position = cursor
                    baseline_position = cursor
                cursor += 1
                continue
            independent_count = (
                candidate.normalized_index - values[baseline_position].normalized_index + 1
            )
            if independent_count < REFERENCE_MIN_INDEPENDENT_BARS:
                cursor += 1
                continue
            candidate_bar = self.included[candidate.normalized_index]
            endpoint_bar = self.included[values[endpoint_position].normalized_index]
            separated = (
                candidate_bar.low_i64 > endpoint_bar.high_i64
                if base_is_top
                else candidate_bar.high_i64 < endpoint_bar.low_i64
            )
            if separated:
                return endpoint_position, candidate.known_at_bar_index
            cursor += 1
        # The reference batch renderer includes this final, not-yet-validated endpoint.
        return endpoint_position, None

    def _update_provisional_bi(self, known_at_bar_index: int) -> None:
        selection = self._reference_selection()
        if selection is None or selection[1] is not None:
            if self._provisional_bi_id is not None:
                self.emitter.delete(known_at_bar_index, "bi", self._provisional_bi_id)
                self._provisional_bi_id = None
            return
        endpoint_position, _ = selection
        start = self._pending_fractals[0]
        endpoint = self._pending_fractals[endpoint_position]
        provisional_id = _stable_id("bi-provisional", start.object_id)
        if self._provisional_bi_id is not None and self._provisional_bi_id != provisional_id:
            self.emitter.delete(known_at_bar_index, "bi", self._provisional_bi_id)
        self._provisional_bi_id = provisional_id
        line = LineObject(
            provisional_id,
            start,
            endpoint,
            "up" if start.fractal_type == "bottom" else "down",
            known_at_bar_index,
            known_at_bar_index,
        )
        self.emitter.upsert(known_at_bar_index, "bi", provisional_id, line.payload(confirmed=False))

    def _update_structures(self, known_at_bar_index: int) -> None:
        centers: list[tuple[str, dict[str, Any], int]] = []
        for center in reference_centers(self.bi):
            object_id = _stable_id(
                "zhongshu",
                self.bi[center.base_index].object_id,
                self.bi[center.seed_end_index].object_id,
            )
            centers.append(
                (
                    object_id,
                    {
                        "start_bar_index": center.start_bar_index,
                        "start_time": center.start_time,
                        "end_bar_index": center.end_bar_index,
                        "end_time": center.end_time,
                        "zg_i64": center.zg_i64,
                        "zd_i64": center.zd_i64,
                        "confirmed": True,
                        "confirmed_at_bar_index": center.known_at_bar_index,
                        "status": center.status,
                        "leave_direction": center.leave_direction,
                    },
                    center.known_at_bar_index,
                )
            )
        segments: list[tuple[str, dict[str, Any], int]] = []
        for segment in reference_segments(self.bi, self.raw_bars):
            object_id = _stable_id(
                "segment",
                self.bi[segment.start_index].object_id,
                "up" if segment.up else "down",
            )
            segments.append(
                (
                    object_id,
                    {
                        "start_bar_index": segment.start_bar_index,
                        "start_time": segment.start_time,
                        "start_price_i64": segment.start_price_i64,
                        "end_bar_index": segment.end_bar_index,
                        "end_time": segment.end_time,
                        "end_price_i64": segment.end_price_i64,
                        "direction": "up" if segment.up else "down",
                        "confirmed": segment.confirmed,
                        "confirmed_at_bar_index": (
                            segment.known_at_bar_index if segment.confirmed else None
                        ),
                    },
                    segment.known_at_bar_index,
                )
            )
        self._sync_objects("zhongshu", centers, known_at_bar_index)
        self._sync_objects("segment", segments, known_at_bar_index)

    def _sync_objects(
        self,
        object_type: str,
        values: list[tuple[str, dict[str, Any], int]],
        known_at_bar_index: int,
    ) -> None:
        current_values = {
            str(item["object_id"]): item for item in self.emitter.current(object_type)
        }
        desired = {object_id for object_id, _, _ in values}
        for object_id in sorted(current_values.keys() - desired):
            self.emitter.delete(known_at_bar_index, object_type, object_id)
        for object_id, payload, known_at in values:
            previous = current_values.get(object_id)
            if previous is not None and all(
                previous.get(name) == value for name, value in payload.items()
            ):
                continue
            # A structure can be discovered only after a later line changes the
            # reference scan's base position.  Its event must record discovery
            # time, never a historical formation index that was not yet known.
            event_known_at = max(known_at, known_at_bar_index)
            self.emitter.upsert(event_known_at, object_type, object_id, payload)

    def result_rows(self) -> dict[str, list[dict[str, Any]]]:
        return {
            "fractals": sorted(self.emitter.current("fractal"), key=lambda item: item["bar_index"]),
            "bi": sorted(self.emitter.current("bi"), key=lambda item: item["start_bar_index"]),
            "segments": sorted(
                self.emitter.current("segment"), key=lambda item: item["start_bar_index"]
            ),
            "zhongshu": sorted(
                self.emitter.current("zhongshu"), key=lambda item: item["start_bar_index"]
            ),
        }

    def export_state(self) -> dict[str, Any]:
        return {
            "parameters": asdict(self.parameters),
            "raw_bars": [asdict(item) for item in self.raw_bars],
            "included": [asdict(item) for item in self.included],
            "fractals": [asdict(item) for item in self.fractals],
            "bi": [_line_state(item) for item in self.bi],
            "stroke_start_id": self._stroke_start.object_id if self._stroke_start else None,
            "pending_fractal_ids": [item.object_id for item in self._pending_fractals],
            "provisional_bi_id": self._provisional_bi_id,
            "emitter": self.emitter.state(),
        }

    @classmethod
    def from_state(cls, state: dict[str, Any]) -> ChanEngine:
        engine = cls(ChanParameters(**state["parameters"]))
        engine.raw_bars = [RawBar(**item) for item in state["raw_bars"]]
        engine.included = [IncludedBar(**item) for item in state["included"]]
        engine.fractals = [Fractal(**item) for item in state["fractals"]]
        fractals = {item.object_id: item for item in engine.fractals}
        engine.bi = [_line_from_state(item, fractals) for item in state["bi"]]
        start_id = state["stroke_start_id"]
        engine._stroke_start = fractals.get(start_id)
        engine._pending_fractals = [fractals[value] for value in state["pending_fractal_ids"]]
        engine._provisional_bi_id = state["provisional_bi_id"]
        engine.emitter = EventEmitter.from_state(state["emitter"])
        return engine


def _line_state(line: LineObject) -> dict[str, Any]:
    return {
        "object_id": line.object_id,
        "start_id": line.start.object_id,
        "end_id": line.end.object_id,
        "direction": line.direction,
        "confirmed_at_bar_index": line.confirmed_at_bar_index,
        "known_at_bar_index": line.known_at_bar_index,
    }


def _line_from_state(value: dict[str, Any], fractals: dict[str, Fractal]) -> LineObject:
    return LineObject(
        object_id=value["object_id"],
        start=fractals[value["start_id"]],
        end=fractals[value["end_id"]],
        direction=value["direction"],
        confirmed_at_bar_index=value["confirmed_at_bar_index"],
        known_at_bar_index=value["known_at_bar_index"],
    )
