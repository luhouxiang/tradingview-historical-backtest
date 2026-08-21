from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

PPM = 1_000_000
MAX_I64 = 9_223_372_036_854_775_807
SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")

Action = Literal[
    "open_long",
    "open_short",
    "close_long",
    "close_short",
    "add_long",
    "add_short",
    "reduce_long",
    "reduce_short",
]
DecisionType = Literal[
    "approved_order_intent",
    "reduced_order_intent",
    "blocked_decision",
    "kill_switch",
]
TradingStatus = Literal["normal", "suspended", "limit_up", "limit_down"]

BUY_ACTIONS = {"open_long", "add_long", "close_short", "reduce_short"}
SELL_ACTIONS = {"open_short", "add_short", "close_long", "reduce_long"}
RISK_INCREASING_ACTIONS = {"open_long", "open_short", "add_long", "add_short"}


def _source_hash() -> str:
    return "sha256:" + hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def definition() -> dict[str, Any]:
    integer_ppm = {"type": "integer", "minimum": 1, "maximum": 5_000_000}
    loss_ppm = {"type": "integer", "minimum": 1, "maximum": PPM}
    return {
        "kind": "risk_filter",
        "algorithm_id": "unified_risk_execution_overlay",
        "algorithm_version": "1.0.0",
        "source_hash": _source_hash(),
        "name": "统一风险与执行覆盖层",
        "input_schema": "bars.v1",
        "parameter_schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "leverage_allowed": {"type": "boolean", "default": False},
                "leverage_approval_id": {"type": "string", "default": ""},
                "max_position_weight_ppm": {**integer_ppm, "default": 100_000},
                "max_sector_weight_ppm": {**integer_ppm, "default": 300_000},
                "max_order_loss_weight_ppm": {**loss_ppm, "default": 10_000},
                "stress_loss_per_contract_i64": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAX_I64,
                    "default": 100_000,
                },
                "max_daily_loss_ppm": {**loss_ppm, "default": 20_000},
                "max_strategy_drawdown_ppm": {**loss_ppm, "default": 150_000},
                "max_order_participation_ppm": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": PPM,
                    "default": 100_000,
                },
                "max_stale_bars": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 10_000,
                    "default": 0,
                },
                "max_data_gap_bars": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 10_000,
                    "default": 0,
                },
                "max_open_signal_age_bars": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 10_000,
                    "default": 3,
                },
                "event_risk_max_position_weight_ppm": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 5_000_000,
                    "default": 50_000,
                },
                "kill_switch_on_data_revision": {"type": "boolean", "default": True},
            },
            "required": [
                "leverage_allowed",
                "leverage_approval_id",
                "max_position_weight_ppm",
                "max_sector_weight_ppm",
                "max_order_loss_weight_ppm",
                "stress_loss_per_contract_i64",
                "max_daily_loss_ppm",
                "max_strategy_drawdown_ppm",
                "max_order_participation_ppm",
                "max_stale_bars",
                "max_data_gap_bars",
                "max_open_signal_age_bars",
                "event_risk_max_position_weight_ppm",
                "kill_switch_on_data_revision",
            ],
        },
        "outputs": [
            {
                "name": "risk_decision",
                "display_name": "风险覆盖决策",
                "pane": "main",
                "series_type": "semantic_objects",
                "object_type": "risk_decision",
            }
        ],
        "warmup": {"kind": "fixed_bars", "bars": 0},
        "causal": True,
    }


def _integer(parameters: dict[str, Any], name: str, minimum: int, maximum: int) -> int:
    value = parameters.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer in [{minimum}, {maximum}]")
    return value


