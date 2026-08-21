from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

ClassificationType = Literal["daily_one_center", "daily_two_center", "daily_no_center"]
BalanceSubtype = Literal[
    "weak_balance",
    "strong_balance",
    "turning_balance",
    "dual_extreme_balance",
]
ClosePosition = Literal[
    "at_day_high_and_low",
    "at_day_high",
    "above_center",
    "inside_center",
    "below_center",
    "at_day_low",
    "above_upper_center",
    "inside_upper_center",
    "single_side_interval",
    "inside_lower_center",
    "below_lower_center",
]
Direction = Literal["up", "down", "flat"]
EventOperation = Literal["upsert", "delete"]

PROFILE_ID = 1
PROFILE_NAME = "cn_a_share_4h_30m_bar_end_v1"
EXPECTED_SOURCE_HHMM = (1000, 1030, 1100, 1130, 1330, 1400, 1430, 1500)


def _source_hash() -> str:
    return "sha256:" + hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def definition() -> dict[str, Any]:
    return {
        "kind": "strategy",
        "algorithm_id": "aux_daily_30m_classification",
        "algorithm_version": "1.0.0",
        "source_hash": _source_hash(),
        "name": "经验·8根30分钟日内分类（不交易）",
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
                    "minimum": 30,
                    "maximum": 30,
                    "default": 30,
                },
                "session_profile_id": {
                    "type": "integer",
                    "minimum": PROFILE_ID,
                    "maximum": PROFILE_ID,
                    "default": PROFILE_ID,
                },
            },
            "required": [
                "checkpoint_interval",
                "observation_timeframe_minutes",
                "session_profile_id",
            ],
        },
        "outputs": [
            {
                "name": "aux_daily_30m_classification",
                "display_name": "经验·8根30分钟日内分类",
                "pane": "main",
                "series_type": "semantic_objects",
                "object_type": "chart_event",
            },
            {
                "name": "aux_daily_30m_profile_rejected",
                "display_name": "经验·日内会话不符合8根30分钟profile",
                "pane": "main",
                "series_type": "semantic_objects",
                "object_type": "chart_event",
            },
        ],
        "warmup": {"kind": "fixed_bars", "bars": len(EXPECTED_SOURCE_HHMM)},
        "causal": True,
    }


def _integer(parameters: dict[str, Any], name: str, minimum: int, maximum: int) -> int:
    value = parameters.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer between {minimum} and {maximum}")
    return value


def _timeframe_minutes(value: str) -> int:
    match = re.fullmatch(r"([1-9][0-9]*)(m|h|d)", value)
    if match is None:
        raise ValueError("dataset timeframe must use Nm, Nh or Nd format")
    return int(match.group(1)) * {"m": 1, "h": 60, "d": 1440}[match.group(2)]


@dataclass(frozen=True)
class Daily30mConfig:
    checkpoint_interval: int
    observation_timeframe_minutes: int
    session_profile_id: int
    session_profile: str
    expected_source_hhmm: tuple[int, ...]

    @classmethod
    def from_parameters(
        cls,
        parameters: dict[str, Any],
        *,
        dataset_timeframe: str,
        timestamp_semantics: str,
        date_semantics: str,
        timezone: str,
    ) -> Daily30mConfig:
        observation_timeframe = _integer(parameters, "observation_timeframe_minutes", 30, 30)
        if _timeframe_minutes(dataset_timeframe) != observation_timeframe:
            raise ValueError("dataset timeframe must be exactly 30m for the daily 8x30m profile")
        if timestamp_semantics != "bar_end":
            raise ValueError("daily 8x30m profile requires bar_end timestamp semantics")
        if date_semantics != "trading_day":
            raise ValueError("daily 8x30m profile requires trading_day date semantics")
        if timezone != "Asia/Shanghai":
            raise ValueError("daily 8x30m profile requires Asia/Shanghai timezone")
        profile_id = _integer(parameters, "session_profile_id", PROFILE_ID, PROFILE_ID)
        return cls(
            checkpoint_interval=_integer(parameters, "checkpoint_interval", 64, 100_000),
            observation_timeframe_minutes=observation_timeframe,
            session_profile_id=profile_id,
            session_profile=PROFILE_NAME,
            expected_source_hhmm=EXPECTED_SOURCE_HHMM,
        )


