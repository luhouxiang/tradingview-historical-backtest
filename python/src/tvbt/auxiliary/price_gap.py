from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol


class BarLike(Protocol):
    @property
    def bar_index(self) -> int: ...

    @property
    def timestamp_utc(self) -> int: ...

    @property
    def high_i64(self) -> int: ...

    @property
    def low_i64(self) -> int: ...

    @property
    def close_i64(self) -> int: ...


GapDirection = Literal["up", "down"]


def definition() -> dict[str, Any]:
    return {
        "kind": "strategy",
        "algorithm_id": "aux_price_gap_lifecycle",
        "algorithm_version": "1.0.0",
        "source_hash": "sha256:" + hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "name": "辅助·价格缺口生命周期（不交易）",
        "input_schema": "bars.v1",
        "parameter_schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {},
            "required": [],
        },
        "outputs": [
            {
                "name": name,
                "display_name": label,
                "pane": "main",
                "series_type": "semantic_objects",
                "object_type": "chart_event",
            }
            for name, label in (
                ("aux_price_gap_formed", "辅助·缺口形成"),
                ("aux_price_gap_partially_filled", "辅助·缺口部分回补"),
                ("aux_price_gap_filled", "辅助·缺口完全回补"),
                ("aux_price_gap_unknown", "辅助·缺口状态未知"),
            )
        ],
        "warmup": {"kind": "fixed_bars", "bars": 1},
        "causal": True,
    }


@dataclass(frozen=True)
class PriceGapEvent:
    event_id: str
    event_type: str
    gap_id: str
    direction: GapDirection
    bar_index: int
    timestamp_utc: int
    known_at_bar_index: int
    price_i64: int
    lower_i64: int
    upper_i64: int
    fill_extreme_i64: int
    reason_code: str

    def details(self) -> dict[str, Any]:
        return {
            "gap_id": self.gap_id,
            "direction": self.direction,
            "lower_i64": self.lower_i64,
            "upper_i64": self.upper_i64,
            "width_i64": self.upper_i64 - self.lower_i64,
            "fill_extreme_i64": self.fill_extreme_i64,
            "gap_profile": "adjacent_raw_ohlc_positive_width_v1",
            "catalog_algorithm_id": "ALG-AUX-GAP-001",
            "semantic_namespace": "auxiliary",
            "evidence_level": "AUXILIARY",
            "standard_signal": False,
            "execution_allowed": False,
        }


@dataclass
class _ActiveGap:
    gap_id: str
    direction: GapDirection
    lower_i64: int
    upper_i64: int
    fill_extreme_i64: int
    partial_emitted: bool = False


def classify_price_gaps(bars: Sequence[BarLike]) -> list[PriceGapEvent]:
    """Track positive-width adjacent OHLC gaps without filling missing bars."""
    result: list[PriceGapEvent] = []
    active: list[_ActiveGap] = []
    previous: BarLike | None = None
    for bar in bars:
        if previous is not None and bar.bar_index != previous.bar_index + 1:
            for gap in active:
                result.append(
                    PriceGapEvent(
                        f"{gap.gap_id}-unknown-{bar.bar_index}",
                        "aux_price_gap_unknown",
                        gap.gap_id,
                        gap.direction,
                        bar.bar_index,
                        bar.timestamp_utc,
                        bar.bar_index,
                        bar.close_i64,
                        gap.lower_i64,
                        gap.upper_i64,
                        gap.fill_extreme_i64,
                        "AUX_PRICE_GAP_INPUT_DISCONTINUITY",
                    )
                )
            active.clear()
            previous = bar
            continue

        survivors: list[_ActiveGap] = []
        for gap in active:
            if gap.direction == "up":
                gap.fill_extreme_i64 = min(gap.fill_extreme_i64, bar.low_i64)
                complete = gap.fill_extreme_i64 <= gap.lower_i64
                partial = gap.fill_extreme_i64 < gap.upper_i64
            else:
                gap.fill_extreme_i64 = max(gap.fill_extreme_i64, bar.high_i64)
                complete = gap.fill_extreme_i64 >= gap.upper_i64
                partial = gap.fill_extreme_i64 > gap.lower_i64
            if complete:
                event_type = "aux_price_gap_filled"
                reason = "AUX_PRICE_GAP_OPPOSITE_BOUNDARY_REACHED"
            elif partial and not gap.partial_emitted:
                event_type = "aux_price_gap_partially_filled"
                reason = "AUX_PRICE_GAP_INTERIOR_ENTERED"
                gap.partial_emitted = True
            else:
                survivors.append(gap)
                continue
            result.append(
                PriceGapEvent(
                    f"{gap.gap_id}-{event_type}-{bar.bar_index}",
                    event_type,
                    gap.gap_id,
                    gap.direction,
                    bar.bar_index,
                    bar.timestamp_utc,
                    bar.bar_index,
                    gap.fill_extreme_i64,
                    gap.lower_i64,
                    gap.upper_i64,
                    gap.fill_extreme_i64,
                    reason,
                )
            )
            if not complete:
                survivors.append(gap)
        active = survivors

        if previous is not None:
            if bar.low_i64 > previous.high_i64:
                direction: GapDirection = "up"
                lower, upper = previous.high_i64, bar.low_i64
                fill_extreme = bar.low_i64
            elif bar.high_i64 < previous.low_i64:
                direction = "down"
                lower, upper = bar.high_i64, previous.low_i64
                fill_extreme = bar.high_i64
            else:
                previous = bar
                continue
            gap_id = f"AUX-GAP-{direction.upper()}-{previous.bar_index}-{bar.bar_index}"
            active.append(_ActiveGap(gap_id, direction, lower, upper, fill_extreme))
            result.append(
                PriceGapEvent(
                    f"{gap_id}-formed",
                    "aux_price_gap_formed",
                    gap_id,
                    direction,
                    bar.bar_index,
                    bar.timestamp_utc,
                    bar.bar_index,
                    upper if direction == "up" else lower,
                    lower,
                    upper,
                    fill_extreme,
                    "AUX_PRICE_GAP_POSITIVE_WIDTH_FORMED",
                )
            )
        previous = bar
    return result
