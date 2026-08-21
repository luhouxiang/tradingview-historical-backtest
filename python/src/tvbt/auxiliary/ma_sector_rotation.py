from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import Any, Literal

from tvbt.chan.algorithm import definition as chan_definition
from tvbt.indicators.algorithms import resolve

EventOperation = Literal["upsert", "delete"]
AdjustmentMode = Literal["forward_adjusted", "back_adjusted", "total_return"]

DEFAULT_MA_PERIODS = (5, 13, 21, 34, 55, 89, 144, 233)
_SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


def _source_hash() -> str:
    ma = resolve("ma")
    assert ma is not None
    digest = hashlib.sha256(Path(__file__).read_bytes())
    digest.update(ma[0]["source_hash"].encode())
    digest.update(chan_definition()["source_hash"].encode())
    return "sha256:" + digest.hexdigest()


def definition() -> dict[str, Any]:
    period_properties = {
        f"ma_period_{ordinal}": {
            "type": "integer",
            "minimum": 1,
            "maximum": 10_000,
            "default": period,
        }
        for ordinal, period in enumerate(DEFAULT_MA_PERIODS, 1)
    }
    return {
        "kind": "strategy",
        "algorithm_id": "aux_ma_sector_rotation",
        "algorithm_version": "1.0.0",
        "source_hash": _source_hash(),
        "name": "经验·均线等级与板块轮动（不交易）",
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
                **period_properties,
                "minimum_sector_coverage_milli": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 1000,
                    "default": 800,
                },
                "capacity_lookback_bars": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 250,
                    "default": 20,
                },
                "minimum_average_volume": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 9_000_000_000_000_000_000,
                    "default": 0,
                },
                "maximum_rotation_candidates": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 500,
                    "default": 20,
                },
            },
            "required": [
                "checkpoint_interval",
                *period_properties,
                "minimum_sector_coverage_milli",
                "capacity_lookback_bars",
                "minimum_average_volume",
                "maximum_rotation_candidates",
            ],
        },
        "outputs": [
            {
                "name": "aux_ma_strength_class",
                "display_name": "经验·标的均线等级",
                "pane": "main",
                "series_type": "semantic_objects",
                "object_type": "chart_event",
            },
            {
                "name": "aux_sector_strength_mean",
                "display_name": "经验·板块平均等级",
                "pane": "indicator",
                "series_type": "semantic_objects",
                "object_type": "chart_event",
            },
            {
                "name": "aux_rotation_candidate",
                "display_name": "经验·板块轮动候选",
                "pane": "main",
                "series_type": "semantic_objects",
                "object_type": "chart_event",
            },
        ],
        "warmup": {
            "kind": "formula",
            "expression": "max(ma_period_1..ma_period_8) and explicit episode availability",
        },
        "causal": True,
    }


def _integer(parameters: dict[str, Any], name: str, minimum: int, maximum: int) -> int:
    value = parameters.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer between {minimum} and {maximum}")
    return value


@dataclass(frozen=True)
class MaSectorRotationConfig:
    checkpoint_interval: int
    ma_periods: tuple[int, ...]
    minimum_sector_coverage_milli: int
    capacity_lookback_bars: int
    minimum_average_volume: int
    maximum_rotation_candidates: int

    @classmethod
    def from_parameters(cls, parameters: dict[str, Any]) -> MaSectorRotationConfig:
        periods = tuple(
            _integer(parameters, f"ma_period_{ordinal}", 1, 10_000) for ordinal in range(1, 9)
        )
        if any(left >= right for left, right in pairwise(periods)):
            raise ValueError("ma periods must be strictly increasing")
        return cls(
            checkpoint_interval=_integer(parameters, "checkpoint_interval", 64, 100_000),
            ma_periods=periods,
            minimum_sector_coverage_milli=_integer(
                parameters, "minimum_sector_coverage_milli", 1, 1000
            ),
            capacity_lookback_bars=_integer(parameters, "capacity_lookback_bars", 1, 250),
            minimum_average_volume=_integer(
                parameters, "minimum_average_volume", 0, 9_000_000_000_000_000_000
            ),
            maximum_rotation_candidates=_integer(parameters, "maximum_rotation_candidates", 1, 500),
        )


