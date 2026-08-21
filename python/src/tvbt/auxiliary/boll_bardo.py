from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from tvbt.chan.algorithm import definition as chan_definition
from tvbt.indicators import resolve as resolve_indicator

Direction = Literal["up", "down"]


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


class StructuralEventLike(Protocol):
    @property
    def event_seq(self) -> int: ...

    @property
    def known_at_bar_index(self) -> int: ...

    @property
    def object_type(self) -> str: ...

    @property
    def object_id(self) -> str: ...

    @property
    def operation(self) -> str: ...

    @property
    def payload_json(self) -> str: ...


def _source_hash() -> str:
    digest = hashlib.sha256(Path(__file__).read_bytes())
    boll = resolve_indicator("boll")
    assert boll is not None
    digest.update(boll[0]["source_hash"].encode())
    digest.update(chan_definition()["source_hash"].encode())
    return "sha256:" + digest.hexdigest()


def definition() -> dict[str, Any]:
    outputs = (
        ("aux_boll_superstrong_exit", "辅助·BOLL超强区退出/中阴候选"),
        ("aux_boll_second_buy_zone", "辅助·BOLL二买支撑区域"),
        ("aux_boll_second_sell_zone", "辅助·BOLL二卖阻力区域"),
        (
            "aux_boll_bardo_end_or_promotion_warning",
            "辅助·BOLL中阴结束或升级预警",
        ),
    )
    return {
        # The public execution contract currently exposes a single causal-event
        # adapter. This auxiliary implementation never emits an executable signal.
        "kind": "strategy",
        "algorithm_id": "aux_boll_bardo_warning",
        "algorithm_version": "1.0.0",
        "source_hash": _source_hash(),
        "name": "辅助·BOLL中阴判断（预警不交易）",
        "input_schema": "bars.v1",
        "parameter_schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "checkpoint_interval": {
                    "type": "integer",
                    "minimum": 64,
                    "maximum": 100_000,
                    "default": 1024,
                },
                "observation_timeframe_minutes": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 43_200,
                    "default": 30,
                },
                "level_mapping_profile_id": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 1,
                    "default": 1,
                },
                "boll_period": {
                    "type": "integer",
                    "minimum": 2,
                    "maximum": 1000,
                    "default": 20,
                },
                "boll_stddev_milli": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100_000,
                    "default": 2000,
                },
                "effective_reentry_bars": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "default": 2,
                },
                "failed_reentry_confirm_bars": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "default": 2,
                },
                "band_turn_confirm_bars": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "default": 1,
                },
                "band_turn_min_change_ticks": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 1000,
                    "default": 0,
                },
                "contraction_confirm_bars": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "default": 3,
                },
                "contraction_min_width_drop_ticks": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 1000,
                    "default": 0,
                },
            },
            "required": [
                "checkpoint_interval",
                "observation_timeframe_minutes",
                "level_mapping_profile_id",
                "boll_period",
                "boll_stddev_milli",
                "effective_reentry_bars",
                "failed_reentry_confirm_bars",
                "band_turn_confirm_bars",
                "band_turn_min_change_ticks",
                "contraction_confirm_bars",
                "contraction_min_width_drop_ticks",
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
            "expression": "max(boll_period - 1, full-history causal Chan state)",
        },
        "causal": True,
    }


def _integer(parameters: dict[str, Any], name: str, minimum: int, maximum: int) -> int:
    value = parameters.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer between {minimum} and {maximum}")
    return value


def timeframe_minutes(value: str) -> int:
    import re

    match = re.fullmatch(r"([1-9][0-9]*)(m|h|d)", value)
    if match is None:
        raise ValueError("dataset timeframe must use Nm, Nh or Nd format")
    amount = int(match.group(1))
    multiplier = {"m": 1, "h": 60, "d": 1440}[match.group(2)]
    return amount * multiplier


