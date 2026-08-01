"""Causal engineering inclusion, fractal, stroke, center and platform algorithms."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
from typing import Iterable

from trading_research.contracts import (
    Bar, BarSeries, BreakoutDirection, CenterRevision, CenterStatus, ComponentRef,
    Direction, FeatureSnapshot, Fractal, FractalKind, IncludedBar, Platform,
    PlatformBreakout, Stroke, StructureManifest, StructureSnapshot, SwingLabel, SwingPoint, Timeframe,
)
from trading_research.contracts.hashing import sha256_digest


@dataclass(frozen=True, slots=True)
class EngineeringStructureParams:
    fractal_left: int = 2
    fractal_right: int = 2
    micro_fractal_left: int = 1
    micro_fractal_right: int = 1
    min_stroke_bars: int = 5
    min_stroke_atr: Decimal = Decimal("0.5")
    min_micro_stroke_bars: int = 2
    min_micro_stroke_atr: Decimal = Decimal("0")
    platform_bars: int = 12
    platform_max_width_atr: Decimal = Decimal("2.0")
    platform_min_tests: int = 2
    platform_touch_tolerance_atr: Decimal = Decimal("0.15")
    platform_break_buffer_atr: Decimal = Decimal("0.05")
    atr_feature_key: str = "atr.value"
    initial_inclusion_direction: Direction = Direction.UP

    def __post_init__(self) -> None:
        for name in ("fractal_left", "fractal_right", "micro_fractal_left", "micro_fractal_right"):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be >= 1")
        for name in ("min_stroke_bars", "min_micro_stroke_bars", "platform_bars", "platform_min_tests"):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be >= 1")
        for name in (
            "min_stroke_atr", "min_micro_stroke_atr", "platform_max_width_atr",
            "platform_touch_tolerance_atr", "platform_break_buffer_atr",
        ):
            value = getattr(self, name)
            if not isinstance(value, Decimal) or value < 0:
                raise ValueError(f"{name} must be a non-negative Decimal")


@dataclass(slots=True)
class _Included:
    start: int
    end: int
    high: Decimal
    low: Decimal
    high_time: datetime
    low_time: datetime
    confirm_time: datetime
    direction: Direction
    raw_indices: list[int]


def _stable_id(prefix: str, *parts: object) -> str:
    return f"{prefix}_{sha256_digest(parts)[:20]}"


class EngineeringStructureEngine:
    version = "1.0.0"
    manifests = (
        StructureManifest(
            ComponentRef("bar_inclusion.engineering", version, sha256_digest({"schema": 1})),
            "Engineering Bar Inclusion", ("BarSeries",), ("IncludedBar",),
            ("initial_inclusion_direction",),
        ),
        StructureManifest(
            ComponentRef("fractal.window.engineering", version, sha256_digest({"schema": 1})),
            "Sealed Window Fractal", ("IncludedBar",), ("Fractal", "SwingPoint"),
            ("fractal_left", "fractal_right", "micro_fractal_left", "micro_fractal_right"),
        ),
        StructureManifest(
            ComponentRef("stroke.engineering", version, sha256_digest({"schema": 1})),
            "Engineering Stroke and Micro Stroke", ("Fractal", "atr.value"), ("Stroke",),
            ("min_stroke_bars", "min_stroke_atr", "min_micro_stroke_bars", "min_micro_stroke_atr"),
        ),
        StructureManifest(
            ComponentRef("center.overlap.engineering", version, sha256_digest({"schema": 1})),
            "Engineering Three-Stroke Center", ("Stroke",), ("CenterRevision",), (),
        ),
        StructureManifest(
            ComponentRef("platform.rolling.engineering", version, sha256_digest({"schema": 1})),
            "Rolling ATR Platform", ("BarSeries", "atr.value"), ("Platform", "PlatformBreakout"),
            ("platform_bars", "platform_max_width_atr", "platform_min_tests", "platform_touch_tolerance_atr", "platform_break_buffer_atr"),
        ),
    )

    def compute_batch(
        self,
        bars: BarSeries,
        features: FeatureSnapshot | None = None,
        params: EngineeringStructureParams | None = None,
    ) -> StructureSnapshot:
        if features is not None and features.data_hash != bars.data_hash:
            raise ValueError("feature and bar data hashes must match")
        runtime = self.initialize(bars.data_hash, bars.timeframe, features, params)
        for bar in bars.bars:
            runtime.update(bar)
        return runtime.snapshot()

    def initialize(
        self,
        data_hash: str,
        timeframe: Timeframe,
        features: FeatureSnapshot | None = None,
        params: EngineeringStructureParams | None = None,
    ) -> StructureRuntime:
        return StructureRuntime(data_hash, timeframe, features, params or EngineeringStructureParams())


class StructureRuntime:
    def __init__(
        self,
        data_hash: str,
        timeframe: Timeframe,
        features: FeatureSnapshot | None,
        params: EngineeringStructureParams,
    ) -> None:
        self.data_hash = data_hash
        self.timeframe = timeframe
        self.features = features
        self.params = params
        self._bars: list[Bar] = []

    def update(self, bar: Bar) -> None:
        if bar.timeframe != self.timeframe:
            raise ValueError("streaming bar timeframe changed")
        if self._bars and bar.open_time <= self._bars[-1].open_time:
            raise ValueError("streaming bars must be strictly increasing")
        self._bars.append(bar)

    def snapshot(self) -> StructureSnapshot:
        complete = [(index, bar) for index, bar in enumerate(self._bars) if bar.is_complete]
        included = self._inclusion(complete)
        fractals = self._fractals(included, self.params.fractal_left, self.params.fractal_right, "fx")
        micro_fractals = self._fractals(
            included, self.params.micro_fractal_left, self.params.micro_fractal_right, "mfx"
        )
        strokes = self._strokes(
            fractals, included, "main", self.params.min_stroke_bars, self.params.min_stroke_atr
        )
        micro_strokes = self._strokes(
            micro_fractals, included, "micro", self.params.min_micro_stroke_bars,
            self.params.min_micro_stroke_atr,
        )
        swings = self._swings(fractals)
        centers = self._centers(strokes)
        platforms, breakouts = self._platforms(complete)
        parameter_hash = sha256_digest(asdict(self.params))
        components = tuple(
            ComponentRef(manifest.component.id, manifest.component.version, parameter_hash)
            for manifest in EngineeringStructureEngine.manifests
        )
        warnings = () if self.features is not None else ("ATR feature snapshot absent; ATR-gated structures may be unavailable",)
        return StructureSnapshot(
            self.data_hash, components, self.timeframe, tuple(included), tuple(fractals),
            tuple(micro_fractals), tuple(swings), tuple(strokes), tuple(micro_strokes),
            tuple(centers), tuple(platforms), tuple(breakouts), warnings,
        )

    def _inclusion(self, complete: list[tuple[int, Bar]]) -> list[IncludedBar]:
        merged: list[_Included] = []
        for raw_index, bar in complete:
            current = _Included(
                raw_index, raw_index, bar.high, bar.low, bar.close_time, bar.close_time,
                bar.close_time, Direction.UNKNOWN, [raw_index],
            )
            if not merged:
                merged.append(current)
                continue
            previous = merged[-1]
            if current.high > previous.high and current.low > previous.low:
                current.direction = Direction.UP
                merged.append(current)
            elif current.high < previous.high and current.low < previous.low:
                current.direction = Direction.DOWN
                merged.append(current)
            else:
                direction = previous.direction
                if direction is Direction.UNKNOWN:
                    direction = next(
                        (item.direction for item in reversed(merged[:-1]) if item.direction is not Direction.UNKNOWN),
                        self.params.initial_inclusion_direction,
                    )
                if direction is Direction.UP:
                    high = max(previous.high, current.high)
                    low = max(previous.low, current.low)
                    high_time = current.high_time if current.high > previous.high else previous.high_time
                    low_time = current.low_time if current.low > previous.low else previous.low_time
                else:
                    high = min(previous.high, current.high)
                    low = min(previous.low, current.low)
                    high_time = current.high_time if current.high < previous.high else previous.high_time
                    low_time = current.low_time if current.low < previous.low else previous.low_time
                merged[-1] = _Included(
                    previous.start, raw_index, high, low, high_time, low_time, bar.close_time,
                    direction, previous.raw_indices + [raw_index],
                )
        return [
            IncludedBar(
                index, item.start, item.end, item.high, item.low, item.high_time, item.low_time,
                item.confirm_time, item.direction, tuple(item.raw_indices), index == len(merged) - 1,
            )
            for index, item in enumerate(merged)
        ]

    @staticmethod
    def _fractals(
        included: list[IncludedBar], left: int, right: int, prefix: str
    ) -> list[Fractal]:
        result = []
        # The final included bar is provisional. A right context is usable only after a later
        # independent bar seals it, hence the extra bar in the upper bound and confirmation time.
        for index in range(left, len(included) - right - 1):
            center = included[index]
            window = included[index - left:index + right + 1]
            others = window[:left] + window[left + 1:]
            top = all(center.high > item.high and center.low > item.low for item in others)
            bottom = all(center.high < item.high and center.low < item.low for item in others)
            if not top and not bottom:
                continue
            kind = FractalKind.TOP if top else FractalKind.BOTTOM
            price = center.high if top else center.low
            pivot_time = center.high_time if top else center.low_time
            strength = (
                center.high - max(item.high for item in others)
                if top else min(item.low for item in others) - center.low
            )
            confirm_time = included[index + right + 1].confirm_time
            sources = tuple(sorted({raw for item in window for raw in item.source_raw_indices}))
            result.append(
                Fractal(
                    _stable_id(prefix, kind.value, pivot_time, confirm_time, price), kind, index,
                    pivot_time, confirm_time, confirm_time, price, strength, left, right, sources,
                )
            )
        return result

    def _atr(self, at_time: datetime) -> Decimal | None:
        if self.features is None:
            return None
        return self.features.get_value(self.params.atr_feature_key, at_time)

    def _strokes(
        self,
        fractals: list[Fractal],
        included: list[IncludedBar],
        level: str,
        minimum_bars: int,
        minimum_atr: Decimal,
    ) -> list[Stroke]:
        if not fractals:
            return []
        start = fractals[0]
        result: list[Stroke] = []
        for endpoint in fractals[1:]:
            if endpoint.kind is start.kind:
                # Once a stroke has used its endpoint, never rewrite that historical endpoint.
                if not result or result[-1].end_pivot.fractal_id != start.fractal_id:
                    if (endpoint.kind is FractalKind.TOP and endpoint.price > start.price) or (
                        endpoint.kind is FractalKind.BOTTOM and endpoint.price < start.price
                    ):
                        start = endpoint
                continue
            duration = endpoint.normalized_index - start.normalized_index
            amplitude = abs(endpoint.price - start.price)
            atr = self._atr(start.pivot_time)
            amplitude_atr = amplitude / atr if atr is not None and atr > 0 else None
            if duration + 1 < minimum_bars:
                continue
            if minimum_atr > 0 and (amplitude_atr is None or amplitude_atr < minimum_atr):
                continue
            direction = Direction.UP if start.kind is FractalKind.BOTTOM else Direction.DOWN
            raw_start = included[start.normalized_index].start_raw_index
            raw_end = included[endpoint.normalized_index].end_raw_index
            path = [bar.close for bar in self._bars[raw_start:raw_end + 1] if bar.is_complete]
            path_distance = sum((abs(current - previous) for previous, current in zip(path, path[1:])), Decimal(0))
            efficiency = amplitude / path_distance if path_distance > 0 else Decimal(1)
            result.append(
                Stroke(
                    _stable_id("stroke", level, start.fractal_id, endpoint.fractal_id), level,
                    direction, start, endpoint, start.price, endpoint.price, amplitude,
                    amplitude_atr, duration + 1, amplitude / Decimal(max(duration, 1)), efficiency,
                    endpoint.pivot_time, endpoint.confirm_time, endpoint.available_from,
                )
            )
            start = endpoint
        return result

    @staticmethod
    def _swings(fractals: list[Fractal]) -> list[SwingPoint]:
        previous: dict[FractalKind, Decimal] = {}
        counts: dict[FractalKind, int] = {FractalKind.TOP: 0, FractalKind.BOTTOM: 0}
        result = []
        for fractal in fractals:
            counts[fractal.kind] += 1
            if fractal.kind not in previous:
                label = SwingLabel.FIRST_HIGH if fractal.kind is FractalKind.TOP else SwingLabel.FIRST_LOW
            elif fractal.kind is FractalKind.TOP:
                label = SwingLabel.HH if fractal.price > previous[fractal.kind] else SwingLabel.LH
            else:
                label = SwingLabel.LL if fractal.price < previous[fractal.kind] else SwingLabel.HL
            previous[fractal.kind] = fractal.price
            result.append(SwingPoint(fractal, label, counts[fractal.kind]))
        return result

    @staticmethod
    def _centers(strokes: list[Stroke]) -> list[CenterRevision]:
        revisions: list[CenterRevision] = []
        start = 0
        while start + 2 < len(strokes):
            seed = strokes[start:start + 3]
            low = max(min(item.start_price, item.end_price) for item in seed)
            high = min(max(item.start_price, item.end_price) for item in seed)
            if low >= high:
                start += 1
                continue
            center_id = _stable_id("center", *(item.stroke_id for item in seed))
            source_ids = [item.stroke_id for item in seed]
            revision = 0
            revisions.append(
                CenterRevision(
                    center_id, revision, seed[0].level, seed[0].start_pivot.pivot_time,
                    seed[0].start_pivot.pivot_time, seed[-1].confirm_time,
                    seed[-1].available_from, seed[-1].end_pivot.pivot_time, low, high,
                    (low + high) / Decimal(2), tuple(source_ids), CenterStatus.CONFIRMED,
                )
            )
            cursor = start + 3
            while cursor < len(strokes):
                stroke = strokes[cursor]
                range_low, range_high = sorted((stroke.start_price, stroke.end_price))
                revision += 1
                if range_high > low and range_low < high:
                    source_ids.append(stroke.stroke_id)
                    revisions.append(
                        CenterRevision(
                            center_id, revision, stroke.level, seed[0].start_pivot.pivot_time,
                            stroke.end_pivot.pivot_time, stroke.confirm_time, stroke.available_from,
                            stroke.end_pivot.pivot_time, low, high, (low + high) / Decimal(2),
                            tuple(source_ids), CenterStatus.EXTENDED,
                        )
                    )
                    cursor += 1
                    continue
                leave = BreakoutDirection.UP if range_low >= high else BreakoutDirection.DOWN
                revisions.append(
                    CenterRevision(
                        center_id, revision, stroke.level, seed[0].start_pivot.pivot_time,
                        stroke.start_pivot.pivot_time, stroke.confirm_time, stroke.available_from,
                        strokes[cursor - 1].end_pivot.pivot_time, low, high,
                        (low + high) / Decimal(2), tuple(source_ids), CenterStatus.LEFT, leave,
                    )
                )
                break
            start = cursor - 2 if cursor < len(strokes) else len(strokes)
        return revisions

    def _platforms(
        self, complete: list[tuple[int, Bar]]
    ) -> tuple[list[Platform], list[PlatformBreakout]]:
        platforms: list[Platform] = []
        breakouts: list[PlatformBreakout] = []
        active: Platform | None = None
        for position, (raw_index, bar) in enumerate(complete):
            atr = self._atr(bar.close_time)
            if active is not None and atr is not None:
                buffer = self.params.platform_break_buffer_atr * atr
                direction = None
                boundary = None
                if bar.close > active.high + buffer:
                    direction, boundary = BreakoutDirection.UP, active.high
                elif bar.close < active.low - buffer:
                    direction, boundary = BreakoutDirection.DOWN, active.low
                if direction is not None:
                    breakouts.append(
                        PlatformBreakout(
                            active.platform_id, direction, bar.close_time, bar.close_time,
                            bar.close_time, bar.close, boundary,
                        )
                    )
                    active = None
                continue
            if active is not None or position + 1 < self.params.platform_bars or atr is None or atr <= 0:
                continue
            window = complete[position + 1 - self.params.platform_bars:position + 1]
            high = max(item.high for _, item in window)
            low = min(item.low for _, item in window)
            # A flat window has no price area and cannot form a valid platform.
            # Skip it before constructing the immutable contract rather than
            # failing the entire causal replay.
            if high <= low:
                continue
            width_atr = (high - low) / atr
            if width_atr > self.params.platform_max_width_atr:
                continue
            tolerance = self.params.platform_touch_tolerance_atr * atr
            high_tests = sum(item.high >= high - tolerance for _, item in window)
            low_tests = sum(item.low <= low + tolerance for _, item in window)
            if min(high_tests, low_tests) < self.params.platform_min_tests:
                continue
            closes = [item.close for _, item in window]
            slope = self._slope(closes)
            raw_indices = tuple(index for index, _ in window)
            start_time = window[0][1].close_time
            active = Platform(
                _stable_id("platform", start_time, bar.close_time, low, high), start_time,
                start_time, bar.close_time, bar.close_time, low, high,
                (low + high) / Decimal(2), len(window), width_atr, high_tests, low_tests,
                slope, raw_indices,
            )
            platforms.append(active)
        return platforms, breakouts

    @staticmethod
    def _slope(values: Iterable[Decimal]) -> Decimal:
        items = tuple(values)
        count = len(items)
        if count < 2:
            return Decimal(0)
        x_mean = Decimal(count - 1) / Decimal(2)
        y_mean = sum(items, Decimal(0)) / Decimal(count)
        numerator = sum((Decimal(i) - x_mean) * (value - y_mean) for i, value in enumerate(items))
        denominator = sum((Decimal(i) - x_mean) ** 2 for i in range(count))
        return numerator / denominator
