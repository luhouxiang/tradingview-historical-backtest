from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from tvbt.auxiliary.ma_sector_rotation import DEFAULT_MA_PERIODS
from tvbt.indicators import resolve as resolve_indicator


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


Direction = Literal["up", "down"]


def definition() -> dict[str, Any]:
    ma = resolve_indicator("ma")
    assert ma is not None
    digest = hashlib.sha256(Path(__file__).read_bytes())
    digest.update(ma[0]["source_hash"].encode())
    periods = {
        f"ma_period_{index}": {
            "type": "integer",
            "minimum": 1,
            "maximum": 10_000,
            "default": period,
        }
        for index, period in enumerate(DEFAULT_MA_PERIODS, 1)
    }
    return {
        "kind": "strategy",
        "algorithm_id": "aux_single_instrument_ma_observation",
        "algorithm_version": "1.0.0",
        "source_hash": "sha256:" + digest.hexdigest(),
        "name": "辅助·单标的 MA 等级与压制一致性（不交易）",
        "input_schema": "bars.v1",
        "parameter_schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "episode_start_bar_index": {"type": "integer", "minimum": 0},
                "observation_direction": {
                    "type": "string",
                    "enum": ["up", "down"],
                    "default": "up",
                },
                "pressure_confirmation_bars": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "default": 3,
                },
                **periods,
            },
            "required": [
                "episode_start_bar_index",
                "observation_direction",
                "pressure_confirmation_bars",
                *periods,
            ],
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
                ("aux_single_instrument_ma_level", "辅助·单标的 MA 攻克等级"),
                ("aux_ma_pressure_consistency", "辅助·受压均线一致性"),
            )
        ],
        "warmup": {"kind": "formula", "expression": "max(ma_period_1..ma_period_8)"},
        "causal": True,
    }


@dataclass(frozen=True)
class SingleMaConfig:
    episode_start_bar_index: int
    observation_direction: Direction
    pressure_confirmation_bars: int
    ma_periods: tuple[int, ...]

    @classmethod
    def from_parameters(cls, parameters: dict[str, Any]) -> SingleMaConfig:
        start = parameters.get("episode_start_bar_index")
        horizon = parameters.get("pressure_confirmation_bars")
        direction = parameters.get("observation_direction")
        if isinstance(start, bool) or not isinstance(start, int) or start < 0:
            raise ValueError("episode_start_bar_index must be a non-negative integer")
        if isinstance(horizon, bool) or not isinstance(horizon, int) or not 1 <= horizon <= 100:
            raise ValueError("pressure_confirmation_bars must be between 1 and 100")
        if direction not in {"up", "down"}:
            raise ValueError("observation_direction must be up or down")
        periods: list[int] = []
        for index in range(1, 9):
            value = parameters.get(f"ma_period_{index}")
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 10_000:
                raise ValueError(f"ma_period_{index} must be between 1 and 10000")
            periods.append(value)
        if periods != sorted(set(periods)):
            raise ValueError("ma periods must be strictly increasing")
        return cls(start, direction, horizon, tuple(periods))


@dataclass(frozen=True)
class SingleMaEvent:
    event_id: str
    event_type: str
    bar_index: int
    timestamp_utc: int
    known_at_bar_index: int
    price_i64: int
    details: dict[str, Any]


def compute_ma_ladder(
    closes_i64: Sequence[int], periods: Sequence[int]
) -> list[list[float | None]]:
    ma = resolve_indicator("ma")
    assert ma is not None
    values = [float(value) for value in closes_i64]
    return [
        ma[1]({"close": values}, {"period": period, "source": "close"})["ma"] for period in periods
    ]


def classify_single_instrument_ma(
    bars: Sequence[BarLike],
    ladder: Sequence[Sequence[float | None]],
    config: SingleMaConfig,
) -> list[SingleMaEvent]:
    if len(ladder) != len(config.ma_periods) or any(len(values) != len(bars) for values in ladder):
        raise ValueError("MA ladder dimensions do not match bars and periods")
    result: list[SingleMaEvent] = []
    conquered = [False] * len(config.ma_periods)
    last_level = 0
    pending: list[tuple[int, int, float]] = []
    first_pressure_period: int | None = None
    common = {
        "catalog_algorithm_id": "ALG-AUX-MA-SINGLE-001",
        "semantic_namespace": "auxiliary",
        "evidence_level": "HEURISTIC",
        "standard_signal": False,
        "execution_allowed": False,
    }
    for position, bar in enumerate(bars):
        if bar.bar_index < config.episode_start_bar_index:
            continue
        for ordinal, values in enumerate(ladder):
            current = values[position]
            if current is None:
                continue
            conquered_now = (
                bar.close_i64 > current
                if config.observation_direction == "up"
                else bar.close_i64 < current
            )
            conquered[ordinal] = conquered[ordinal] or conquered_now
            if bar.low_i64 <= current <= bar.high_i64:
                pending.append((position, ordinal, current))
        level = sum(conquered)
        if level != last_level:
            last_level = level
            result.append(
                SingleMaEvent(
                    f"AUX-MA-LEVEL-{config.episode_start_bar_index}-{bar.bar_index}",
                    "aux_single_instrument_ma_level",
                    bar.bar_index,
                    bar.timestamp_utc,
                    bar.bar_index,
                    bar.close_i64,
                    {
                        **common,
                        "profile": "single_instrument_close_strict_conquest_v1",
                        "episode_start_bar_index": config.episode_start_bar_index,
                        "direction": config.observation_direction,
                        "conquered_count": level,
                        "conquered_periods": [
                            period
                            for period, value in zip(config.ma_periods, conquered, strict=True)
                            if value
                        ],
                        "equality_is_conquered": False,
                    },
                )
            )
        remaining: list[tuple[int, int, float]] = []
        for touch_position, ordinal, touched_ma in pending:
            if position < touch_position + config.pressure_confirmation_bars:
                remaining.append((touch_position, ordinal, touched_ma))
                continue
            pressure_confirmed = (
                bar.close_i64 < touched_ma
                if config.observation_direction == "up"
                else bar.close_i64 > touched_ma
            )
            if not pressure_confirmed:
                continue
            period = config.ma_periods[ordinal]
            same = first_pressure_period is None or first_pressure_period == period
            if first_pressure_period is None:
                first_pressure_period = period
            touch_bar = bars[touch_position]
            result.append(
                SingleMaEvent(
                    f"AUX-MA-PRESSURE-{touch_bar.bar_index}-{period}-{bar.bar_index}",
                    "aux_ma_pressure_consistency",
                    touch_bar.bar_index,
                    touch_bar.timestamp_utc,
                    bar.bar_index,
                    touch_bar.close_i64,
                    {
                        **common,
                        "profile": "range_touch_then_close_rejection_v1",
                        "direction": config.observation_direction,
                        "ma_period": period,
                        "ma_value": touched_ma,
                        "confirmation_bars": config.pressure_confirmation_bars,
                        "first_pressure_period": first_pressure_period,
                        "same_as_first_pressure": same,
                    },
                )
            )
        pending = remaining
    return result
