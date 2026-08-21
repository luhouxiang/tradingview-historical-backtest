from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from tvbt.indicators import resolve as resolve_indicator

Relation = Literal["bullish", "bearish"]
KissKind = Literal["flying", "lip", "wet"]


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


def _source_hash() -> str:
    digest = hashlib.sha256(Path(__file__).read_bytes())
    for algorithm_id in ("ma", "macd"):
        resolved = resolve_indicator(algorithm_id)
        assert resolved is not None
        digest.update(resolved[0]["source_hash"].encode())
    return "sha256:" + digest.hexdigest()


def definition() -> dict[str, Any]:
    outputs = (
        ("aux_flying_kiss", "辅助·飞吻"),
        ("aux_lip_kiss", "辅助·唇吻"),
        ("aux_wet_kiss", "辅助·湿吻"),
        ("aux_legacy_B1_candidate", "辅助·旧一买候选"),
        ("aux_legacy_B2_candidate", "辅助·旧二买候选"),
    )
    return {
        # The current public execution contract has one causal event pipeline. This
        # adapter uses it without publishing any standard or executable signal.
        "kind": "strategy",
        "algorithm_id": "aux_ma_kiss_legacy",
        "algorithm_version": "1.0.0",
        "source_hash": _source_hash(),
        "name": "辅助·均线“吻”旧系统（候选不交易）",
        "input_schema": "bars.v1",
        "parameter_schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "short_period": {
                    "type": "integer",
                    "minimum": 2,
                    "maximum": 500,
                    "default": 5,
                },
                "long_period": {
                    "type": "integer",
                    "minimum": 3,
                    "maximum": 1000,
                    "default": 10,
                },
                "proximity_ticks": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 1000,
                    "default": 1,
                },
                "flat_slope_ticks": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 1000,
                    "default": 1,
                },
                "enable_legacy_b1_macd_proxy": {"type": "boolean", "default": True},
                "macd_fast_period": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 1000,
                    "default": 12,
                },
                "macd_slow_period": {
                    "type": "integer",
                    "minimum": 2,
                    "maximum": 1000,
                    "default": 26,
                },
                "macd_signal_period": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 1000,
                    "default": 9,
                },
                "legacy_divergence_min_bars": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "default": 1,
                },
            },
            "required": [
                "short_period",
                "long_period",
                "proximity_ticks",
                "flat_slope_ticks",
                "enable_legacy_b1_macd_proxy",
                "macd_fast_period",
                "macd_slow_period",
                "macd_signal_period",
                "legacy_divergence_min_bars",
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
            for name, display_name in outputs
        ],
        "warmup": {
            "kind": "formula",
            "expression": "max(long_period - 1, macd_slow_period + macd_signal_period - 2)",
        },
        "causal": True,
    }


def _integer(parameters: dict[str, Any], name: str, minimum: int, maximum: int) -> int:
    value = parameters.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer between {minimum} and {maximum}")
    return value


@dataclass(frozen=True)
class MaKissConfig:
    short_period: int
    long_period: int
    proximity_i64: int
    flat_slope_i64: int
    enable_legacy_b1_macd_proxy: bool
    macd_fast_period: int
    macd_slow_period: int
    macd_signal_period: int
    legacy_divergence_min_bars: int

    @classmethod
    def from_parameters(cls, parameters: dict[str, Any], tick_size_i64: int) -> MaKissConfig:
        if tick_size_i64 <= 0:
            raise ValueError("tick_size_i64 must be positive")
        short_period = _integer(parameters, "short_period", 2, 500)
        long_period = _integer(parameters, "long_period", 3, 1000)
        if short_period >= long_period:
            raise ValueError("short_period must be less than long_period")
        fast_period = _integer(parameters, "macd_fast_period", 1, 1000)
        slow_period = _integer(parameters, "macd_slow_period", 2, 1000)
        if fast_period >= slow_period:
            raise ValueError("macd_fast_period must be less than macd_slow_period")
        enabled = parameters.get("enable_legacy_b1_macd_proxy")
        if not isinstance(enabled, bool):
            raise ValueError("enable_legacy_b1_macd_proxy must be a boolean")
        return cls(
            short_period=short_period,
            long_period=long_period,
            proximity_i64=_integer(parameters, "proximity_ticks", 0, 1000) * tick_size_i64,
            flat_slope_i64=_integer(parameters, "flat_slope_ticks", 0, 1000) * tick_size_i64,
            enable_legacy_b1_macd_proxy=enabled,
            macd_fast_period=fast_period,
            macd_slow_period=slow_period,
            macd_signal_period=_integer(parameters, "macd_signal_period", 1, 1000),
            legacy_divergence_min_bars=_integer(parameters, "legacy_divergence_min_bars", 1, 100),
        )