@dataclass(frozen=True)
class RankingMembership:
    dataset_id: str
    data_revision: str
    sector_id: str
    effective_from_utc: int
    effective_to_utc: int | None
    available_at_utc: int

    def active_at(self, timestamp_utc: int) -> bool:
        return (
            self.available_at_utc <= timestamp_utc
            and self.effective_from_utc <= timestamp_utc
            and (self.effective_to_utc is None or timestamp_utc < self.effective_to_utc)
        )


@dataclass(frozen=True)
class RankingContext:
    universe_id: str
    membership_revision: str
    membership_mode: str
    price_adjustment_mode: AdjustmentMode
    price_adjustment_revision: str
    episode_id: str
    episode_start_timestamp_utc: int
    episode_available_at_utc: int
    memberships: tuple[RankingMembership, ...]

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> RankingContext:
        required_strings = (
            "universe_id",
            "membership_revision",
            "membership_mode",
            "price_adjustment_mode",
            "price_adjustment_revision",
            "episode_id",
        )
        if any(
            not isinstance(payload.get(name), str) or not payload[name] for name in required_strings
        ):
            raise ValueError("ranking context string fields are required")
        if payload["membership_mode"] != "point_in_time":
            raise ValueError("ranking context requires point_in_time membership")
        if not _SHA256_PATTERN.fullmatch(
            payload["membership_revision"]
        ) or not _SHA256_PATTERN.fullmatch(payload["price_adjustment_revision"]):
            raise ValueError("ranking context revisions must be sha256 values")
        adjustment_mode = payload["price_adjustment_mode"]
        if adjustment_mode not in {"forward_adjusted", "back_adjusted", "total_return"}:
            raise ValueError("ranking context requires a supported adjusted-price mode")
        start = payload.get("episode_start_timestamp_utc")
        available = payload.get("episode_available_at_utc")
        if (
            isinstance(start, bool)
            or not isinstance(start, int)
            or start < 0
            or isinstance(available, bool)
            or not isinstance(available, int)
            or available < start
        ):
            raise ValueError("episode availability must not precede its start")
        raw_memberships = payload.get("memberships")
        if not isinstance(raw_memberships, list) or len(raw_memberships) < 2:
            raise ValueError("ranking context requires at least two memberships")
        memberships: list[RankingMembership] = []
        for raw in raw_memberships:
            if not isinstance(raw, dict):
                raise ValueError("ranking memberships must be objects")
            effective_to = raw.get("effective_to_utc")
            membership = RankingMembership(
                dataset_id=str(raw.get("dataset_id", "")),
                data_revision=str(raw.get("data_revision", "")),
                sector_id=str(raw.get("sector_id", "")),
                effective_from_utc=_required_non_negative_int(raw, "effective_from_utc"),
                effective_to_utc=(
                    None
                    if effective_to is None
                    else _required_non_negative_int(raw, "effective_to_utc")
                ),
                available_at_utc=_required_non_negative_int(raw, "available_at_utc"),
            )
            if (
                not membership.dataset_id
                or not _SHA256_PATTERN.fullmatch(membership.data_revision)
                or not membership.sector_id
            ):
                raise ValueError("ranking membership identity fields are required")
            if (
                membership.effective_to_utc is not None
                and membership.effective_to_utc <= membership.effective_from_utc
            ):
                raise ValueError("ranking membership effective interval is empty")
            memberships.append(membership)
        if len({membership.dataset_id for membership in memberships}) < 2:
            raise ValueError("ranking context requires at least two unique datasets")
        _validate_non_overlapping_memberships(memberships)
        return cls(
            universe_id=payload["universe_id"],
            membership_revision=payload["membership_revision"],
            membership_mode=payload["membership_mode"],
            price_adjustment_mode=adjustment_mode,
            price_adjustment_revision=payload["price_adjustment_revision"],
            episode_id=payload["episode_id"],
            episode_start_timestamp_utc=start,
            episode_available_at_utc=available,
            memberships=tuple(memberships),
        )


def _required_non_negative_int(payload: dict[str, Any], name: str) -> int:
    value = payload.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _validate_non_overlapping_memberships(memberships: Sequence[RankingMembership]) -> None:
    by_dataset: dict[str, list[RankingMembership]] = {}
    for membership in memberships:
        by_dataset.setdefault(membership.dataset_id, []).append(membership)
    for values in by_dataset.values():
        ordered = sorted(values, key=lambda item: item.effective_from_utc)
        for left, right in pairwise(ordered):
            if left.effective_to_utc is None or left.effective_to_utc > right.effective_from_utc:
                raise ValueError("ranking membership intervals must not overlap per dataset")