@dataclass(frozen=True)
class BollBardoConfig:
    checkpoint_interval: int
    observation_timeframe_minutes: int
    level_mapping_profile_id: int
    boll_period: int
    boll_standard_deviations: float
    effective_reentry_bars: int
    failed_reentry_confirm_bars: int
    band_turn_confirm_bars: int
    band_turn_min_change_i64: int
    contraction_confirm_bars: int
    contraction_min_width_drop_i64: int
    tick_size_i64: int
    dataset_timeframe: str

    @classmethod
    def from_parameters(
        cls,
        parameters: dict[str, Any],
        tick_size_i64: int,
        dataset_timeframe: str,
    ) -> BollBardoConfig:
        if tick_size_i64 <= 0:
            raise ValueError("tick_size_i64 must be positive")
        observation_timeframe = _integer(parameters, "observation_timeframe_minutes", 1, 43_200)
        if timeframe_minutes(dataset_timeframe) != observation_timeframe:
            raise ValueError("dataset timeframe must equal the fixed observation_timeframe_minutes")
        profile_id = _integer(parameters, "level_mapping_profile_id", 1, 1)
        return cls(
            checkpoint_interval=_integer(parameters, "checkpoint_interval", 64, 100_000),
            observation_timeframe_minutes=observation_timeframe,
            level_mapping_profile_id=profile_id,
            boll_period=_integer(parameters, "boll_period", 2, 1000),
            boll_standard_deviations=(
                _integer(parameters, "boll_stddev_milli", 1, 100_000) / 1000.0
            ),
            effective_reentry_bars=_integer(parameters, "effective_reentry_bars", 1, 100),
            failed_reentry_confirm_bars=_integer(parameters, "failed_reentry_confirm_bars", 1, 100),
            band_turn_confirm_bars=_integer(parameters, "band_turn_confirm_bars", 1, 100),
            band_turn_min_change_i64=(
                _integer(parameters, "band_turn_min_change_ticks", 0, 1000) * tick_size_i64
            ),
            contraction_confirm_bars=_integer(parameters, "contraction_confirm_bars", 1, 100),
            contraction_min_width_drop_i64=(
                _integer(parameters, "contraction_min_width_drop_ticks", 0, 1000) * tick_size_i64
            ),
            tick_size_i64=tick_size_i64,
            dataset_timeframe=dataset_timeframe,
        )


@dataclass(frozen=True)
class BollSeries:
    middle: list[float | None]
    upper: list[float | None]
    lower: list[float | None]


def compute_boll_series(closes_i64: Sequence[int], config: BollBardoConfig) -> BollSeries:
    resolved = resolve_indicator("boll")
    assert resolved is not None
    values = resolved[1](
        {"close": [float(value) for value in closes_i64]},
        {
            "period": config.boll_period,
            "standard_deviations": config.boll_standard_deviations,
            "source": "close",
        },
    )
    return BollSeries(values["middle"], values["upper"], values["lower"])


@dataclass(frozen=True)
class BardoContext:
    source_object_id: str
    entry_known_at_bar_index: int
    old_direction: Direction
    profile: str = "confirmed_trend_divergence_until_structural_resolution_v1"