@dataclass(frozen=True)
class Daily30mBar:
    bar_index: int
    timestamp_utc: int
    trading_day: str
    source_hhmm: int
    open_i64: int
    high_i64: int
    low_i64: int
    close_i64: int


@dataclass(frozen=True)
class DailyOverlapCenter:
    start_ordinal: int
    end_ordinal: int
    start_bar_index: int
    end_bar_index: int
    start_timestamp_utc: int
    end_timestamp_utc: int
    low_i64: int
    high_i64: int


@dataclass(frozen=True)
class Daily30mEvent:
    operation: EventOperation
    event_id: str
    event_type: str
    known_at_bar_index: int
    timestamp_utc: int
    bar_index: int
    price_i64: int
    reason_code: str
    details: dict[str, Any]


def _overlap_center(bars: Sequence[Daily30mBar], start_position: int) -> DailyOverlapCenter | None:
    components = bars[start_position : start_position + 3]
    low_i64 = max(bar.low_i64 for bar in components)
    high_i64 = min(bar.high_i64 for bar in components)
    if low_i64 > high_i64:
        return None
    return DailyOverlapCenter(
        start_ordinal=start_position + 1,
        end_ordinal=start_position + 3,
        start_bar_index=components[0].bar_index,
        end_bar_index=components[-1].bar_index,
        start_timestamp_utc=components[0].timestamp_utc,
        end_timestamp_utc=components[-1].timestamp_utc,
        low_i64=low_i64,
        high_i64=high_i64,
    )


def _daily_centers(bars: Sequence[Daily30mBar]) -> tuple[DailyOverlapCenter, ...]:
    candidates = [
        center
        for position in range(len(bars) - 2)
        if (center := _overlap_center(bars, position)) is not None
    ]
    if not candidates:
        return ()
    first = candidates[0]
    for candidate in candidates[1:]:
        has_single_side_bar = candidate.start_ordinal > first.end_ordinal + 1
        strictly_nonoverlapping = (
            candidate.low_i64 > first.high_i64 or candidate.high_i64 < first.low_i64
        )
        if has_single_side_bar and strictly_nonoverlapping:
            return first, candidate
    return (first,)


def _one_center_close_position(
    close_i64: int,
    day_low_i64: int,
    day_high_i64: int,
    center: DailyOverlapCenter,
) -> ClosePosition:
    if day_low_i64 == day_high_i64 == close_i64:
        return "at_day_high_and_low"
    if close_i64 == day_high_i64:
        return "at_day_high"
    if close_i64 == day_low_i64:
        return "at_day_low"
    if close_i64 > center.high_i64:
        return "above_center"
    if close_i64 < center.low_i64:
        return "below_center"
    return "inside_center"


def _two_center_close_position(
    close_i64: int,
    day_low_i64: int,
    day_high_i64: int,
    centers: tuple[DailyOverlapCenter, DailyOverlapCenter],
) -> ClosePosition:
    if day_low_i64 == day_high_i64 == close_i64:
        return "at_day_high_and_low"
    if close_i64 == day_high_i64:
        return "at_day_high"
    if close_i64 == day_low_i64:
        return "at_day_low"
    lower, upper = sorted(centers, key=lambda center: center.low_i64)
    if close_i64 > upper.high_i64:
        return "above_upper_center"
    if upper.low_i64 <= close_i64 <= upper.high_i64:
        return "inside_upper_center"
    if lower.high_i64 < close_i64 < upper.low_i64:
        return "single_side_interval"
    if lower.low_i64 <= close_i64 <= lower.high_i64:
        return "inside_lower_center"
    return "below_lower_center"