@dataclass(frozen=True)
class RankingBar:
    bar_index: int
    timestamp_utc: int
    close_i64: int
    volume: int | None


@dataclass(frozen=True)
class RankingInstrument:
    dataset_id: str
    data_revision: str
    bars: tuple[RankingBar, ...]


@dataclass(frozen=True)
class DivergenceUpdate:
    dataset_id: str
    object_id: str
    operation: EventOperation
    known_timestamp_utc: int
    signal_type: str | None = None
    divergence_kind: str | None = None
    source_bar_index: int | None = None
    source_timestamp_utc: int | None = None
    source_price_i64: int | None = None


@dataclass(frozen=True)
class MaSectorRotationEvent:
    operation: EventOperation
    event_id: str
    event_type: str
    known_at_bar_index: int
    timestamp_utc: int
    bar_index: int
    price_i64: int
    reason_code: str
    details: dict[str, Any]


def classify_ma_sector_rotation(
    *,
    anchor_dataset_id: str,
    instruments: Sequence[RankingInstrument],
    context: RankingContext,
    config: MaSectorRotationConfig,
    divergence_updates: Sequence[DivergenceUpdate] = (),
) -> list[MaSectorRotationEvent]:
    instrument_by_id = {instrument.dataset_id: instrument for instrument in instruments}
    if len(instrument_by_id) != len(instruments) or anchor_dataset_id not in instrument_by_id:
        raise ValueError("ranking instruments must be unique and include the anchor dataset")
    membership_ids = {membership.dataset_id for membership in context.memberships}
    if membership_ids != set(instrument_by_id):
        raise ValueError("ranking instruments must exactly match membership datasets")
    for membership in context.memberships:
        if instrument_by_id[membership.dataset_id].data_revision != membership.data_revision:
            raise ValueError("ranking instrument revision does not match membership")
    anchor = instrument_by_id[anchor_dataset_id]
    _validate_bars(instruments)
    classes, bars_by_timestamp = _instrument_classes(instruments, context, config)
    anchor_bars = [
        bar for bar in anchor.bars if bar.timestamp_utc >= context.episode_available_at_utc
    ]
    updates = sorted(
        divergence_updates,
        key=lambda item: (
            item.known_timestamp_utc,
            item.dataset_id,
            item.object_id,
            item.operation,
        ),
    )
    update_position = 0
    active_divergences: dict[tuple[str, str], DivergenceUpdate] = {}
    candidate_ids_by_source: dict[tuple[str, str], set[str]] = {}
    previous_class: dict[str, int] = {}
    events: list[MaSectorRotationEvent] = []

    for anchor_bar in anchor_bars:
        timestamp = anchor_bar.timestamp_utc
        active_memberships = {
            membership.dataset_id: membership
            for membership in context.memberships
            if membership.active_at(timestamp)
        }
        for inactive_dataset_id in set(previous_class) - set(active_memberships):
            previous_class.pop(inactive_dataset_id)
        sector_means = _sector_means(
            timestamp, active_memberships, classes, config.minimum_sector_coverage_milli
        )
        for dataset_id in sorted(active_memberships):
            strength_class = classes[dataset_id].get(timestamp)
            member_bar = bars_by_timestamp[dataset_id].get(timestamp)
            if strength_class is None or member_bar is None:
                continue
            if previous_class.get(dataset_id) != strength_class:
                membership = active_memberships[dataset_id]
                events.append(
                    _event(
                        event_id=(f"AUX-MA-CLASS-{context.episode_id}-{dataset_id}-{timestamp}"),
                        event_type="aux_ma_strength_class",
                        known_bar=anchor_bar,
                        anchor_bar=member_bar,
                        reason_code="AUX_MA_SMALLEST_UNCONQUERED_PERIOD_CHANGED",
                        details={
                            **_common(context, config),
                            "chart_dataset_id": dataset_id,
                            "instrument_dataset_id": dataset_id,
                            "sector_id": membership.sector_id,
                            "instrument_strength_class": strength_class,
                            "previous_instrument_strength_class": previous_class.get(dataset_id),
                            "display_label": f"均线等级 {strength_class}",
                            "classification_detail": (
                                f"{dataset_id} 当前反弹均线等级 {strength_class}；"
                                "严格收盘站上才算攻克，候选语义不允许交易"
                            ),
                        },
                    )
                )
                previous_class[dataset_id] = strength_class
        for sector_id in sorted(sector_means):
            mean = sector_means[sector_id]
            events.append(
                _event(
                    event_id=f"AUX-SECTOR-MEAN-{context.episode_id}-{sector_id}-{timestamp}",
                    event_type="aux_sector_strength_mean",
                    known_bar=anchor_bar,
                    anchor_bar=anchor_bar,
                    reason_code="AUX_SECTOR_POINT_IN_TIME_COVERAGE_ACCEPTED",
                    details={
                        **_common(context, config),
                        "chart_dataset_id": anchor_dataset_id,
                        "sector_id": sector_id,
                        "sector_strength_class_sum": mean[0],
                        "sector_strength_class_count": mean[1],
                        "sector_member_count": mean[2],
                        "sector_coverage_milli": mean[3],
                        "sector_strength_mean_milli": mean[4],
                        "display_label": f"{sector_id} {mean[4] / 1000:.3f}",
                        "classification_detail": (
                            f"板块 {sector_id} 点时平均等级 {mean[4] / 1000:.3f}；"
                            f"覆盖 {mean[1]}/{mean[2]}"
                        ),
                    },
                )
            )

        due_updates: list[DivergenceUpdate] = []
        while (
            update_position < len(updates)
            and updates[update_position].known_timestamp_utc <= timestamp
        ):
            due_updates.append(updates[update_position])
            update_position += 1
        due_top_divergences: list[DivergenceUpdate] = []
        for update in due_updates:
            source_key = (update.dataset_id, update.object_id)
            if update.operation == "delete":
                active_divergences.pop(source_key, None)
                for event_id in sorted(candidate_ids_by_source.pop(source_key, set())):
                    events.append(
                        _delete_event(event_id, anchor_bar, "AUX_SOURCE_DIVERGENCE_INVALIDATED")
                    )
                continue
            active_divergences[source_key] = update
            if update.divergence_kind != "trend" or update.signal_type != "top_divergence":
                continue
            due_top_divergences.append(update)
        for update in due_top_divergences:
            source_key = (update.dataset_id, update.object_id)
            generated = _rotation_candidates(
                update,
                anchor_bar,
                active_memberships,
                sector_means,
                active_divergences,
                classes,
                bars_by_timestamp,
                context,
                config,
            )
            next_ids = {event.event_id for event in generated}
            for event_id in sorted(candidate_ids_by_source.get(source_key, set()) - next_ids):
                events.append(_delete_event(event_id, anchor_bar, "AUX_SOURCE_REVISION_REPLACED"))
            events.extend(generated)
            candidate_ids_by_source[source_key] = next_ids
    return events