def derive_bardo_contexts(
    bars: Sequence[BarLike], structural_events: Sequence[StructuralEventLike]
) -> list[BardoContext | None]:
    """Map confirmed preceding-layer facts to a live BARDO context without geometry scans."""
    events_by_bar: dict[int, list[StructuralEventLike]] = {}
    for event in structural_events:
        events_by_bar.setdefault(event.known_at_bar_index, []).append(event)
    current: BardoContext | None = None
    contexts: list[BardoContext | None] = []
    for bar in bars:
        for event in sorted(events_by_bar.get(bar.bar_index, []), key=lambda item: item.event_seq):
            if event.object_type == "divergence":
                if event.operation == "delete":
                    if current is not None and current.source_object_id == event.object_id:
                        current = None
                    continue
                payload = json.loads(event.payload_json)
                signal_type = payload.get("signal_type")
                qualifies = (
                    payload.get("confirmed") is True
                    and payload.get("divergence_kind") == "trend"
                    and signal_type in {"bottom_divergence", "top_divergence"}
                )
                if qualifies:
                    current = BardoContext(
                        source_object_id=event.object_id,
                        entry_known_at_bar_index=event.known_at_bar_index,
                        old_direction="down" if signal_type == "bottom_divergence" else "up",
                    )
                elif current is not None and current.source_object_id == event.object_id:
                    current = None
                continue
            if current is None or event.operation == "delete":
                continue
            payload = json.loads(event.payload_json)
            resolved_by_third_point = (
                event.object_type == "trade_point"
                and payload.get("confirmed") is True
                and payload.get("signal_class") == "standard"
                and payload.get("signal_type") in {"buy_3", "sell_3"}
            )
            resolved_by_movement = (
                event.object_type == "movement_state"
                and payload.get("confirmed") is True
                and payload.get("state_type") in {"centre_migration_up", "centre_migration_down"}
            )
            resolved_by_promotion = (
                event.object_type == "segment_zhongshu"
                and payload.get("confirmed") is True
                and int(payload.get("component_count", 0)) >= 9
            )
            if resolved_by_third_point or resolved_by_movement or resolved_by_promotion:
                current = None
        contexts.append(current)
    return contexts


@dataclass(frozen=True)
class BollAuxiliaryEvent:
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
class _SuperstrongWatch:
    direction: Direction
    reference_extreme_i64: int
    returned_inside: bool = False
    reentry_count: int = 0
    pending_position: int | None = None
    pending_price_i64: int | None = None
    failed_reentry_count: int = 0


@dataclass
class _SecondPointWatch:
    direction: Direction
    source_event_id: str
    started_position: int
    turn_count: int = 0


def _snap_to_tick(value: float, tick_size_i64: int) -> int:
    quotient = value / tick_size_i64
    rounded = math.floor(quotient + 0.5) if quotient >= 0 else math.ceil(quotient - 0.5)
    return int(rounded) * tick_size_i64


