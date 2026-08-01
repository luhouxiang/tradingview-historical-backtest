from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, Literal

from tvbt.chan.events import EventEmitter

Direction = Literal["up", "down", "unknown"]
FractalKind = Literal["top", "bottom"]


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
    fractal_left: int = 2
    fractal_right: int = 2
    min_stroke_bars: int = 5
    min_stroke_atr: float = 0.5
    atr_period: int = 14
    checkpoint_interval: int = 1024
    initial_inclusion_direction: Literal["up", "down"] = "up"

    def __post_init__(self) -> None:
        if self.fractal_left < 1 or self.fractal_right < 1:
            raise ValueError("fractal window sides must be positive")
        if self.min_stroke_bars < 1 or self.atr_period < 1 or self.checkpoint_interval < 1:
            raise ValueError("bar-count parameters must be positive")
        if self.min_stroke_atr < 0:
            raise ValueError("min_stroke_atr must not be negative")


class ChanEngine:
    algorithm_version = "1.0.0"

    def __init__(self, parameters: ChanParameters | None = None) -> None:
        self.parameters = parameters or ChanParameters()
        self.raw_bars: list[RawBar] = []
        self.included: list[IncludedBar] = []
        self.fractals: list[Fractal] = []
        self.bi: list[LineObject] = []
        self._stroke_start: Fractal | None = None
        self._provisional_bi_id: str | None = None
        self._atr_values: dict[int, float | None] = {}
        self._previous_close: int | None = None
        self._atr_seed: list[int] = []
        self._atr_current: float | None = None
        self.emitter = EventEmitter()

    def update(self, bar: RawBar) -> None:
        if self.raw_bars and bar.bar_index != self.raw_bars[-1].bar_index + 1:
            raise ValueError("raw bars must have contiguous bar_index")
        if self.raw_bars and bar.time <= self.raw_bars[-1].time:
            raise ValueError("raw bar times must be strictly increasing")
        if bar.low_i64 > bar.high_i64:
            raise ValueError("raw bar low exceeds high")
        self.raw_bars.append(bar)
        self._update_atr(bar)
        appended = self._update_inclusion(bar)
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
        self._update_centers()

    def _update_atr(self, bar: RawBar) -> None:
        previous = self._previous_close
        true_range = bar.high_i64 - bar.low_i64
        if previous is not None:
            true_range = max(
                true_range,
                abs(bar.high_i64 - previous),
                abs(bar.low_i64 - previous),
            )
        self._previous_close = bar.close_i64
        period = self.parameters.atr_period
        if self._atr_current is None:
            self._atr_seed.append(true_range)
            if len(self._atr_seed) == period:
                self._atr_current = sum(self._atr_seed) / period
        else:
            self._atr_current = ((period - 1) * self._atr_current + true_range) / period
        self._atr_values[bar.bar_index] = self._atr_current

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
            direction="unknown",
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
        if direction == "unknown":
            direction = next(
                (
                    item.direction
                    for item in reversed(self.included[:-1])
                    if item.direction != "unknown"
                ),
                self.parameters.initial_inclusion_direction,
            )
        if direction == "up":
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
        left, right = self.parameters.fractal_left, self.parameters.fractal_right
        index = len(self.included) - right - 2
        if index < left:
            return None
        center = self.included[index]
        window = self.included[index - left : index + right + 1]
        others = [*window[:left], *window[left + 1 :]]
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
        confirmed_at = self.included[index + right + 1].end_raw_index
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
        start = self._stroke_start
        if start is None:
            self._stroke_start = endpoint
            return
        if endpoint.fractal_type == start.fractal_type:
            endpoint_was_used = bool(self.bi and self.bi[-1].end.object_id == start.object_id)
            more_extreme = (
                endpoint.fractal_type == "top" and endpoint.price_i64 > start.price_i64
            ) or (endpoint.fractal_type == "bottom" and endpoint.price_i64 < start.price_i64)
            if not endpoint_was_used and more_extreme:
                self._stroke_start = endpoint
            return
        raw_span = endpoint.bar_index - start.bar_index + 1
        if raw_span < self.parameters.min_stroke_bars:
            return
        amplitude = abs(endpoint.price_i64 - start.price_i64)
        atr = self._atr_values.get(start.bar_index)
        if self.parameters.min_stroke_atr > 0 and (
            atr is None or atr <= 0 or amplitude / atr < self.parameters.min_stroke_atr
        ):
            return
        direction: Literal["up", "down"] = "up" if start.fractal_type == "bottom" else "down"
        line = LineObject(
            object_id=_stable_id("bi", start.object_id, endpoint.object_id),
            start=start,
            end=endpoint,
            direction=direction,
            confirmed_at_bar_index=endpoint.confirmed_at_bar_index,
            known_at_bar_index=endpoint.known_at_bar_index,
        )
        if self._provisional_bi_id is not None:
            self.emitter.delete(endpoint.known_at_bar_index, "bi", self._provisional_bi_id)
            self._provisional_bi_id = None
        self.bi.append(line)
        self.emitter.upsert(line.known_at_bar_index, "bi", line.object_id, line.payload())
        self._stroke_start = endpoint

    def _update_provisional_bi(self, known_at_bar_index: int) -> None:
        start = self._stroke_start
        if start is None or not self.included:
            return
        current = self.included[-1]
        upward = start.fractal_type == "bottom"
        end_price = current.high_i64 if upward else current.low_i64
        end_time = current.high_time if upward else current.low_time
        end_index = current.high_raw_index if upward else current.low_raw_index
        if end_index <= start.bar_index:
            return
        provisional_id = _stable_id("bi-provisional", start.object_id)
        if self._provisional_bi_id is not None and self._provisional_bi_id != provisional_id:
            self.emitter.delete(known_at_bar_index, "bi", self._provisional_bi_id)
        self._provisional_bi_id = provisional_id
        endpoint = Fractal(
            object_id="provisional-end",
            fractal_type="top" if upward else "bottom",
            normalized_index=current.normalized_index,
            bar_index=end_index,
            time=end_time,
            price_i64=end_price,
            confirmed_at_bar_index=known_at_bar_index,
            known_at_bar_index=known_at_bar_index,
        )
        line = LineObject(
            provisional_id,
            start,
            endpoint,
            "up" if upward else "down",
            known_at_bar_index,
            known_at_bar_index,
        )
        self.emitter.upsert(known_at_bar_index, "bi", provisional_id, line.payload(confirmed=False))

    def _update_centers(self) -> None:
        for object_id, payload, known_at in _centers(self.bi):
            self.emitter.upsert(known_at, "zhongshu", object_id, payload)

    def result_rows(self) -> dict[str, list[dict[str, Any]]]:
        return {
            "fractals": sorted(self.emitter.current("fractal"), key=lambda item: item["bar_index"]),
            "bi": sorted(self.emitter.current("bi"), key=lambda item: item["start_bar_index"]),
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
            "provisional_bi_id": self._provisional_bi_id,
            "atr_values": {str(key): value for key, value in self._atr_values.items()},
            "previous_close": self._previous_close,
            "atr_seed": self._atr_seed,
            "atr_current": self._atr_current,
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
        engine._provisional_bi_id = state["provisional_bi_id"]
        engine._atr_values = {int(key): value for key, value in state["atr_values"].items()}
        engine._previous_close = state["previous_close"]
        engine._atr_seed = [int(value) for value in state["atr_seed"]]
        engine._atr_current = state["atr_current"]
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


def _centers(lines: list[LineObject]) -> list[tuple[str, dict[str, Any], int]]:
    centers: list[tuple[str, dict[str, Any], int]] = []
    start = 0
    while start + 2 < len(lines):
        seed = lines[start : start + 3]
        low = max(min(item.start.price_i64, item.end.price_i64) for item in seed)
        high = min(max(item.start.price_i64, item.end.price_i64) for item in seed)
        if low >= high:
            start += 1
            continue
        center_id = _stable_id("zhongshu", *(item.object_id for item in seed))
        end_line = seed[-1]
        payload: dict[str, Any] = {
            "start_bar_index": seed[0].start.bar_index,
            "start_time": seed[0].start.time,
            "end_bar_index": end_line.end.bar_index,
            "end_time": end_line.end.time,
            "zg_i64": high,
            "zd_i64": low,
            "confirmed": True,
            "confirmed_at_bar_index": end_line.confirmed_at_bar_index,
            "status": "confirmed",
            "leave_direction": None,
        }
        known_at = end_line.known_at_bar_index
        cursor = start + 3
        while cursor < len(lines):
            line = lines[cursor]
            range_low, range_high = sorted((line.start.price_i64, line.end.price_i64))
            if range_high > low and range_low < high:
                payload["end_bar_index"] = line.end.bar_index
                payload["end_time"] = line.end.time
                payload["confirmed_at_bar_index"] = line.confirmed_at_bar_index
                payload["status"] = "extended"
                known_at = line.known_at_bar_index
                cursor += 1
                continue
            payload["confirmed_at_bar_index"] = line.confirmed_at_bar_index
            payload["status"] = "left"
            payload["leave_direction"] = "up" if range_low >= high else "down"
            known_at = line.known_at_bar_index
            break
        centers.append((center_id, payload, known_at))
        start = cursor - 2 if cursor < len(lines) else len(lines)
    return centers