def _validate_bars(instruments: Sequence[RankingInstrument]) -> None:
    for instrument in instruments:
        previous_timestamp = -1
        previous_bar_index = -1
        for bar in instrument.bars:
            if bar.timestamp_utc <= previous_timestamp or bar.bar_index <= previous_bar_index:
                raise ValueError("ranking bars must have increasing timestamp and bar_index")
            if bar.volume is not None and bar.volume < 0:
                raise ValueError("ranking volume must not be negative")
            previous_timestamp = bar.timestamp_utc
            previous_bar_index = bar.bar_index


def _instrument_classes(
    instruments: Sequence[RankingInstrument],
    context: RankingContext,
    config: MaSectorRotationConfig,
) -> tuple[dict[str, dict[int, int]], dict[str, dict[int, RankingBar]]]:
    ma = resolve("ma")
    assert ma is not None
    compute = ma[1]
    classes: dict[str, dict[int, int]] = {}
    bars_by_timestamp: dict[str, dict[int, RankingBar]] = {}
    for instrument in instruments:
        closes = [float(bar.close_i64) for bar in instrument.bars]
        ma_values = [
            compute({"close": closes}, {"period": period, "source": "close"})["ma"]
            for period in config.ma_periods
        ]
        conquered = [False] * len(config.ma_periods)
        values: dict[int, int] = {}
        by_timestamp = {bar.timestamp_utc: bar for bar in instrument.bars}
        for position, bar in enumerate(instrument.bars):
            current_mas = [series[position] for series in ma_values]
            if bar.timestamp_utc >= context.episode_start_timestamp_utc:
                for ordinal, current in enumerate(current_mas):
                    if current is not None and bar.close_i64 > current:
                        conquered[ordinal] = True
            if bar.timestamp_utc >= context.episode_available_at_utc and all(
                current is not None for current in current_mas
            ):
                values[bar.timestamp_utc] = next(
                    (ordinal for ordinal, value in enumerate(conquered, 1) if not value),
                    len(config.ma_periods) + 1,
                )
        classes[instrument.dataset_id] = values
        bars_by_timestamp[instrument.dataset_id] = by_timestamp
    return classes, bars_by_timestamp