def _balance_subtype(bars: Sequence[Daily30mBar]) -> BalanceSubtype:
    day_high = max(bar.high_i64 for bar in bars)
    day_low = min(bar.low_i64 for bar in bars)
    first_three_have_high = max(bar.high_i64 for bar in bars[:3]) == day_high
    first_three_have_low = min(bar.low_i64 for bar in bars[:3]) == day_low
    if first_three_have_high and first_three_have_low:
        return "dual_extreme_balance"
    if first_three_have_high:
        return "weak_balance"
    if first_three_have_low:
        return "strong_balance"
    return "turning_balance"


def _center_details(
    centers: tuple[DailyOverlapCenter, ...],
) -> dict[str, int | None]:
    result: dict[str, int | None] = {}
    for ordinal in (1, 2):
        center = centers[ordinal - 1] if ordinal <= len(centers) else None
        prefix = f"center_{ordinal}"
        result.update(
            {
                f"{prefix}_start_ordinal": None if center is None else center.start_ordinal,
                f"{prefix}_end_ordinal": None if center is None else center.end_ordinal,
                f"{prefix}_start_bar_index": None if center is None else center.start_bar_index,
                f"{prefix}_end_bar_index": None if center is None else center.end_bar_index,
                f"{prefix}_start_timestamp_utc": (
                    None if center is None else center.start_timestamp_utc
                ),
                f"{prefix}_end_timestamp_utc": (
                    None if center is None else center.end_timestamp_utc
                ),
                f"{prefix}_low_i64": None if center is None else center.low_i64,
                f"{prefix}_high_i64": None if center is None else center.high_i64,
            }
        )
    return result


_BALANCE_LABELS: dict[BalanceSubtype, str] = {
    "weak_balance": "弱平衡",
    "strong_balance": "强平衡",
    "turning_balance": "转折平衡",
    "dual_extreme_balance": "双极值平衡",
}

_CLOSE_LABELS: dict[ClosePosition, str] = {
    "at_day_high_and_low": "收于全天唯一价",
    "at_day_high": "收于全天高点",
    "above_center": "收于日内重叠区上方",
    "inside_center": "收于日内重叠区内",
    "below_center": "收于日内重叠区下方",
    "at_day_low": "收于全天低点",
    "above_upper_center": "收于上方重叠区上方",
    "inside_upper_center": "收于上方重叠区内",
    "single_side_interval": "收于单边区间",
    "inside_lower_center": "收于下方重叠区内",
    "below_lower_center": "收于下方重叠区下方",
}