def classify_boll_bardo(
    bars: Sequence[BarLike],
    series: BollSeries,
    bardo_contexts: Sequence[BardoContext | None],
    config: BollBardoConfig,
) -> list[BollAuxiliaryEvent]:
    """Publish BOLL observations; never confirm structure, B1/S1, B2/S2, or B3/S3."""
    if not (
        len(bars)
        == len(series.middle)
        == len(series.upper)
        == len(series.lower)
        == len(bardo_contexts)
    ):
        raise ValueError("bars, BOLL series and BARDO contexts must have the same length")
    events: list[BollAuxiliaryEvent] = []
    superstrong: _SuperstrongWatch | None = None
    second_point: _SecondPointWatch | None = None
    previous_upper: float | None = None
    previous_lower: float | None = None
    previous_width: float | None = None
    contraction_count = 0
    contraction_warned = False
    previous_context_id: str | None = None
    common = {
        "catalog_algorithm_id": "ALG-AUX-003",
        "semantic_namespace": "auxiliary",
        "evidence_level": "AUXILIARY",
        "standard_signal": False,
        "execution_allowed": False,
        "opens_position": False,
        "dataset_timeframe": config.dataset_timeframe,
        "observation_timeframe_minutes": config.observation_timeframe_minutes,
        "level_mapping_profile_id": config.level_mapping_profile_id,
        "level_mapping_profile": "same_dataset_segment_bardo_v1",
        "boll_period": config.boll_period,
        "boll_standard_deviations": config.boll_standard_deviations,
    }

    def append_event(
        *,
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
            BollAuxiliaryEvent(
                event_id=event_id,
                event_type=event_type,
                known_at_bar_index=known_bar.bar_index,
                timestamp_utc=anchor_bar.timestamp_utc,
                bar_index=anchor_bar.bar_index,
                price_i64=price_i64,
                reason_code=reason_code,
                reference_object_id=reference_object_id,
                details={**common, **details},
            )
        )

    def begin_superstrong(direction: Direction, bar: BarLike) -> _SuperstrongWatch:
        extreme = bar.high_i64 if direction == "up" else bar.low_i64
        return _SuperstrongWatch(direction=direction, reference_extreme_i64=extreme)

    def confirm_superstrong_exit(watch: _SuperstrongWatch, known_position: int) -> bool:
        nonlocal second_point
        if (
            watch.pending_position is None
            or watch.pending_price_i64 is None
            or watch.failed_reentry_count < config.failed_reentry_confirm_bars
        ):
            return False
        anchor_position = watch.pending_position
        price_i64 = watch.pending_price_i64
        direction = watch.direction
        known_bar = bars[known_position]
        event_id = (
            f"AUX-BOLL-SUPERSTRONG-EXIT-{direction.upper()}-"
            f"{bars[anchor_position].bar_index}-{known_bar.bar_index}"
        )
        append_event(
            event_id=event_id,
            event_type="aux_boll_superstrong_exit",
            known_position=known_position,
            anchor_position=anchor_position,
            price_i64=price_i64,
            reason_code="AUX_BOLL_NEW_EXTREME_FAILED_EFFECTIVE_SUPERSTRONG_REENTRY",
            reference_object_id=None,
            details={
                "candidate_only": True,
                "old_superstrong_direction": direction,
                "new_extreme_i64": price_i64,
                "effective_reentry_bars": config.effective_reentry_bars,
                "failed_reentry_confirm_bars": config.failed_reentry_confirm_bars,
                "structural_bardo_confirmed": False,
            },
        )
        second_point = _SecondPointWatch(direction, event_id, known_position)
        return True

    for position, bar in enumerate(bars):
        middle = series.middle[position]
        upper = series.upper[position]
        lower = series.lower[position]
        context = bardo_contexts[position]
        if middle is None or upper is None or lower is None:
            continue
        outside: Direction | None = (
            "up" if bar.close_i64 > upper else "down" if bar.close_i64 < lower else None
        )

        if superstrong is None:
            if outside is not None:
                superstrong = begin_superstrong(outside, bar)
        elif not superstrong.returned_inside:
            if outside == superstrong.direction:
                if superstrong.direction == "up":
                    superstrong.reference_extreme_i64 = max(
                        superstrong.reference_extreme_i64, bar.high_i64
                    )
                else:
                    superstrong.reference_extreme_i64 = min(
                        superstrong.reference_extreme_i64, bar.low_i64
                    )
            elif outside is None:
                superstrong.returned_inside = True
                new_extreme = (
                    bar.high_i64 > superstrong.reference_extreme_i64
                    if superstrong.direction == "up"
                    else bar.low_i64 < superstrong.reference_extreme_i64
                )
                if new_extreme:
                    superstrong.pending_position = position
                    superstrong.pending_price_i64 = (
                        bar.high_i64 if superstrong.direction == "up" else bar.low_i64
                    )
                    superstrong.failed_reentry_count = 1
                if confirm_superstrong_exit(superstrong, position):
                    superstrong = None
            else:
                superstrong = begin_superstrong(outside, bar)
        else:
            if outside is not None and outside != superstrong.direction:
                superstrong = begin_superstrong(outside, bar)
            else:
                new_extreme = (
                    bar.high_i64 > superstrong.reference_extreme_i64
                    if superstrong.direction == "up"
                    else bar.low_i64 < superstrong.reference_extreme_i64
                )
                if new_extreme:
                    superstrong.pending_position = position
                    superstrong.pending_price_i64 = (
                        bar.high_i64 if superstrong.direction == "up" else bar.low_i64
                    )
                    superstrong.failed_reentry_count = 0 if outside is not None else 1
                elif superstrong.pending_position is not None and outside is None:
                    superstrong.failed_reentry_count += 1

                if outside == superstrong.direction:
                    superstrong.reentry_count += 1
                    superstrong.failed_reentry_count = 0
                    if superstrong.reentry_count >= config.effective_reentry_bars:
                        superstrong.reference_extreme_i64 = (
                            max(superstrong.reference_extreme_i64, bar.high_i64)
                            if superstrong.direction == "up"
                            else min(superstrong.reference_extreme_i64, bar.low_i64)
                        )
                        superstrong.returned_inside = False
                        superstrong.reentry_count = 0
                        superstrong.pending_position = None
                        superstrong.pending_price_i64 = None
                else:
                    superstrong.reentry_count = 0

                if confirm_superstrong_exit(superstrong, position):
                    superstrong = None

        if second_point is not None and position > second_point.started_position:
            if second_point.direction == "up":
                turned = (
                    previous_upper is not None
                    and previous_upper - upper > config.band_turn_min_change_i64
                )
            else:
                turned = (
                    previous_lower is not None
                    and lower - previous_lower > config.band_turn_min_change_i64
                )
            second_point.turn_count = second_point.turn_count + 1 if turned else 0
            if second_point.turn_count >= config.band_turn_confirm_bars:
                is_buy = second_point.direction == "down"
                band_value = lower if is_buy else upper
                event_type = "aux_boll_second_buy_zone" if is_buy else "aux_boll_second_sell_zone"
                event_id = f"AUX-BOLL-SECOND-{'BUY' if is_buy else 'SELL'}-ZONE-{bar.bar_index}"
                append_event(
                    event_id=event_id,
                    event_type=event_type,
                    known_position=position,
                    anchor_position=position,
                    price_i64=_snap_to_tick(band_value, config.tick_size_i64),
                    reason_code=(
                        "AUX_BOLL_LOWER_BAND_TURNED_UP_SUPPORT_ZONE"
                        if is_buy
                        else "AUX_BOLL_UPPER_BAND_TURNED_DOWN_RESISTANCE_ZONE"
                    ),
                    reference_object_id=second_point.source_event_id,
                    details={
                        "candidate_only": True,
                        "point_side": "buy" if is_buy else "sell",
                        "band": "lower" if is_buy else "upper",
                        "band_value": band_value,
                        "band_turn_confirm_bars": config.band_turn_confirm_bars,
                        "band_turn_min_change_i64": config.band_turn_min_change_i64,
                        "standard_second_point": False,
                    },
                )
                second_point = None

        width = upper - lower
        context_id = None if context is None else context.source_object_id
        if context_id != previous_context_id:
            contraction_count = 0
            contraction_warned = False
        elif context is not None and previous_width is not None:
            contracting = previous_width - width > config.contraction_min_width_drop_i64
            if contracting:
                contraction_count += 1
            else:
                contraction_count = 0
                contraction_warned = False
            if contraction_count >= config.contraction_confirm_bars and not contraction_warned:
                event_id = (
                    f"AUX-BOLL-BARDO-END-OR-PROMOTION-{context.source_object_id}-{bar.bar_index}"
                )
                append_event(
                    event_id=event_id,
                    event_type="aux_boll_bardo_end_or_promotion_warning",
                    known_position=position,
                    anchor_position=position,
                    price_i64=_snap_to_tick(middle, config.tick_size_i64),
                    reason_code="AUX_BOLL_CONTRACTION_IN_CONFIRMED_BARDO_CONTEXT",
                    reference_object_id=context.source_object_id,
                    details={
                        "warning_only": True,
                        "structural_bardo_context": True,
                        "bardo_context_profile": context.profile,
                        "bardo_entry_known_at_bar_index": context.entry_known_at_bar_index,
                        "old_movement_direction": context.old_direction,
                        "contraction_confirm_bars": config.contraction_confirm_bars,
                        "contraction_min_width_drop_i64": (config.contraction_min_width_drop_i64),
                        "band_width": width,
                        "confirms_third_point": False,
                    },
                )
                contraction_warned = True

        previous_upper = upper
        previous_lower = lower
        previous_width = width
        previous_context_id = context_id

    return events