def _sector_means(
    timestamp: int,
    active_memberships: dict[str, RankingMembership],
    classes: dict[str, dict[int, int]],
    minimum_coverage_milli: int,
) -> dict[str, tuple[int, int, int, int, int]]:
    member_ids: dict[str, list[str]] = {}
    for dataset_id, membership in active_memberships.items():
        member_ids.setdefault(membership.sector_id, []).append(dataset_id)
    result: dict[str, tuple[int, int, int, int, int]] = {}
    for sector_id, datasets in member_ids.items():
        observed = [classes[dataset_id].get(timestamp) for dataset_id in datasets]
        available = [value for value in observed if value is not None]
        member_count = len(datasets)
        if not available or len(available) * 1000 < member_count * minimum_coverage_milli:
            continue
        class_sum = sum(available)
        coverage_milli = len(available) * 1000 // member_count
        mean_milli = (class_sum * 1000 + len(available) // 2) // len(available)
        result[sector_id] = (
            class_sum,
            len(available),
            member_count,
            coverage_milli,
            mean_milli,
        )
    return result


def _rotation_candidates(
    source: DivergenceUpdate,
    anchor_bar: RankingBar,
    active_memberships: dict[str, RankingMembership],
    sector_means: dict[str, tuple[int, int, int, int, int]],
    active_divergences: dict[tuple[str, str], DivergenceUpdate],
    classes: dict[str, dict[int, int]],
    bars_by_timestamp: dict[str, dict[int, RankingBar]],
    context: RankingContext,
    config: MaSectorRotationConfig,
) -> list[MaSectorRotationEvent]:
    timestamp = anchor_bar.timestamp_utc
    source_membership = active_memberships.get(source.dataset_id)
    source_class = classes.get(source.dataset_id, {}).get(timestamp)
    source_bar = bars_by_timestamp.get(source.dataset_id, {}).get(timestamp)
    if source_membership is None or source_class is None or source_bar is None:
        return []
    ranked: list[tuple[tuple[int, int, int, str], dict[str, Any], RankingBar]] = []
    for dataset_id, membership in active_memberships.items():
        if dataset_id == source.dataset_id or membership.sector_id not in sector_means:
            continue
        strength_class = classes[dataset_id].get(timestamp)
        candidate_bar = bars_by_timestamp[dataset_id].get(timestamp)
        if strength_class is None or candidate_bar is None:
            continue
        latest = max(
            (
                update
                for (active_dataset_id, _), update in active_divergences.items()
                if active_dataset_id == dataset_id and update.divergence_kind == "trend"
            ),
            key=lambda item: (item.known_timestamp_utc, item.object_id),
            default=None,
        )
        adjusted_ready = latest is not None and latest.signal_type == "bottom_divergence"
        catch_up = strength_class < source_class
        if not adjusted_ready and not catch_up:
            continue
        instrument_bars = [
            bar for bar in bars_by_timestamp[dataset_id].values() if bar.timestamp_utc <= timestamp
        ][-config.capacity_lookback_bars :]
        if len(instrument_bars) < config.capacity_lookback_bars or any(
            bar.volume is None for bar in instrument_bars
        ):
            continue
        volume_sum = sum(int(bar.volume) for bar in instrument_bars if bar.volume is not None)
        if volume_sum < config.minimum_average_volume * config.capacity_lookback_bars:
            continue
        sector_mean = sector_means[membership.sector_id][4]
        details = {
            "candidate_dataset_id": dataset_id,
            "candidate_sector_id": membership.sector_id,
            "candidate_instrument_strength_class": strength_class,
            "candidate_sector_strength_mean_milli": sector_mean,
            "source_dataset_id": source.dataset_id,
            "source_sector_id": source_membership.sector_id,
            "source_instrument_strength_class": source_class,
            "source_divergence_object_id": source.object_id,
            "source_divergence_bar_index": source.source_bar_index,
            "source_divergence_timestamp_utc": source.source_timestamp_utc,
            "source_divergence_price_i64": source.source_price_i64,
            "adjusted_ready": adjusted_ready,
            "catch_up": catch_up,
            "capacity_average_volume": volume_sum // config.capacity_lookback_bars,
            "latest_candidate_trend_divergence": None if latest is None else latest.signal_type,
        }
        ranked.append(
            (
                (-int(adjusted_ready), -sector_mean, -strength_class, dataset_id),
                details,
                candidate_bar,
            )
        )
    result: list[MaSectorRotationEvent] = []
    for rank, (_, details, candidate_bar) in enumerate(
        sorted(ranked, key=lambda item: item[0])[: config.maximum_rotation_candidates], 1
    ):
        candidate_id = str(details["candidate_dataset_id"])
        event_id = f"AUX-ROTATION-{source.dataset_id}-{source.object_id}-{candidate_id}"
        result.append(
            _event(
                event_id=event_id,
                event_type="aux_rotation_candidate",
                known_bar=anchor_bar,
                anchor_bar=source_bar,
                reason_code="AUX_TREND_TOP_DIVERGENCE_ROTATION_CANDIDATE",
                details={
                    **_common(context, config),
                    **details,
                    "chart_dataset_id": source.dataset_id,
                    "candidate_rank": rank,
                    "candidate_only": True,
                    "display_label": f"轮动候选 {rank}·{candidate_id}",
                    "classification_detail": (
                        f"{source.dataset_id} 趋势顶背驰后的第 {rank} 个轮动候选："
                        f"{candidate_id}；仅候选，不构成收益保证或交易指令"
                    ),
                    "candidate_bar_index": candidate_bar.bar_index,
                    "candidate_timestamp_utc": candidate_bar.timestamp_utc,
                    "candidate_price_i64": candidate_bar.close_i64,
                },
            )
        )
    return result


def _common(context: RankingContext, config: MaSectorRotationConfig) -> dict[str, Any]:
    return {
        "catalog_algorithm_id": "ALG-AUX-006",
        "semantic_namespace": "heuristic",
        "evidence_level": "HEURISTIC",
        "standard_signal": False,
        "execution_allowed": False,
        "opens_position": False,
        "profit_guarantee": False,
        "candidate_only": True,
        "universe_id": context.universe_id,
        "membership_revision": context.membership_revision,
        "membership_mode": context.membership_mode,
        "price_adjustment_mode": context.price_adjustment_mode,
        "price_adjustment_revision": context.price_adjustment_revision,
        "episode_id": context.episode_id,
        "episode_start_timestamp_utc": context.episode_start_timestamp_utc,
        "episode_available_at_utc": context.episode_available_at_utc,
        "ma_periods": list(config.ma_periods),
        "ma_conquer_rule": "adjusted_close_strictly_above_since_episode_start",
        "ma_equal_is_conquered": False,
    }


def _event(
    *,
    event_id: str,
    event_type: str,
    known_bar: RankingBar,
    anchor_bar: RankingBar,
    reason_code: str,
    details: dict[str, Any],
) -> MaSectorRotationEvent:
    return MaSectorRotationEvent(
        operation="upsert",
        event_id=event_id,
        event_type=event_type,
        known_at_bar_index=known_bar.bar_index,
        timestamp_utc=known_bar.timestamp_utc,
        bar_index=anchor_bar.bar_index,
        price_i64=anchor_bar.close_i64,
        reason_code=reason_code,
        details=details,
    )


def _delete_event(event_id: str, known_bar: RankingBar, reason_code: str) -> MaSectorRotationEvent:
    return MaSectorRotationEvent(
        operation="delete",
        event_id=event_id,
        event_type="aux_rotation_candidate",
        known_at_bar_index=known_bar.bar_index,
        timestamp_utc=known_bar.timestamp_utc,
        bar_index=known_bar.bar_index,
        price_i64=known_bar.close_i64,
        reason_code=reason_code,
        details={},
    )
