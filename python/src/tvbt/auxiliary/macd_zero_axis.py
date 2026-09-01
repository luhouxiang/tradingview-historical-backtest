from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from tvbt.indicators import resolve as resolve_indicator


class BarLike(Protocol):
    @property
    def bar_index(self) -> int: ...

    @property
    def timestamp_utc(self) -> int: ...

    @property
    def close_i64(self) -> int: ...


def _source_hash() -> str:
    digest = hashlib.sha256(Path(__file__).read_bytes())
    resolved = resolve_indicator("macd")
    assert resolved is not None
    digest.update(resolved[0]["source_hash"].encode())
    return "sha256:" + digest.hexdigest()


def definition() -> dict[str, Any]:
    return {
        # The public contract currently has a single causal event adapter. No
        # strategy state or trade signal is emitted by this auxiliary risk filter.
        "kind": "strategy",
        "algorithm_id": "aux_macd_zero_axis_defense",
        "algorithm_version": "1.0.0",
        "source_hash": _source_hash(),
        "name": "辅助·MACD零轴防守（风险开关不交易）",
        "input_schema": "bars.v1",
        "parameter_schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "minimum_timeframe_minutes": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 43_200,
                    "default": 60,
                },
                "fast_period": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 1000,
                    "default": 12,
                },
                "slow_period": {
                    "type": "integer",
                    "minimum": 2,
                    "maximum": 1000,
                    "default": 26,
                },
                "signal_period": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 1000,
                    "default": 9,
                },
                "zero_axis_buffer_ticks": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 1000,
                    "default": 0,
                },
                "risk_off_confirm_bars": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "default": 1,
                },
                "reclaim_confirm_bars": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "default": 2,
                },
            },
            "required": [
                "minimum_timeframe_minutes",
                "fast_period",
                "slow_period",
                "signal_period",
                "zero_axis_buffer_ticks",
                "risk_off_confirm_bars",
                "reclaim_confirm_bars",
            ],
        },
        "outputs": [
            {
                "name": name,
                "display_name": display_name,
                "pane": "main",
                "series_type": "semantic_objects",
                "object_type": "chart_event",
            }
            for name, display_name in (
                ("aux_macd_risk_off", "辅助·MACD零轴下防守"),
                ("aux_macd_risk_on_candidate", "辅助·MACD重新站稳候选"),
            )
        ],
        "warmup": {
            "kind": "formula",
            "expression": "slow_period + signal_period - 2",
        },
        "causal": True,
    }


def _integer(parameters: dict[str, Any], name: str, minimum: int, maximum: int) -> int:
    value = parameters.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer between {minimum} and {maximum}")
    return value


def timeframe_minutes(value: str) -> int:
    match = re.fullmatch(r"([1-9][0-9]*)(m|h|d)", value)
    if match is None:
        raise ValueError("dataset timeframe must use Nm, Nh or Nd format")
    amount = int(match.group(1))
    multiplier = {"m": 1, "h": 60, "d": 1440}[match.group(2)]
    return amount * multiplier


@dataclass(frozen=True)
class MacdZeroAxisConfig:
    minimum_timeframe_minutes: int
    fast_period: int
    slow_period: int
    signal_period: int
    zero_axis_buffer_i64: int
    risk_off_confirm_bars: int
    reclaim_confirm_bars: int
    dataset_timeframe: str

    @classmethod
    def from_parameters(
        cls,
        parameters: dict[str, Any],
        tick_size_i64: int,
        dataset_timeframe: str,
    ) -> MacdZeroAxisConfig:
        if tick_size_i64 <= 0:
            raise ValueError("tick_size_i64 must be positive")
        minimum_timeframe = _integer(parameters, "minimum_timeframe_minutes", 1, 43_200)
        actual_timeframe = timeframe_minutes(dataset_timeframe)
        if actual_timeframe != minimum_timeframe:
            raise ValueError("dataset timeframe must equal the fixed minimum_timeframe_minutes")
        fast_period = _integer(parameters, "fast_period", 1, 1000)
        slow_period = _integer(parameters, "slow_period", 2, 1000)
        if fast_period >= slow_period:
            raise ValueError("fast_period must be less than slow_period")
        return cls(
            minimum_timeframe_minutes=minimum_timeframe,
            fast_period=fast_period,
            slow_period=slow_period,
            signal_period=_integer(parameters, "signal_period", 1, 1000),
            zero_axis_buffer_i64=_integer(parameters, "zero_axis_buffer_ticks", 0, 1000)
            * tick_size_i64,
            risk_off_confirm_bars=_integer(parameters, "risk_off_confirm_bars", 1, 100),
            reclaim_confirm_bars=_integer(parameters, "reclaim_confirm_bars", 1, 100),
            dataset_timeframe=dataset_timeframe,
        )


@dataclass(frozen=True)
class MacdZeroAxisSeries:
    diff: list[float | None]
    dea: list[float | None]


def compute_macd_zero_axis_series(
    closes_i64: Sequence[int], config: MacdZeroAxisConfig
) -> MacdZeroAxisSeries:
    resolved = resolve_indicator("macd")
    assert resolved is not None
    values = resolved[1](
        {"close": [float(value) for value in closes_i64]},
        {
            "fast_period": config.fast_period,
            "slow_period": config.slow_period,
            "signal_period": config.signal_period,
            "source": "close",
        },
    )
    return MacdZeroAxisSeries(values["macd"], values["signal"])