@dataclass(frozen=True)
class RiskConfig:
    leverage_allowed: bool
    leverage_approval_id: str
    max_position_weight_ppm: int
    max_sector_weight_ppm: int
    max_order_loss_weight_ppm: int
    stress_loss_per_contract_i64: int
    max_daily_loss_ppm: int
    max_strategy_drawdown_ppm: int
    max_order_participation_ppm: int
    max_stale_bars: int
    max_data_gap_bars: int
    max_open_signal_age_bars: int
    event_risk_max_position_weight_ppm: int
    kill_switch_on_data_revision: bool

    @classmethod
    def from_parameters(cls, parameters: dict[str, Any]) -> RiskConfig:
        leverage_allowed = parameters.get("leverage_allowed")
        leverage_approval_id = parameters.get("leverage_approval_id")
        kill_switch = parameters.get("kill_switch_on_data_revision")
        if not isinstance(leverage_allowed, bool) or not isinstance(kill_switch, bool):
            raise ValueError("risk boolean parameters are invalid")
        if not isinstance(leverage_approval_id, str):
            raise ValueError("leverage_approval_id must be a string")
        if leverage_allowed and not leverage_approval_id.strip():
            raise ValueError("leveraged risk requires a separate approval id")
        result = cls(
            leverage_allowed=leverage_allowed,
            leverage_approval_id=leverage_approval_id,
            max_position_weight_ppm=_integer(parameters, "max_position_weight_ppm", 1, 5_000_000),
            max_sector_weight_ppm=_integer(parameters, "max_sector_weight_ppm", 1, 5_000_000),
            max_order_loss_weight_ppm=_integer(parameters, "max_order_loss_weight_ppm", 1, PPM),
            stress_loss_per_contract_i64=_integer(
                parameters, "stress_loss_per_contract_i64", 1, MAX_I64
            ),
            max_daily_loss_ppm=_integer(parameters, "max_daily_loss_ppm", 1, PPM),
            max_strategy_drawdown_ppm=_integer(parameters, "max_strategy_drawdown_ppm", 1, PPM),
            max_order_participation_ppm=_integer(parameters, "max_order_participation_ppm", 1, PPM),
            max_stale_bars=_integer(parameters, "max_stale_bars", 0, 10_000),
            max_data_gap_bars=_integer(parameters, "max_data_gap_bars", 0, 10_000),
            max_open_signal_age_bars=_integer(parameters, "max_open_signal_age_bars", 0, 10_000),
            event_risk_max_position_weight_ppm=_integer(
                parameters, "event_risk_max_position_weight_ppm", 0, 5_000_000
            ),
            kill_switch_on_data_revision=kill_switch,
        )
        if result.event_risk_max_position_weight_ppm > result.max_position_weight_ppm:
            raise ValueError("event risk position cap must not exceed the ordinary position cap")
        return result


@dataclass(frozen=True)
class MarketObservation:
    effective_from_bar_index: int
    available_at_bar_index: int
    data_revision: str
    trading_status: TradingStatus
    stale_bars: int
    data_gap_bars: int
    event_risk_active: bool