@dataclass(frozen=True)
class AuxMaKissSeries:
    short_ma: list[float | None]
    long_ma: list[float | None]
    macd_histogram: list[float | None]


def compute_ma_kiss_series(closes_i64: Sequence[int], config: MaKissConfig) -> AuxMaKissSeries:
    values = [float(value) for value in closes_i64]
    ma = resolve_indicator("ma")
    macd = resolve_indicator("macd")
    assert ma is not None and macd is not None
    short_ma = ma[1]({"close": values}, {"period": config.short_period, "source": "close"})["ma"]
    long_ma = ma[1]({"close": values}, {"period": config.long_period, "source": "close"})["ma"]
    if config.enable_legacy_b1_macd_proxy:
        macd_histogram = macd[1](
            {"close": values},
            {
                "fast_period": config.macd_fast_period,
                "slow_period": config.macd_slow_period,
                "signal_period": config.macd_signal_period,
                "source": "close",
            },
        )["histogram"]
    else:
        macd_histogram = [None] * len(values)
    return AuxMaKissSeries(short_ma, long_ma, macd_histogram)


@dataclass(frozen=True)
class AuxMaKissEvent:
    event_id: str
    event_type: str
    known_at_bar_index: int
    timestamp_utc: int
    bar_index: int
    price_i64: int
    reason_code: str
    reference_object_id: str | None
    details: dict[str, Any]


@dataclass
class _TangleEpisode:
    start_position: int
    origin_regime: Relation
    crossed: bool = False


def _raw_relation(spread: float) -> Relation | None:
    if spread > 0:
        return "bullish"
    if spread < 0:
        return "bearish"
    return None


def _clear_relation(spread: float, proximity_i64: int) -> Relation | None:
    if spread > proximity_i64:
        return "bullish"
    if spread < -proximity_i64:
        return "bearish"
    return None