def _classify_complete_session(
    bars: Sequence[Daily30mBar], config: Daily30mConfig
) -> Daily30mEvent:
    assert len(bars) == len(config.expected_source_hhmm)
    centers = _daily_centers(bars)
    day_high = max(bar.high_i64 for bar in bars)
    day_low = min(bar.low_i64 for bar in bars)
    final_bar = bars[-1]
    balance_subtype: BalanceSubtype | None = None
    direction: Direction | None = None

    if not centers:
        classification: ClassificationType = "daily_no_center"
        direction = (
            "up"
            if final_bar.close_i64 > bars[0].open_i64
            else "down"
            if final_bar.close_i64 < bars[0].open_i64
            else "flat"
        )
        close_position: ClosePosition | None = None
        strength_subclass = f"no_center_{direction}"
        display_label = (
            f"日内无重叠区·{ {'up': '向上', 'down': '向下', 'flat': '平收'}[direction] }"
        )
    elif len(centers) == 1:
        classification = "daily_one_center"
        balance_subtype = _balance_subtype(bars)
        close_position = _one_center_close_position(
            final_bar.close_i64, day_low, day_high, centers[0]
        )
        strength_subclass = f"{balance_subtype}_{close_position}"
        display_label = (
            f"日内一重叠区·{_BALANCE_LABELS[balance_subtype]}·{_CLOSE_LABELS[close_position]}"
        )
    else:
        classification = "daily_two_center"
        pair = (centers[0], centers[1])
        direction = "up" if pair[1].low_i64 > pair[0].high_i64 else "down"
        close_position = _two_center_close_position(final_bar.close_i64, day_low, day_high, pair)
        strength_subclass = f"two_center_{direction}_{close_position}"
        display_label = (
            f"日内双重叠区·{'向上' if direction == 'up' else '向下'}·"
            f"{_CLOSE_LABELS[close_position]}"
        )

    event_id = f"AUX-DAILY30M-{final_bar.trading_day}-CLASSIFICATION"
    common: dict[str, Any] = {
        "catalog_algorithm_id": "ALG-AUX-005",
        "semantic_namespace": "heuristic",
        "evidence_level": "HEURISTIC",
        "standard_signal": False,
        "standard_center": False,
        "execution_allowed": False,
        "opens_position": False,
        "profit_guarantee": False,
        "candidate_only": True,
        "market_session_profile": config.session_profile,
        "session_profile_id": config.session_profile_id,
        "center_semantics": "adjacent_three_bar_closed_overlap_not_standard_chan_center",
        "observation_timeframe_minutes": config.observation_timeframe_minutes,
        "expected_bar_count": len(config.expected_source_hhmm),
        "observed_bar_count": len(bars),
        "expected_source_hhmm": list(config.expected_source_hhmm),
        "observed_source_hhmm": [bar.source_hhmm for bar in bars],
        "trading_day": final_bar.trading_day,
        "session_start_bar_index": bars[0].bar_index,
        "session_end_bar_index": final_bar.bar_index,
        "session_start_timestamp_utc": bars[0].timestamp_utc,
        "session_end_timestamp_utc": final_bar.timestamp_utc,
        "classification": classification,
        "daily_center_count": len(centers),
        "balance_subtype": balance_subtype,
        "direction": direction,
        "close_position": close_position,
        "daily_strength_subclass": strength_subclass,
        "day_open_i64": bars[0].open_i64,
        "day_high_i64": day_high,
        "day_low_i64": day_low,
        "day_close_i64": final_bar.close_i64,
        "display_label": display_label,
        "classification_detail": (f"{display_label}；经验分类，不是标准中枢或买卖点，不允许交易"),
        **_center_details(centers),
    }
    return Daily30mEvent(
        operation="upsert",
        event_id=event_id,
        event_type="aux_daily_30m_classification",
        known_at_bar_index=final_bar.bar_index,
        timestamp_utc=final_bar.timestamp_utc,
        bar_index=final_bar.bar_index,
        price_i64=final_bar.close_i64,
        reason_code="AUX_DAILY_8X30M_SESSION_CLASSIFIED_AT_FINAL_BAR_CLOSE",
        details=common,
    )


def _profile_rejection(
    *,
    trading_day: str,
    session: Sequence[Daily30mBar],
    known_bar: Daily30mBar,
    config: Daily30mConfig,
    reason_code: str,
    invalidates_event_id: str | None = None,
) -> Daily30mEvent:
    anchor = session[-1] if session else known_bar
    return Daily30mEvent(
        operation="upsert",
        event_id=f"AUX-DAILY30M-{trading_day}-PROFILE-REJECTED",
        event_type="aux_daily_30m_profile_rejected",
        known_at_bar_index=known_bar.bar_index,
        timestamp_utc=anchor.timestamp_utc,
        bar_index=anchor.bar_index,
        price_i64=anchor.close_i64,
        reason_code=reason_code,
        details={
            "catalog_algorithm_id": "ALG-AUX-005",
            "semantic_namespace": "heuristic",
            "evidence_level": "HEURISTIC",
            "standard_signal": False,
            "standard_center": False,
            "execution_allowed": False,
            "opens_position": False,
            "profit_guarantee": False,
            "candidate_only": True,
            "market_session_profile": config.session_profile,
            "session_profile_id": config.session_profile_id,
            "observation_timeframe_minutes": config.observation_timeframe_minutes,
            "expected_bar_count": len(config.expected_source_hhmm),
            "observed_bar_count": len(session),
            "expected_source_hhmm": list(config.expected_source_hhmm),
            "observed_source_hhmm": [bar.source_hhmm for bar in session],
            "trading_day": trading_day,
            "classification": None,
            "daily_center_count": None,
            "daily_strength_subclass": None,
            "invalidates_event_id": invalidates_event_id,
            "display_label": "日内8根30分钟profile不匹配",
            "classification_detail": "会话缺根、含夜盘或时间模板变化；本日不分类",
        },
    )


