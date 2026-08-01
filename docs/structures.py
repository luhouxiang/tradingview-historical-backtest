"""Immutable causal market-structure contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import IntEnum, StrEnum
from uuid import NAMESPACE_URL, UUID, uuid5

from trading_research.contracts.core import ComponentRef, Timeframe, _aware_utc
from trading_research.contracts.hashing import sha256_digest
from trading_research.version import CONTRACT_VERSION


class Direction(IntEnum):
    DOWN = -1
    UNKNOWN = 0
    UP = 1


class FractalKind(StrEnum):
    TOP = "top"
    BOTTOM = "bottom"


class SwingLabel(StrEnum):
    HH = "HH"
    HL = "HL"
    LH = "LH"
    LL = "LL"
    FIRST_HIGH = "FIRST_HIGH"
    FIRST_LOW = "FIRST_LOW"


class CenterStatus(StrEnum):
    CONFIRMED = "confirmed"
    EXTENDED = "extended"
    LEFT = "left"


class BreakoutDirection(StrEnum):
    UP = "up"
    DOWN = "down"


def _normalize_causal_times(instance: object, *names: str) -> None:
    for name in names:
        object.__setattr__(instance, name, _aware_utc(getattr(instance, name), name))


def _validate_causal(pivot_time: datetime, confirm_time: datetime, available_from: datetime) -> None:
    if confirm_time < pivot_time:
        raise ValueError("confirm_time cannot precede pivot_time")
    if available_from < confirm_time:
        raise ValueError("available_from cannot precede confirm_time")


@dataclass(frozen=True, slots=True)
class StructureManifest:
    component: ComponentRef
    display_name: str
    input_types: tuple[str, ...]
    output_types: tuple[str, ...]
    parameter_names: tuple[str, ...]
    supports_batch: bool = True
    supports_stream: bool = True
    deterministic: bool = True
    causal: bool = True


@dataclass(frozen=True, slots=True)
class IncludedBar:
    normalized_index: int
    start_raw_index: int
    end_raw_index: int
    high: Decimal
    low: Decimal
    high_time: datetime
    low_time: datetime
    confirm_time: datetime
    direction: Direction
    source_raw_indices: tuple[int, ...]
    is_provisional: bool

    def __post_init__(self) -> None:
        _normalize_causal_times(self, "high_time", "low_time", "confirm_time")
        if self.start_raw_index > self.end_raw_index or self.low > self.high:
            raise ValueError("invalid included-bar range")
        if max(self.high_time, self.low_time) > self.confirm_time:
            raise ValueError("included-bar extreme cannot occur after confirmation")


@dataclass(frozen=True, slots=True)
class Fractal:
    fractal_id: str
    kind: FractalKind
    normalized_index: int
    pivot_time: datetime
    confirm_time: datetime
    available_from: datetime
    price: Decimal
    strength: Decimal
    window_left: int
    window_right: int
    source_raw_indices: tuple[int, ...]
    is_provisional: bool = False

    def __post_init__(self) -> None:
        _normalize_causal_times(self, "pivot_time", "confirm_time", "available_from")
        _validate_causal(self.pivot_time, self.confirm_time, self.available_from)


@dataclass(frozen=True, slots=True)
class SwingPoint:
    fractal: Fractal
    label: SwingLabel
    same_kind_sequence: int


@dataclass(frozen=True, slots=True)
class Stroke:
    stroke_id: str
    level: str
    direction: Direction
    start_pivot: Fractal
    end_pivot: Fractal
    start_price: Decimal
    end_price: Decimal
    amplitude: Decimal
    amplitude_atr: Decimal | None
    duration_bars: int
    speed: Decimal
    efficiency: Decimal
    pivot_time: datetime
    confirm_time: datetime
    available_from: datetime

    def __post_init__(self) -> None:
        _normalize_causal_times(self, "pivot_time", "confirm_time", "available_from")
        _validate_causal(self.pivot_time, self.confirm_time, self.available_from)
        if self.direction is Direction.UNKNOWN or self.amplitude < 0 or self.duration_bars < 1:
            raise ValueError("invalid stroke")


@dataclass(frozen=True, slots=True)
class CenterRevision:
    center_id: str
    revision: int
    level: str
    start_time: datetime
    pivot_time: datetime
    confirm_time: datetime
    available_from: datetime
    end_time: datetime
    low: Decimal
    high: Decimal
    mid: Decimal
    source_stroke_ids: tuple[str, ...]
    status: CenterStatus
    leave_direction: BreakoutDirection | None = None

    def __post_init__(self) -> None:
        _normalize_causal_times(
            self, "start_time", "pivot_time", "confirm_time", "available_from", "end_time"
        )
        _validate_causal(self.pivot_time, self.confirm_time, self.available_from)
        if self.low >= self.high or self.start_time > self.end_time:
            raise ValueError("invalid center range")


@dataclass(frozen=True, slots=True)
class Platform:
    platform_id: str
    start_time: datetime
    pivot_time: datetime
    confirm_time: datetime
    available_from: datetime
    low: Decimal
    high: Decimal
    mid: Decimal
    bar_count: int
    width_atr: Decimal
    test_count_high: int
    test_count_low: int
    slope: Decimal
    source_raw_indices: tuple[int, ...]

    def __post_init__(self) -> None:
        _normalize_causal_times(self, "start_time", "pivot_time", "confirm_time", "available_from")
        _validate_causal(self.pivot_time, self.confirm_time, self.available_from)
        if self.low >= self.high or self.bar_count < 1:
            raise ValueError("invalid platform")


@dataclass(frozen=True, slots=True)
class PlatformBreakout:
    platform_id: str
    direction: BreakoutDirection
    pivot_time: datetime
    confirm_time: datetime
    available_from: datetime
    close: Decimal
    boundary: Decimal

    def __post_init__(self) -> None:
        _normalize_causal_times(self, "pivot_time", "confirm_time", "available_from")
        _validate_causal(self.pivot_time, self.confirm_time, self.available_from)


@dataclass(frozen=True, slots=True)
class LevelStructureState:
    timeframe: Timeframe
    as_of: datetime
    direction: Direction
    last_high_label: SwingLabel | None
    last_low_label: SwingLabel | None
    center_migration: Direction
    confirmed_stroke_count: int

    def __post_init__(self) -> None:
        _normalize_causal_times(self, "as_of")


@dataclass(frozen=True, slots=True)
class MultiLevelStructureState:
    levels: tuple[LevelStructureState, ...]
    trend_vector: tuple[int, ...]
    available_from: datetime

    def __post_init__(self) -> None:
        _normalize_causal_times(self, "available_from")
        if len(self.levels) != len(self.trend_vector):
            raise ValueError("trend vector must align with levels")


@dataclass(frozen=True, slots=True)
class StructureSnapshot:
    data_hash: str
    components: tuple[ComponentRef, ...]
    timeframe: Timeframe
    included_bars: tuple[IncludedBar, ...]
    fractals: tuple[Fractal, ...]
    micro_fractals: tuple[Fractal, ...]
    swings: tuple[SwingPoint, ...]
    strokes: tuple[Stroke, ...]
    micro_strokes: tuple[Stroke, ...]
    centers: tuple[CenterRevision, ...]
    platforms: tuple[Platform, ...]
    platform_breakouts: tuple[PlatformBreakout, ...]
    warnings: tuple[str, ...] = ()
    contract_version: str = CONTRACT_VERSION
    structure_hash: str = field(init=False)
    structure_snapshot_id: UUID = field(init=False)

    def __post_init__(self) -> None:
        digest = sha256_digest({
            "contract_version": self.contract_version, "data_hash": self.data_hash,
            "components": self.components, "timeframe": self.timeframe,
            "included_bars": self.included_bars, "fractals": self.fractals,
            "micro_fractals": self.micro_fractals, "swings": self.swings,
            "strokes": self.strokes, "micro_strokes": self.micro_strokes,
            "centers": self.centers, "platforms": self.platforms,
            "platform_breakouts": self.platform_breakouts, "warnings": self.warnings,
        })
        object.__setattr__(self, "structure_hash", digest)
        object.__setattr__(self, "structure_snapshot_id", uuid5(NAMESPACE_URL, digest))