def classify_ma_kisses(
    bars: Sequence[BarLike], series: AuxMaKissSeries, config: MaKissConfig
) -> list[AuxMaKissEvent]:
    """Classify causal legacy MA events without creating standard signals or orders."""
    if not (len(bars) == len(series.short_ma) == len(series.long_ma) == len(series.macd_histogram)):
        raise ValueError("bars and auxiliary series must have the same length")

    events: list[AuxMaKissEvent] = []
    regime: Relation | None = None
    regime_id = 0
    kiss_count = 0
    episode: _TangleEpisode | None = None
    flying_start: int | None = None
    previous_short: float | None = None
    last_nonzero_relation: Relation | None = None

    latest_bearish_kiss_id: str | None = None
    latest_bearish_kiss_end_bar_index: int | None = None
    latest_bearish_kiss_end_low_i64: int | None = None
    b1_reference_bar_index: int | None = None
    b1_reference_low_i64: int | None = None
    b1_reference_histogram: float | None = None
    b1_emitted = False

    common_details = {
        "catalog_algorithm_id": "ALG-AUX-001",
        "semantic_namespace": "auxiliary",
        "evidence_level": "AUXILIARY",
        "legacy_system": True,
        "standard_signal": False,
        "execution_allowed": False,
    }

    def reset_b1_context() -> None:
        nonlocal latest_bearish_kiss_id, latest_bearish_kiss_end_bar_index
        nonlocal latest_bearish_kiss_end_low_i64, b1_reference_bar_index
        nonlocal b1_reference_low_i64, b1_reference_histogram, b1_emitted
        latest_bearish_kiss_id = None
        latest_bearish_kiss_end_bar_index = None
        latest_bearish_kiss_end_low_i64 = None
        b1_reference_bar_index = None
        b1_reference_low_i64 = None
        b1_reference_histogram = None
        b1_emitted = False

    def set_regime(value: Relation) -> None:
        nonlocal regime, regime_id, kiss_count, flying_start
        if regime == value:
            return
        regime = value
        regime_id += 1
        kiss_count = 0
        flying_start = None
        reset_b1_context()

    def append_event(
        event_id: str,
        event_type: str,
        known_position: int,
        anchor_position: int,
        price_i64: int,
        reason_code: str,
        reference_object_id: str | None,
        details: dict[str, Any],
    ) -> None:
        known_bar = bars[known_position]
        anchor_bar = bars[anchor_position]
        events.append(
            AuxMaKissEvent(
                event_id=event_id,
                event_type=event_type,
                known_at_bar_index=known_bar.bar_index,
                timestamp_utc=anchor_bar.timestamp_utc,
                bar_index=anchor_bar.bar_index,
                price_i64=price_i64,
                reason_code=reason_code,
                reference_object_id=reference_object_id,
                details={**common_details, **details},
            )
        )

    def confirm_kiss(
        kind: KissKind,
        start_position: int,
        known_position: int,
        origin_regime: Relation,
        result_regime: Relation,
    ) -> None:
        nonlocal kiss_count, latest_bearish_kiss_id
        nonlocal latest_bearish_kiss_end_bar_index, latest_bearish_kiss_end_low_i64
        nonlocal b1_reference_bar_index, b1_reference_low_i64
        nonlocal b1_reference_histogram, b1_emitted
        continues_regime = origin_regime == result_regime and regime == result_regime
        if not continues_regime:
            set_regime(result_regime)
        if origin_regime == result_regime:
            kiss_count += 1
            kiss_order = kiss_count
        else:
            kiss_order = 0
        span = bars[start_position : known_position + 1]
        if origin_regime == "bullish":
            offset, anchor = min(
                enumerate(span), key=lambda item: (item[1].low_i64, item[1].bar_index)
            )
            price_i64 = anchor.low_i64
        else:
            offset, anchor = max(
                enumerate(span), key=lambda item: (item[1].high_i64, -item[1].bar_index)
            )
            price_i64 = anchor.high_i64
        anchor_position = start_position + offset
        start_bar = bars[start_position]
        end_bar = bars[known_position]
        event_id = f"AUX-MA-{kind.upper()}-{start_bar.bar_index}-{end_bar.bar_index}"
        short_ma = series.short_ma[known_position]
        long_ma = series.long_ma[known_position]
        assert short_ma is not None and long_ma is not None
        append_event(
            event_id,
            f"aux_{kind}_kiss",
            known_position,
            anchor_position,
            price_i64,
            f"AUX_MA_{kind.upper()}_KISS_CONFIRMED",
            None,
            {
                "kiss_type": kind,
                "origin_regime": origin_regime,
                "result_regime": result_regime,
                "continues_regime": origin_regime == result_regime,
                "regime_id": regime_id,
                "kiss_order": kiss_order,
                "start_bar_index": start_bar.bar_index,
                "end_bar_index": end_bar.bar_index,
                "short_ma": short_ma,
                "long_ma": long_ma,
                "ma_spread": short_ma - long_ma,
                "proximity_i64": config.proximity_i64,
                "flat_slope_i64": config.flat_slope_i64,
            },
        )
        if origin_regime == result_regime == "bearish":
            latest_bearish_kiss_id = event_id
            latest_bearish_kiss_end_bar_index = end_bar.bar_index
            latest_bearish_kiss_end_low_i64 = end_bar.low_i64
            b1_reference_bar_index = None
            b1_reference_low_i64 = None
            b1_reference_histogram = None
            b1_emitted = False
        elif origin_regime == result_regime == "bullish" and kiss_order == 1:
            candidate_id = f"AUX-MA-LEGACY-B2-{event_id}"
            append_event(
                candidate_id,
                "aux_legacy_B2_candidate",
                known_position,
                anchor_position,
                price_i64,
                "AUX_LEGACY_B2_FIRST_KISS_IN_BULLISH_REGIME",
                event_id,
                {
                    "candidate_only": True,
                    "legacy_candidate": "B2",
                    "regime_id": regime_id,
                    "kiss_order": kiss_order,
                    "kiss_type": kind,
                },
            )

    for position, bar in enumerate(bars):
        short_ma = series.short_ma[position]
        long_ma = series.long_ma[position]
        if short_ma is None or long_ma is None:
            continue
        spread = short_ma - long_ma
        raw_relation = _raw_relation(spread)
        clear_relation = _clear_relation(spread, config.proximity_i64)
        slope = None if previous_short is None else short_ma - previous_short
        previous_nonzero_relation = last_nonzero_relation
        kiss_confirmed = False

        if regime is None:
            if clear_relation is not None:
                set_regime(clear_relation)
        elif episode is not None:
            if raw_relation is not None and raw_relation != episode.origin_regime:
                episode.crossed = True
            if clear_relation is not None:
                kind: KissKind = "wet" if episode.crossed else "lip"
                confirm_kiss(
                    kind,
                    episode.start_position,
                    position,
                    episode.origin_regime,
                    clear_relation,
                )
                episode = None
                kiss_confirmed = True
        elif (
            raw_relation is not None
            and previous_nonzero_relation is not None
            and raw_relation != previous_nonzero_relation
        ):
            origin = regime
            episode = _TangleEpisode(position, origin, crossed=True)
            flying_start = None
            if clear_relation is not None:
                confirm_kiss("wet", position, position, origin, clear_relation)
                episode = None
                kiss_confirmed = True
        elif clear_relation is None:
            episode = _TangleEpisode(
                position,
                regime,
                crossed=raw_relation is not None and raw_relation != regime,
            )
            flying_start = None
        elif clear_relation != regime:
            set_regime(clear_relation)

        if not kiss_confirmed and episode is None and regime is not None and slope is not None:
            resumes = (regime == "bullish" and slope > config.flat_slope_i64) or (
                regime == "bearish" and slope < -config.flat_slope_i64
            )
            opposes = (regime == "bullish" and slope < -config.flat_slope_i64) or (
                regime == "bearish" and slope > config.flat_slope_i64
            )
            if flying_start is not None:
                if clear_relation != regime or opposes:
                    flying_start = None
                elif resumes:
                    confirm_kiss("flying", flying_start, position, regime, regime)
                    flying_start = None
                    kiss_confirmed = True
            elif clear_relation == regime and abs(slope) <= config.flat_slope_i64:
                flying_start = position

        histogram = series.macd_histogram[position]
        can_scan_b1 = (
            config.enable_legacy_b1_macd_proxy
            and latest_bearish_kiss_id is not None
            and latest_bearish_kiss_end_bar_index is not None
            and latest_bearish_kiss_end_low_i64 is not None
            and not b1_emitted
            and regime == "bearish"
            and episode is None
            and clear_relation == "bearish"
            and bar.bar_index > latest_bearish_kiss_end_bar_index
            and bar.low_i64 < latest_bearish_kiss_end_low_i64
            and histogram is not None
            and histogram < 0
        )
        if can_scan_b1:
            assert histogram is not None
            if b1_reference_low_i64 is None:
                b1_reference_bar_index = bar.bar_index
                b1_reference_low_i64 = bar.low_i64
                b1_reference_histogram = histogram
            elif bar.low_i64 < b1_reference_low_i64:
                assert b1_reference_bar_index is not None
                assert b1_reference_histogram is not None
                separated = (
                    bar.bar_index - b1_reference_bar_index >= config.legacy_divergence_min_bars
                )
                if separated and histogram > b1_reference_histogram:
                    candidate_id = (
                        f"AUX-MA-LEGACY-B1-{latest_bearish_kiss_end_bar_index}-{bar.bar_index}"
                    )
                    append_event(
                        candidate_id,
                        "aux_legacy_B1_candidate",
                        position,
                        position,
                        bar.low_i64,
                        "AUX_LEGACY_B1_LOWER_LOW_WEAKER_NEGATIVE_MACD_AFTER_LAST_KISS",
                        latest_bearish_kiss_id,
                        {
                            "candidate_only": True,
                            "legacy_candidate": "B1",
                            "regime_id": regime_id,
                            "divergence_provider": "macd_histogram",
                            "reference_bar_index": b1_reference_bar_index,
                            "reference_low_i64": b1_reference_low_i64,
                            "reference_macd_histogram": b1_reference_histogram,
                            "current_macd_histogram": histogram,
                            "legacy_divergence_min_bars": config.legacy_divergence_min_bars,
                        },
                    )
                    b1_emitted = True
                else:
                    b1_reference_bar_index = bar.bar_index
                    b1_reference_low_i64 = bar.low_i64
                    b1_reference_histogram = histogram

        previous_short = short_ma
        if raw_relation is not None:
            last_nonzero_relation = raw_relation

    return events