def classify_daily_30m_sessions(
    bars: Sequence[Daily30mBar], config: Daily30mConfig
) -> list[Daily30mEvent]:
    """Classify exact eight-bar sessions without backfilling an unfinished day."""
    events: list[Daily30mEvent] = []
    session: list[Daily30mBar] = []
    trading_day: str | None = None
    rejected = False
    classified_event_id: str | None = None
    previous: Daily30mBar | None = None

    def reject(known_bar: Daily30mBar, reason_code: str) -> None:
        nonlocal rejected, classified_event_id
        assert trading_day is not None
        if classified_event_id is not None:
            events.append(
                Daily30mEvent(
                    operation="delete",
                    event_id=classified_event_id,
                    event_type="aux_daily_30m_classification",
                    known_at_bar_index=known_bar.bar_index,
                    timestamp_utc=known_bar.timestamp_utc,
                    bar_index=known_bar.bar_index,
                    price_i64=known_bar.close_i64,
                    reason_code="AUX_DAILY_8X30M_CLASSIFICATION_INVALIDATED_BY_SESSION_CHANGE",
                    details={},
                )
            )
        events.append(
            _profile_rejection(
                trading_day=trading_day,
                session=session,
                known_bar=known_bar,
                config=config,
                reason_code=reason_code,
                invalidates_event_id=classified_event_id,
            )
        )
        classified_event_id = None
        rejected = True

    for bar in bars:
        if not bar.trading_day:
            raise ValueError("trading_day is required for every daily 8x30m input bar")
        if bar.low_i64 > min(bar.open_i64, bar.close_i64) or bar.high_i64 < max(
            bar.open_i64, bar.close_i64
        ):
            raise ValueError("input bar violates OHLC bounds")
        if previous is not None and (
            bar.bar_index <= previous.bar_index or bar.timestamp_utc <= previous.timestamp_utc
        ):
            raise ValueError("daily 8x30m bars must be strictly ordered")

        if trading_day is not None and bar.trading_day != trading_day:
            if not rejected and classified_event_id is None and session:
                reject(bar, "AUX_DAILY_8X30M_MISSING_BAR_BEFORE_NEXT_TRADING_DAY")
            session = []
            trading_day = bar.trading_day
            rejected = False
            classified_event_id = None
        elif trading_day is None:
            trading_day = bar.trading_day

        if rejected:
            previous = bar
            continue

        expected_position = len(session)
        session.append(bar)
        if (
            expected_position >= len(config.expected_source_hhmm)
            or bar.source_hhmm != config.expected_source_hhmm[expected_position]
        ):
            reject(bar, "AUX_DAILY_8X30M_SESSION_TEMPLATE_MISMATCH")
            previous = bar
            continue
        if len(session) == len(config.expected_source_hhmm):
            classified = _classify_complete_session(session, config)
            events.append(classified)
            classified_event_id = classified.event_id
        previous = bar

    return events


__all__ = [
    "EXPECTED_SOURCE_HHMM",
    "PROFILE_NAME",
    "Daily30mBar",
    "Daily30mConfig",
    "Daily30mEvent",
    "DailyOverlapCenter",
    "classify_daily_30m_sessions",
    "definition",
]