@dataclass(frozen=True)
class RiskContext:
    market_state_revision: str
    sector_id: str
    legal_future_branches: tuple[str, ...]
    handled_future_branches: tuple[str, ...]
    observations: tuple[MarketObservation, ...]
    dataset_revision: str

    @classmethod
    def from_payload(cls, value: dict[str, Any], dataset_revision: str) -> RiskContext:
        market_state_revision = value.get("market_state_revision")
        sector_id = value.get("sector_id")
        legal = value.get("legal_future_branches")
        handled = value.get("handled_future_branches")
        raw_observations = value.get("observations")
        if not isinstance(market_state_revision, str) or not SHA256.fullmatch(
            market_state_revision
        ):
            raise ValueError("market_state_revision must be sha256")
        if not isinstance(dataset_revision, str) or not SHA256.fullmatch(dataset_revision):
            raise ValueError("dataset revision must be sha256")
        if not isinstance(sector_id, str) or not sector_id:
            raise ValueError("sector_id is required")
        if not _valid_string_set(legal) or not _valid_string_set(handled):
            raise ValueError("future branch lists must contain unique non-empty strings")
        assert isinstance(legal, list) and isinstance(handled, list)
        if not isinstance(raw_observations, list):
            raise ValueError("risk observations must be an array")
        observations: list[MarketObservation] = []
        previous_effective = -1
        for raw in raw_observations:
            if not isinstance(raw, dict):
                raise ValueError("risk observation must be an object")
            effective = raw.get("effective_from_bar_index")
            available = raw.get("available_at_bar_index")
            revision = raw.get("data_revision")
            status = raw.get("trading_status")
            stale = raw.get("stale_bars")
            gap = raw.get("data_gap_bars")
            event_risk = raw.get("event_risk_active")
            if (
                isinstance(effective, bool)
                or not isinstance(effective, int)
                or effective < 0
                or isinstance(available, bool)
                or not isinstance(available, int)
                or available < 0
                or available > effective
                or effective <= previous_effective
            ):
                raise ValueError("risk observations must be strictly ordered and causal")
            if not isinstance(revision, str) or not SHA256.fullmatch(revision):
                raise ValueError("observation data_revision must be sha256")
            if status not in {"normal", "suspended", "limit_up", "limit_down"}:
                raise ValueError("invalid trading status")
            if (
                isinstance(stale, bool)
                or not isinstance(stale, int)
                or stale < 0
                or isinstance(gap, bool)
                or not isinstance(gap, int)
                or gap < 0
                or not isinstance(event_risk, bool)
            ):
                raise ValueError("invalid risk observation counters")
            observations.append(
                MarketObservation(
                    effective,
                    available,
                    revision,
                    status,
                    stale,
                    gap,
                    event_risk,
                )
            )
            previous_effective = effective
        return cls(
            market_state_revision,
            sector_id,
            tuple(legal),
            tuple(handled),
            tuple(observations),
            dataset_revision,
        )

    def observation_at(self, bar_index: int) -> MarketObservation:
        current = MarketObservation(
            effective_from_bar_index=0,
            available_at_bar_index=0,
            data_revision=self.dataset_revision,
            trading_status="normal",
            stale_bars=0,
            data_gap_bars=0,
            event_risk_active=False,
        )
        for observation in self.observations:
            if observation.effective_from_bar_index > bar_index:
                break
            if observation.available_at_bar_index <= bar_index:
                current = observation
        return current

    @property
    def unhandled_legal_branches(self) -> tuple[str, ...]:
        handled = set(self.handled_future_branches)
        return tuple(branch for branch in self.legal_future_branches if branch not in handled)


def _valid_string_set(value: object) -> bool:
    return (
        isinstance(value, list)
        and all(isinstance(item, str) and bool(item) for item in value)
        and len(value) == len(set(value))
    )


@dataclass(frozen=True)
class PortfolioSnapshot:
    bar_index: int
    equity_i64: int
    day_start_equity_i64: int
    peak_equity_i64: int
    position_side: Literal["long", "short"] | None
    position_quantity: int


@dataclass(frozen=True)
class OrderIntent:
    signal_id: str
    action: Action
    requested_quantity: int
    signal_available_at_bar_index: int
    requested_execution_bar_index: int


@dataclass
class RiskState:
    kill_switch_active: bool = False
    kill_switch_reason: str | None = None
    kill_switch_bar_index: int | None = None


@dataclass(frozen=True)
class RiskDecision:
    decision_type: DecisionType
    reason_code: str
    approved_action: Action | None
    approved_quantity: int
    requested_target_risk_weight_ppm: int
    approved_target_risk_weight_ppm: int
    scheduled_execution_bar_index: int | None
    retryable: bool
    kill_switch_active: bool


def _loss_fraction_ppm(reference: int, current: int) -> int:
    if reference <= 0 or current >= reference:
        return 0
    return (reference - current) * PPM // reference


def evaluate_portfolio_kill_switch(
    config: RiskConfig,
    context: RiskContext,
    state: RiskState,
    snapshot: PortfolioSnapshot,
    observation: MarketObservation,
) -> str | None:
    """Activate a persistent kill switch using facts known at this bar only."""
    if state.kill_switch_active:
        return None
    reason: str | None = None
    if (
        config.kill_switch_on_data_revision
        and observation.data_revision != context.dataset_revision
    ):
        reason = "DATA_REVISION_CHANGED"
    elif observation.stale_bars > config.max_stale_bars:
        reason = "STALE_MARKET_DATA_LIMIT"
    elif observation.data_gap_bars > config.max_data_gap_bars:
        reason = "MARKET_DATA_GAP_LIMIT"
    elif (
        _loss_fraction_ppm(snapshot.day_start_equity_i64, snapshot.equity_i64)
        >= config.max_daily_loss_ppm
    ):
        reason = "MAX_DAILY_LOSS_BREACH"
    elif (
        _loss_fraction_ppm(snapshot.peak_equity_i64, snapshot.equity_i64)
        >= config.max_strategy_drawdown_ppm
    ):
        reason = "MAX_STRATEGY_DRAWDOWN_BREACH"
    if reason is not None:
        state.kill_switch_active = True
        state.kill_switch_reason = reason
        state.kill_switch_bar_index = snapshot.bar_index
    return reason