@dataclass(frozen=True)
class MacdDirectionalRegime:
    direction: str
    bullish_count: int
    bearish_count: int


def classify_macd_directional_regimes(
    series: MacdZeroAxisSeries,
    config: MacdZeroAxisConfig,
) -> list[MacdDirectionalRegime]:
    """Classify a causal, non-structural MACD direction at every input bar.

    A bullish regime requires DIFF and DEA to remain strictly above the positive
    buffer for ``reclaim_confirm_bars`` consecutive bars. A bearish regime is the
    exact mirror using ``risk_off_confirm_bars``. Equality, a missing value, or
    only one line crossing the boundary resets that direction's counter.

    The returned regime is auxiliary evidence only. Callers must still obtain an
    independently confirmed structural signal before they may trade.
    """
    if len(series.diff) != len(series.dea):
        raise ValueError("MACD DIFF and DEA series must have the same length")
    bullish_count = 0
    bearish_count = 0
    buffer = float(config.zero_axis_buffer_i64)
    regimes: list[MacdDirectionalRegime] = []
    for diff, dea in zip(series.diff, series.dea, strict=True):
        if diff is None or dea is None:
            bullish_count = 0
            bearish_count = 0
        else:
            bullish_count = bullish_count + 1 if diff > buffer and dea > buffer else 0
            bearish_count = bearish_count + 1 if diff < -buffer and dea < -buffer else 0
        direction = "neutral"
        if bullish_count >= config.reclaim_confirm_bars:
            direction = "bullish"
        elif bearish_count >= config.risk_off_confirm_bars:
            direction = "bearish"
        regimes.append(
            MacdDirectionalRegime(
                direction=direction,
                bullish_count=bullish_count,
                bearish_count=bearish_count,
            )
        )
    return regimes


@dataclass(frozen=True)
class MacdRiskEvent:
    event_id: str
    event_type: str
    known_at_bar_index: int
    timestamp_utc: int
    bar_index: int
    price_i64: int
    reason_code: str
    details: dict[str, Any]


def classify_macd_zero_axis(
    bars: Sequence[BarLike],
    series: MacdZeroAxisSeries,
    config: MacdZeroAxisConfig,
) -> list[MacdRiskEvent]:
    """Publish a causal risk gate; never create structural or executable signals."""
    if not (len(bars) == len(series.diff) == len(series.dea)):
        raise ValueError("bars and MACD series must have the same length")
    events: list[MacdRiskEvent] = []
    risk_off = False
    below_count = 0
    reclaim_count = 0
    buffer = float(config.zero_axis_buffer_i64)
    common = {
        "catalog_algorithm_id": "ALG-AUX-002",
        "semantic_namespace": "auxiliary",
        "evidence_level": "AUXILIARY",
        "risk_filter": True,
        "standard_signal": False,
        "execution_allowed": False,
        "opens_position": False,
        "dataset_timeframe": config.dataset_timeframe,
        "minimum_timeframe_minutes": config.minimum_timeframe_minutes,
        "zero_axis_buffer_i64": config.zero_axis_buffer_i64,
    }

    for position, bar in enumerate(bars):
        diff = series.diff[position]
        dea = series.dea[position]
        if diff is None or dea is None:
            continue
        both_below = diff < -buffer and dea < -buffer
        both_above = diff > buffer and dea > buffer
        if not risk_off:
            reclaim_count = 0
            below_count = below_count + 1 if both_below else 0
            if below_count < config.risk_off_confirm_bars:
                continue
            risk_off = True
            below_count = 0
            event_id = f"AUX-MACD-RISK-OFF-{bar.bar_index}"
            events.append(
                MacdRiskEvent(
                    event_id=event_id,
                    event_type="aux_macd_risk_off",
                    known_at_bar_index=bar.bar_index,
                    timestamp_utc=bar.timestamp_utc,
                    bar_index=bar.bar_index,
                    price_i64=bar.close_i64,
                    reason_code="AUX_MACD_DIFF_AND_DEA_CONFIRMED_BELOW_ZERO_AXIS",
                    details={
                        **common,
                        "risk_filter_active": True,
                        "max_participation_multiplier": 0.0,
                        "diff": diff,
                        "dea": dea,
                        "confirm_bars": config.risk_off_confirm_bars,
                    },
                )
            )
            continue

        below_count = 0
        reclaim_count = reclaim_count + 1 if both_above else 0
        if reclaim_count < config.reclaim_confirm_bars:
            continue
        risk_off = False
        reclaim_count = 0
        event_id = f"AUX-MACD-RISK-ON-CANDIDATE-{bar.bar_index}"
        events.append(
            MacdRiskEvent(
                event_id=event_id,
                event_type="aux_macd_risk_on_candidate",
                known_at_bar_index=bar.bar_index,
                timestamp_utc=bar.timestamp_utc,
                bar_index=bar.bar_index,
                price_i64=bar.close_i64,
                reason_code="AUX_MACD_DIFF_AND_DEA_CONFIRMED_ABOVE_ZERO_AXIS",
                details={
                    **common,
                    "risk_filter_active": False,
                    "max_participation_multiplier": 1.0,
                    "candidate_only": True,
                    "diff": diff,
                    "dea": dea,
                    "confirm_bars": config.reclaim_confirm_bars,
                },
            )
        )

    return events