def _weight_ppm(quantity: int, notional_per_contract_i64: int, equity_i64: int) -> int:
    if quantity <= 0 or notional_per_contract_i64 <= 0 or equity_i64 <= 0:
        return 0
    return quantity * notional_per_contract_i64 * PPM // equity_i64


def _blocked(
    reason: str,
    requested_weight: int,
    state: RiskState,
    *,
    retryable: bool = False,
    next_bar_index: int | None = None,
) -> RiskDecision:
    return RiskDecision(
        decision_type="blocked_decision",
        reason_code=reason,
        approved_action=None,
        approved_quantity=0,
        requested_target_risk_weight_ppm=requested_weight,
        approved_target_risk_weight_ppm=0,
        scheduled_execution_bar_index=next_bar_index,
        retryable=retryable,
        kill_switch_active=state.kill_switch_active,
    )


def _position_state_valid(intent: OrderIntent, snapshot: PortfolioSnapshot) -> bool:
    action = intent.action
    side = "long" if action.endswith("long") else "short"
    if action.startswith("open_"):
        return snapshot.position_side is None
    return snapshot.position_side == side and snapshot.position_quantity > 0


def evaluate_order_intent(
    config: RiskConfig,
    context: RiskContext,
    state: RiskState,
    snapshot: PortfolioSnapshot,
    observation: MarketObservation,
    intent: OrderIntent,
    *,
    evaluated_at_bar_index: int,
    price_i64: int,
    price_scale: int,
    contract_multiplier: float,
    money_scale: int,
    volume: int | None,
) -> RiskDecision:
    """Reduce or reject an order intent; never increase its requested quantity."""
    if intent.requested_quantity < 1:
        raise ValueError("requested order quantity must be positive")
    if price_i64 <= 0 or price_scale <= 0 or contract_multiplier <= 0 or money_scale <= 0:
        raise ValueError("risk notional inputs must be positive")
    notional_per_contract = round(price_i64 / price_scale * contract_multiplier * money_scale)
    current_quantity = snapshot.position_quantity
    requested_quantity = (
        current_quantity
        if intent.action in {"close_long", "close_short"}
        else intent.requested_quantity
    )
    requested_post_quantity = (
        current_quantity + requested_quantity
        if intent.action in RISK_INCREASING_ACTIONS and intent.action.startswith("add_")
        else requested_quantity
        if intent.action.startswith("open_")
        else max(0, current_quantity - requested_quantity)
    )
    requested_weight = _weight_ppm(
        requested_post_quantity, notional_per_contract, snapshot.equity_i64
    )
    risk_increasing = intent.action in RISK_INCREASING_ACTIONS
    if intent.requested_execution_bar_index < intent.signal_available_at_bar_index:
        return _blocked("LOOKAHEAD_EXECUTION", requested_weight, state)
    if risk_increasing and context.unhandled_legal_branches:
        return _blocked("UNHANDLED_LEGAL_BRANCH", requested_weight, state)
    if not _position_state_valid(intent, snapshot):
        return _blocked("POSITION_STATE_MISMATCH", requested_weight, state)
    age = evaluated_at_bar_index - intent.signal_available_at_bar_index
    if age < 0:
        return _blocked("LOOKAHEAD_EXECUTION", requested_weight, state)
    if risk_increasing and age > config.max_open_signal_age_bars:
        return _blocked("OPEN_SIGNAL_AGE_LIMIT", requested_weight, state)
    if observation.data_revision != context.dataset_revision:
        return _blocked("DATA_REVISION_MISMATCH", requested_weight, state)
    if state.kill_switch_active and risk_increasing:
        return _blocked("KILL_SWITCH_ACTIVE", requested_weight, state)
    blocked_by_status = (
        observation.trading_status == "suspended"
        or (observation.trading_status == "limit_up" and intent.action in BUY_ACTIONS)
        or (observation.trading_status == "limit_down" and intent.action in SELL_ACTIONS)
    )
    if blocked_by_status:
        retryable = not risk_increasing or age < config.max_open_signal_age_bars
        return _blocked(
            {
                "suspended": "MARKET_SUSPENDED",
                "limit_up": "PRICE_LIMIT_UP_BLOCKED",
                "limit_down": "PRICE_LIMIT_DOWN_BLOCKED",
                "normal": "MARKET_NOT_EXECUTABLE",
            }[observation.trading_status],
            requested_weight,
            state,
            retryable=retryable,
            next_bar_index=evaluated_at_bar_index + 1 if retryable else None,
        )
    if volume is None or volume < 0:
        return _blocked("MARKET_VOLUME_UNAVAILABLE", requested_weight, state)

    caps: list[tuple[int, str, bool]] = []
    participation_cap = volume * config.max_order_participation_ppm // PPM
    caps.append((participation_cap, "ORDER_PARTICIPATION", participation_cap == 0))
    if risk_increasing:
        loss_cap = (
            max(0, snapshot.equity_i64)
            * config.max_order_loss_weight_ppm
            // (PPM * config.stress_loss_per_contract_i64)
        )
        caps.append((loss_cap, "ORDER_LOSS", False))
        position_caps = [
            (config.max_position_weight_ppm, "POSITION_WEIGHT"),
            (config.max_sector_weight_ppm, "SECTOR_WEIGHT"),
        ]
        if observation.event_risk_active:
            position_caps.append((config.event_risk_max_position_weight_ppm, "EVENT_RISK_WEIGHT"))
        if not config.leverage_allowed:
            position_caps.append((PPM, "NO_LEVERAGE"))
        for weight_cap, reason in position_caps:
            allowed_total = (
                max(0, snapshot.equity_i64) * weight_cap // (PPM * notional_per_contract)
            )
            existing = current_quantity if intent.action.startswith("add_") else 0
            caps.append((max(0, allowed_total - existing), reason, False))

    approved_quantity, limiting_reason, zero_is_retryable = min(
        caps,
        key=lambda item: (
            item[0],
            [
                "NO_LEVERAGE",
                "POSITION_WEIGHT",
                "SECTOR_WEIGHT",
                "EVENT_RISK_WEIGHT",
                "ORDER_LOSS",
                "ORDER_PARTICIPATION",
            ].index(item[1]),
        ),
    )
    approved_quantity = min(requested_quantity, approved_quantity)
    if approved_quantity <= 0:
        retryable = zero_is_retryable and (
            not risk_increasing or age < config.max_open_signal_age_bars
        )
        return _blocked(
            f"{limiting_reason}_BLOCKED",
            requested_weight,
            state,
            retryable=retryable,
            next_bar_index=evaluated_at_bar_index + 1 if retryable else None,
        )
    approved_action: Action = intent.action
    if intent.action == "close_long" and approved_quantity < current_quantity:
        approved_action = "reduce_long"
    elif intent.action == "close_short" and approved_quantity < current_quantity:
        approved_action = "reduce_short"
    approved_post_quantity = (
        current_quantity + approved_quantity
        if approved_action.startswith("add_")
        else approved_quantity
        if approved_action.startswith("open_")
        else max(0, current_quantity - approved_quantity)
    )
    approved_weight = _weight_ppm(
        approved_post_quantity, notional_per_contract, snapshot.equity_i64
    )
    reduced = approved_quantity < requested_quantity or approved_action != intent.action
    continue_exit = (
        intent.action in {"close_long", "close_short"} and approved_quantity < current_quantity
    )
    return RiskDecision(
        decision_type="reduced_order_intent" if reduced else "approved_order_intent",
        reason_code=f"{limiting_reason}_REDUCED" if reduced else "RISK_LIMITS_PASSED",
        approved_action=approved_action,
        approved_quantity=approved_quantity,
        requested_target_risk_weight_ppm=requested_weight,
        approved_target_risk_weight_ppm=approved_weight,
        scheduled_execution_bar_index=(
            evaluated_at_bar_index + 1 if continue_exit else evaluated_at_bar_index
        ),
        retryable=continue_exit,
        kill_switch_active=state.kill_switch_active,
    )
