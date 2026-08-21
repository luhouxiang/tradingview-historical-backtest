from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import shutil
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from tvbt import CONTRACT_VERSION, ENGINE_VERSION
from tvbt.chan.storage import EVENT_SCHEMA
from tvbt.indicators import resolve
from tvbt.logging_config.logger import LOG_METADATA_FIELDS, format_fixed_text_entry
from tvbt.risk import (
    OrderIntent,
    PortfolioSnapshot,
    RiskConfig,
    RiskContext,
    RiskDecision,
    RiskState,
    evaluate_order_intent,
    evaluate_portfolio_kill_switch,
    unified_risk_overlay_definition,
)
from tvbt.storage.path_guard import PathGuard
from tvbt.strategy import run_strategy

SCHEMAS = {
    "indicator_values": pa.schema([("bar_index", pa.int64()), ("ma", pa.float64())]),
    "strategy_states": pa.schema(
        [
            ("known_at_bar_index", pa.int64()),
            ("state_from", pa.string()),
            ("state_to", pa.string()),
            ("reason_code", pa.string()),
            ("object_revision", pa.int64()),
        ]
    ),
    "stage_signals": pa.schema(
        [
            ("stage_signal_id", pa.string()),
            ("known_at_bar_index", pa.int64()),
            ("stage", pa.string()),
            ("status", pa.string()),
            ("price_i64", pa.int64()),
            ("reason_code", pa.string()),
            ("object_revision", pa.int64()),
        ]
    ),
    "trade_signals": pa.schema(
        [
            ("signal_id", pa.string()),
            ("parent_stage_signal_id", pa.string()),
            ("known_at_bar_index", pa.int64()),
            ("side", pa.string()),
            ("action", pa.string()),
            ("quantity", pa.int64()),
            ("price_i64", pa.int64()),
            ("reason_code", pa.string()),
            ("object_revision", pa.int64()),
        ]
    ),
    "risk_decisions": pa.schema(
        [
            ("risk_decision_id", pa.string()),
            ("signal_id", pa.string()),
            ("known_at_bar_index", pa.int64()),
            ("timestamp_utc", pa.int64()),
            ("price_i64", pa.int64()),
            ("decision_type", pa.string()),
            ("reason_code", pa.string()),
            ("requested_action", pa.string()),
            ("approved_action", pa.string()),
            ("requested_quantity", pa.int64()),
            ("approved_quantity", pa.int64()),
            ("requested_target_risk_weight_ppm", pa.int64()),
            ("approved_target_risk_weight_ppm", pa.int64()),
            ("signal_available_at_bar_index", pa.int64()),
            ("requested_execution_bar_index", pa.int64()),
            ("scheduled_execution_bar_index", pa.int64()),
            ("retryable", pa.bool_()),
            ("kill_switch_active", pa.bool_()),
            ("market_state_revision", pa.string()),
            ("observed_data_revision", pa.string()),
            ("sector_id", pa.string()),
            ("object_revision", pa.int64()),
        ]
    ),
    "orders": pa.schema(
        [
            ("order_id", pa.string()),
            ("signal_id", pa.string()),
            ("created_at_bar_index", pa.int64()),
            ("fill_bar_index", pa.int64()),
            ("action", pa.string()),
            ("quantity", pa.int64()),
            ("status", pa.string()),
            ("reason_code", pa.string()),
        ]
    ),
    "fills": pa.schema(
        [
            ("fill_id", pa.string()),
            ("order_id", pa.string()),
            ("bar_index", pa.int64()),
            ("timestamp_utc", pa.int64()),
            ("action", pa.string()),
            ("quantity", pa.int64()),
            ("price_i64", pa.int64()),
            ("commission_i64", pa.int64()),
            ("slippage_i64", pa.int64()),
        ]
    ),
    "trades": pa.schema(
        [
            ("trade_id", pa.string()),
            ("side", pa.string()),
            ("entry_bar_index", pa.int64()),
            ("entry_time", pa.int64()),
            ("entry_price_i64", pa.int64()),
            ("exit_bar_index", pa.int64()),
            ("exit_time", pa.int64()),
            ("exit_price_i64", pa.int64()),
            ("quantity", pa.int64()),
            ("gross_pnl_i64", pa.int64()),
            ("net_pnl_i64", pa.int64()),
            ("commission_i64", pa.int64()),
            ("slippage_i64", pa.int64()),
        ]
    ),
    "positions": pa.schema(
        [
            ("bar_index", pa.int64()),
            ("timestamp_utc", pa.int64()),
            ("side", pa.string()),
            ("quantity", pa.int64()),
            ("entry_price_i64", pa.int64()),
            ("unrealized_pnl_i64", pa.int64()),
        ]
    ),
    "equity": pa.schema(
        [
            ("bar_index", pa.int64()),
            ("timestamp_utc", pa.int64()),
            ("equity_i64", pa.int64()),
            ("cash_i64", pa.int64()),
            ("available_i64", pa.int64()),
            ("margin_i64", pa.int64()),
            ("drawdown", pa.float64()),
        ]
    ),
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return "sha256:" + digest


def _money(price_delta_i64: int | float, scale: int, multiplier: float, money_scale: int) -> int:
    return round(float(price_delta_i64) / scale * multiplier * money_scale)


def _commission(execution: dict[str, Any], notional_i64: int, quantity: int) -> int:
    value = execution["commission"]
    if value["mode"] == "fixed_per_contract":
        source_scale = int(value.get("money_scale", 1))
        return round(
            int(value.get("amount_i64", 0))
            * quantity
            / source_scale
            * int(execution["money_scale"])
        )
    return round(abs(notional_i64) * float(value.get("rate", 0)))


def _trade_signal_quantity(signal: dict[str, Any]) -> int:
    value = signal.get("quantity")
    if value is None:
        return 1
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("trade signal quantity must be a positive integer")
    return value


def _fill_price(
    open_i64: int, action: str, execution: dict[str, Any], tick_size: int
) -> tuple[int, int]:
    slippage = execution["slippage"]
    selling = action in {"open_short", "add_short", "close_long", "reduce_long"}
    if slippage["mode"] == "ticks":
        delta = round(float(slippage["value"]) * tick_size)
    else:
        delta = round(open_i64 * float(slippage["value"]) / 10_000)
    return (open_i64 - delta if selling else open_i64 + delta), abs(delta)


def _position_quantity(position: dict[str, Any] | None) -> int:
    if position is None:
        return 0
    return sum(int(lot["quantity"]) for lot in position["lots"])


def _position_entry_price(position: dict[str, Any] | None) -> int | None:
    quantity = _position_quantity(position)
    if position is None or quantity == 0:
        return None
    return round(
        sum(int(lot["entry_price_i64"]) * int(lot["quantity"]) for lot in position["lots"])
        / quantity
    )


def _position_unrealized(
    position: dict[str, Any] | None,
    close_i64: int,
    price_scale: int,
    multiplier: float,
    money_scale: int,
) -> int:
    if position is None:
        return 0
    short = position["side"] == "short"
    return sum(
        _money(
            int(lot["entry_price_i64"]) - close_i64
            if short
            else close_i64 - int(lot["entry_price_i64"]),
            price_scale,
            multiplier,
            money_scale,
        )
        * int(lot["quantity"])
        for lot in position["lots"]
    )


def _consume_position_lots(position: dict[str, Any], quantity: int) -> list[dict[str, Any]]:
    remaining = quantity
    consumed: list[dict[str, Any]] = []
    retained: list[dict[str, Any]] = []
    for source in position["lots"]:
        lot = dict(source)
        available = int(lot["quantity"])
        take = min(available, remaining)
        if take:
            entry_commission = int(lot["entry_commission_i64"])
            entry_slippage = int(lot["entry_slippage_i64"])
            consumed_commission = (
                entry_commission
                if take == available
                else round(entry_commission * take / available)
            )
            consumed_slippage = (
                entry_slippage if take == available else round(entry_slippage * take / available)
            )
            consumed.append(
                {
                    **lot,
                    "quantity": take,
                    "entry_commission_i64": consumed_commission,
                    "entry_slippage_i64": consumed_slippage,
                }
            )
            remaining -= take
            if take < available:
                retained.append(
                    {
                        **lot,
                        "quantity": available - take,
                        "entry_commission_i64": entry_commission - consumed_commission,
                        "entry_slippage_i64": entry_slippage - consumed_slippage,
                    }
                )
        else:
            retained.append(lot)
    if remaining:
        raise ValueError("position reduction exceeds available quantity")
    position["lots"] = retained
    return consumed


def _risk_overlay(
    payload: dict[str, Any], dataset_revision: str
) -> tuple[dict[str, Any], RiskConfig, RiskContext] | None:
    raw = payload.get("risk_overlay")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("risk_overlay must be an object")
    algorithm = raw.get("algorithm")
    parameters = raw.get("parameters")
    context = raw.get("context")
    if (
        not isinstance(algorithm, dict)
        or not isinstance(parameters, dict)
        or not isinstance(context, dict)
    ):
        raise ValueError("risk overlay facts are incomplete")
    expected = unified_risk_overlay_definition()
    for field in ("kind", "algorithm_id", "algorithm_version", "source_hash"):
        if algorithm.get(field) != expected[field]:
            raise ValueError("risk overlay algorithm reference is stale or invalid")
    return (
        raw,
        RiskConfig.from_parameters(parameters),
        RiskContext.from_payload(context, dataset_revision),
    )


def _risk_market_facts(path: Path, start: int, end: int) -> dict[int, tuple[str, int]]:
    names = set(pq.read_schema(path).names)
    if not {"bar_index", "trading_day", "volume"}.issubset(names):
        raise ValueError("risk overlay requires bar_index, trading_day and volume")
    table = pq.read_table(path, columns=["bar_index", "trading_day", "volume"])
    facts: dict[int, tuple[str, int]] = {}
    for position in range(table.num_rows):
        bar_index = int(table["bar_index"][position].as_py())
        if bar_index < start or bar_index > end:
            continue
        trading_day = table["trading_day"][position].as_py()
        volume = table["volume"][position].as_py()
        if trading_day is None or volume is None or int(volume) < 0:
            raise ValueError("risk market facts contain missing trading day or volume")
        facts[bar_index] = (str(trading_day), int(volume))
    if len(facts) != end - start + 1:
        raise ValueError("risk market facts do not cover the backtest range")
    return facts


def _risk_snapshot(
    *,
    bar_index: int,
    equity_i64: int,
    day_start_equity_i64: int,
    peak_equity_i64: int,
    position: dict[str, Any] | None,
) -> PortfolioSnapshot:
    return PortfolioSnapshot(
        bar_index=bar_index,
        equity_i64=equity_i64,
        day_start_equity_i64=day_start_equity_i64,
        peak_equity_i64=peak_equity_i64,
        position_side=None if position is None else position["side"],
        position_quantity=_position_quantity(position),
    )


def _append_risk_decision(
    rows: list[dict[str, Any]],
    events: list[dict[str, Any]],
    *,
    context: RiskContext,
    observation_data_revision: str,
    bar_index: int,
    timestamp_utc: int,
    price_i64: int,
    decision: RiskDecision,
    signal: dict[str, Any] | None,
    requested_execution_bar_index: int,
) -> None:
    signal_id = "" if signal is None else str(signal["signal_id"])
    requested_action = "" if signal is None else str(signal["action"])
    requested_quantity = 0 if signal is None else _trade_signal_quantity(signal)
    signal_available = bar_index if signal is None else int(signal["known_at_bar_index"])
    identity = "|".join(
        (
            signal_id,
            str(bar_index),
            decision.decision_type,
            decision.reason_code,
            str(len(rows)),
        )
    )
    short_hash = hashlib.sha256(identity.encode()).hexdigest()[:16]
    decision_id = f"RISK-{bar_index}-{decision.decision_type}-{short_hash}"
    row = {
        "risk_decision_id": decision_id,
        "signal_id": signal_id,
        "known_at_bar_index": bar_index,
        "timestamp_utc": timestamp_utc,
        "price_i64": price_i64,
        "decision_type": decision.decision_type,
        "reason_code": decision.reason_code,
        "requested_action": requested_action,
        "approved_action": decision.approved_action,
        "requested_quantity": requested_quantity,
        "approved_quantity": decision.approved_quantity,
        "requested_target_risk_weight_ppm": decision.requested_target_risk_weight_ppm,
        "approved_target_risk_weight_ppm": decision.approved_target_risk_weight_ppm,
        "signal_available_at_bar_index": signal_available,
        "requested_execution_bar_index": requested_execution_bar_index,
        "scheduled_execution_bar_index": decision.scheduled_execution_bar_index,
        "retryable": decision.retryable,
        "kill_switch_active": decision.kill_switch_active,
        "market_state_revision": context.market_state_revision,
        "observed_data_revision": observation_data_revision,
        "sector_id": context.sector_id,
        "object_revision": 1,
    }
    rows.append(row)
    display_labels = {
        "approved_order_intent": "风控·订单意图批准",
        "reduced_order_intent": "风控·订单意图降仓",
        "blocked_decision": "风控·策略决策阻断",
        "kill_switch": "风控·熔断",
    }
    payload = {
        **row,
        "event_type": decision.decision_type,
        "bar_index": bar_index,
        "display_label": display_labels[decision.decision_type],
        "classification_detail": decision.reason_code,
        "catalog_algorithm_id": "ALG-RISK-001",
        "semantic_namespace": "risk",
        "evidence_level": "HEURISTIC",
        "standard_signal": False,
        "execution_allowed": decision.decision_type
        in {"approved_order_intent", "reduced_order_intent"},
        "opens_position": decision.approved_action
        in {"open_long", "open_short", "add_long", "add_short"},
    }
    events.append(
        {
            "event_seq": max((int(event["event_seq"]) for event in events), default=-1) + 1,
            "known_at_bar_index": bar_index,
            "object_type": "risk_decision",
            "object_id": decision_id,
            "operation": "upsert",
            "object_revision": 1,
            "payload_json": json.dumps(payload, separators=(",", ":"), sort_keys=True),
        }
    )


def _kill_switch_decision(state: RiskState, reason: str) -> RiskDecision:
    return RiskDecision(
        decision_type="kill_switch",
        reason_code=reason,
        approved_action=None,
        approved_quantity=0,
        requested_target_risk_weight_ppm=0,
        approved_target_risk_weight_ppm=0,
        scheduled_execution_bar_index=None,
        retryable=False,
        kill_switch_active=state.kill_switch_active,
    )


def run_backtest(payload: dict[str, Any], guard: PathGuard, cancelled: threading.Event) -> str:
    dataset = payload.get("dataset")
    algorithm = payload.get("algorithm")
    parameters = payload.get("parameters")
    range_value = payload.get("range")
    execution_input = payload.get("execution")
    capital = payload.get("capital")
    if not all(
        isinstance(value, dict)
        for value in (dataset, algorithm, parameters, range_value, execution_input, capital)
    ):
        raise ValueError("backtest facts are incomplete")
    assert isinstance(dataset, dict) and isinstance(algorithm, dict)
    assert isinstance(parameters, dict) and isinstance(range_value, dict)
    assert isinstance(execution_input, dict) and isinstance(capital, dict)
    start, end, warmup = (
        int(range_value["from_bar_index"]),
        int(range_value["to_bar_index"]),
        int(range_value["warmup_from_bar_index"]),
    )
    if warmup < 0 or start < warmup or end < start:
        raise ValueError("invalid backtest range")
    meta = json.loads(guard.resolve(str(dataset["meta_path"])).read_text(encoding="utf-8"))
    price_scale = int(meta["price"]["price_scale"])
    tick_size = int(meta["price"].get("tick_size_i64") or 1)
    money_scale = int(capital["money_scale"])
    execution = {**execution_input, "money_scale": money_scale}
    strategy = run_strategy(payload, guard, cancelled, last_bar_index=end)
    if not strategy.bars or end > strategy.bars[-1].bar_index:
        raise ValueError("backtest range exceeds dataset")
    risk_overlay = _risk_overlay(payload, str(dataset["data_revision"]))
    risk_config: RiskConfig | None = None
    risk_context: RiskContext | None = None
    risk_market: dict[int, tuple[str, int]] = {}
    if risk_overlay is not None:
        _, risk_config, risk_context = risk_overlay
        risk_market = _risk_market_facts(guard.resolve(str(dataset["bars_path"])), start, end)
    signals = [
        signal for signal in strategy.trade_signals if start <= signal["known_at_bar_index"] <= end
    ]
    due: dict[int, list[dict[str, Any]]] = {}
    for signal in signals:
        fill_index = signal["known_at_bar_index"] + (
            1 if execution["fill_timing"] == "next_bar_open" else 0
        )
        due.setdefault(fill_index, []).append(signal)
    cash = int(capital["initial_cash_i64"])
    peak = cash
    position: dict[str, Any] | None = None
    orders: list[dict[str, Any]] = []
    order_ids: set[str] = set()
    fills: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    positions: list[dict[str, Any]] = []
    equity: list[dict[str, Any]] = []
    total_commission = 0
    total_slippage = 0
    risk_decisions: list[dict[str, Any]] = []
    risk_state = RiskState()
    current_trading_day: str | None = None
    day_start_equity = cash
    bars = [bar for bar in strategy.bars if start <= bar.bar_index <= end]
    for bar in bars:
        if cancelled.is_set():
            raise InterruptedError("backtest cancelled")
        source_price = (
            bar.open_i64 if execution["fill_timing"] == "next_bar_open" else bar.close_i64
        )
        market_observation = None
        market_volume: int | None = None
        if risk_config is not None and risk_context is not None:
            trading_day, market_volume = risk_market[bar.bar_index]
            mark_unrealized = _position_unrealized(
                position,
                source_price,
                price_scale,
                float(execution["contract_multiplier"]),
                money_scale,
            )
            mark_equity = cash + mark_unrealized
            if trading_day != current_trading_day:
                current_trading_day = trading_day
                day_start_equity = mark_equity
            market_observation = risk_context.observation_at(bar.bar_index)
            snapshot = _risk_snapshot(
                bar_index=bar.bar_index,
                equity_i64=mark_equity,
                day_start_equity_i64=day_start_equity,
                peak_equity_i64=peak,
                position=position,
            )
            kill_reason = evaluate_portfolio_kill_switch(
                risk_config, risk_context, risk_state, snapshot, market_observation
            )
            if kill_reason is not None:
                _append_risk_decision(
                    risk_decisions,
                    strategy.events,
                    context=risk_context,
                    observation_data_revision=market_observation.data_revision,
                    bar_index=bar.bar_index,
                    timestamp_utc=bar.timestamp_utc,
                    price_i64=source_price,
                    decision=_kill_switch_decision(risk_state, kill_reason),
                    signal=None,
                    requested_execution_bar_index=bar.bar_index,
                )
        for signal in due.get(bar.bar_index, []):
            action = str(signal["action"])
            requested_quantity = _trade_signal_quantity(signal)
            original_execution_bar_index = int(signal["known_at_bar_index"]) + (
                1 if execution["fill_timing"] == "next_bar_open" else 0
            )
            if risk_config is not None and risk_context is not None:
                assert market_observation is not None
                mark_unrealized = _position_unrealized(
                    position,
                    source_price,
                    price_scale,
                    float(execution["contract_multiplier"]),
                    money_scale,
                )
                snapshot = _risk_snapshot(
                    bar_index=bar.bar_index,
                    equity_i64=cash + mark_unrealized,
                    day_start_equity_i64=day_start_equity,
                    peak_equity_i64=peak,
                    position=position,
                )
                decision = evaluate_order_intent(
                    risk_config,
                    risk_context,
                    risk_state,
                    snapshot,
                    market_observation,
                    OrderIntent(
                        signal_id=str(signal["signal_id"]),
                        action=action,  # type: ignore[arg-type]
                        requested_quantity=requested_quantity,
                        signal_available_at_bar_index=int(signal["known_at_bar_index"]),
                        requested_execution_bar_index=original_execution_bar_index,
                    ),
                    evaluated_at_bar_index=bar.bar_index,
                    price_i64=source_price,
                    price_scale=price_scale,
                    contract_multiplier=float(execution["contract_multiplier"]),
                    money_scale=money_scale,
                    volume=market_volume,
                )
                if (
                    decision.retryable
                    and decision.scheduled_execution_bar_index is not None
                    and decision.scheduled_execution_bar_index > end
                ):
                    decision = RiskDecision(
                        decision_type=(
                            "blocked_decision"
                            if decision.approved_action is None
                            else decision.decision_type
                        ),
                        reason_code=(
                            "NO_NEXT_EXECUTABLE_BAR"
                            if decision.approved_action is None
                            else decision.reason_code
                        ),
                        approved_action=decision.approved_action,
                        approved_quantity=decision.approved_quantity,
                        requested_target_risk_weight_ppm=decision.requested_target_risk_weight_ppm,
                        approved_target_risk_weight_ppm=decision.approved_target_risk_weight_ppm,
                        scheduled_execution_bar_index=None,
                        retryable=False,
                        kill_switch_active=decision.kill_switch_active,
                    )
                _append_risk_decision(
                    risk_decisions,
                    strategy.events,
                    context=risk_context,
                    observation_data_revision=market_observation.data_revision,
                    bar_index=bar.bar_index,
                    timestamp_utc=bar.timestamp_utc,
                    price_i64=source_price,
                    decision=decision,
                    signal=signal,
                    requested_execution_bar_index=original_execution_bar_index,
                )
                if decision.retryable and decision.scheduled_execution_bar_index is not None:
                    due.setdefault(decision.scheduled_execution_bar_index, []).append(signal)
                    if decision.approved_action is None:
                        continue
                if decision.approved_action is None or decision.approved_quantity <= 0:
                    continue
                action = decision.approved_action
                requested_quantity = decision.approved_quantity
            order_id = f"ORDER-{signal['signal_id']}"
            if order_id in order_ids:
                order_id = f"{order_id}-{bar.bar_index}-{len(orders) + 1}"
            order_ids.add(order_id)
            action_side = (
                "long"
                if action in {"open_long", "add_long", "reduce_long", "close_long"}
                else "short"
            )
            matching_position = position is not None and position["side"] == action_side
            closes_position = matching_position and action in {"close_long", "close_short"}
            reduces_position = matching_position and action in {"reduce_long", "reduce_short"}
            adds_position = matching_position and action in {"add_long", "add_short"}
            quantity = requested_quantity
            if closes_position:
                assert position is not None
                quantity = _position_quantity(position)
            fill_price, slippage_price = _fill_price(source_price, action, execution, tick_size)
            slippage_cost = quantity * _money(
                slippage_price,
                price_scale,
                float(execution["contract_multiplier"]),
                money_scale,
            )
            notional_per_contract = _money(
                fill_price, price_scale, float(execution["contract_multiplier"]), money_scale
            )
            notional = notional_per_contract * quantity
            commission = _commission(execution, notional, quantity)
            reason = "FILLED"
            status = "filled"
            if action in {"open_short", "open_long"} and position is None:
                margin = round(abs(notional) * float(execution["margin_ratio"]))
                if cash < margin + commission:
                    status, reason = "rejected", "INSUFFICIENT_MARGIN"
                else:
                    cash -= commission
                    position = {
                        "side": "short" if action == "open_short" else "long",
                        "lots": [
                            {
                                "entry_bar_index": bar.bar_index,
                                "entry_time": bar.timestamp_utc,
                                "entry_price_i64": fill_price,
                                "quantity": quantity,
                                "entry_commission_i64": commission,
                                "entry_slippage_i64": slippage_cost,
                            }
                        ],
                    }
            elif adds_position:
                assert position is not None
                margin = round(abs(notional) * float(execution["margin_ratio"]))
                if cash < margin + commission:
                    status, reason = "rejected", "INSUFFICIENT_MARGIN"
                else:
                    cash -= commission
                    position["lots"].append(
                        {
                            "entry_bar_index": bar.bar_index,
                            "entry_time": bar.timestamp_utc,
                            "entry_price_i64": fill_price,
                            "quantity": quantity,
                            "entry_commission_i64": commission,
                            "entry_slippage_i64": slippage_cost,
                        }
                    )
            elif closes_position or reduces_position:
                assert position is not None
                available_quantity = _position_quantity(position)
                if reduces_position and quantity > available_quantity:
                    status, reason = "rejected", "REDUCE_QUANTITY_EXCEEDS_POSITION"
                else:
                    consumed = _consume_position_lots(position, quantity)
                    gross_total = 0
                    remaining_exit_commission = commission
                    remaining_exit_slippage = slippage_cost
                    for lot_index, lot in enumerate(consumed):
                        lot_quantity = int(lot["quantity"])
                        last_lot = lot_index == len(consumed) - 1
                        exit_commission = (
                            remaining_exit_commission
                            if last_lot
                            else round(commission * lot_quantity / quantity)
                        )
                        exit_slippage = (
                            remaining_exit_slippage
                            if last_lot
                            else round(slippage_cost * lot_quantity / quantity)
                        )
                        remaining_exit_commission -= exit_commission
                        remaining_exit_slippage -= exit_slippage
                        price_delta = (
                            int(lot["entry_price_i64"]) - fill_price
                            if position["side"] == "short"
                            else fill_price - int(lot["entry_price_i64"])
                        )
                        gross = (
                            _money(
                                price_delta,
                                price_scale,
                                float(execution["contract_multiplier"]),
                                money_scale,
                            )
                            * lot_quantity
                        )
                        gross_total += gross
                        trade_commission = int(lot["entry_commission_i64"]) + exit_commission
                        trade_slippage = int(lot["entry_slippage_i64"]) + exit_slippage
                        trade_id = f"TRADE-{lot['entry_bar_index']}-{bar.bar_index}"
                        if any(value["trade_id"] == trade_id for value in trades):
                            trade_id = f"{trade_id}-{len(trades) + 1}"
                        trades.append(
                            {
                                "trade_id": trade_id,
                                "side": position["side"],
                                "entry_bar_index": lot["entry_bar_index"],
                                "entry_time": lot["entry_time"],
                                "entry_price_i64": lot["entry_price_i64"],
                                "exit_bar_index": bar.bar_index,
                                "exit_time": bar.timestamp_utc,
                                "exit_price_i64": fill_price,
                                "quantity": lot_quantity,
                                "gross_pnl_i64": gross,
                                "net_pnl_i64": gross - trade_commission,
                                "commission_i64": trade_commission,
                                "slippage_i64": trade_slippage,
                            }
                        )
                    cash += gross_total - commission
                    if _position_quantity(position) == 0:
                        position = None
            else:
                status, reason = "rejected", "POSITION_STATE_MISMATCH"
            orders.append(
                {
                    "order_id": order_id,
                    "signal_id": signal["signal_id"],
                    "created_at_bar_index": signal["known_at_bar_index"],
                    "fill_bar_index": bar.bar_index,
                    "action": action,
                    "quantity": quantity,
                    "status": status,
                    "reason_code": reason,
                }
            )
            if status == "filled":
                total_commission += commission
                total_slippage += slippage_cost
                fills.append(
                    {
                        "fill_id": f"FILL-{order_id.removeprefix('ORDER-')}",
                        "order_id": order_id,
                        "bar_index": bar.bar_index,
                        "timestamp_utc": bar.timestamp_utc,
                        "action": action,
                        "quantity": quantity,
                        "price_i64": fill_price,
                        "commission_i64": commission,
                        "slippage_i64": slippage_cost,
                    }
                )
        position_quantity = _position_quantity(position)
        unrealized = _position_unrealized(
            position,
            bar.close_i64,
            price_scale,
            float(execution["contract_multiplier"]),
            money_scale,
        )
        equity_value = cash + unrealized
        margin = (
            0
            if position is None
            else round(
                abs(
                    _money(
                        bar.close_i64,
                        price_scale,
                        float(execution["contract_multiplier"]),
                        money_scale,
                    )
                )
                * position_quantity
                * float(execution["margin_ratio"])
            )
        )
        peak = max(peak, equity_value)
        drawdown = 0.0 if peak <= 0 else (peak - equity_value) / peak
        positions.append(
            {
                "bar_index": bar.bar_index,
                "timestamp_utc": bar.timestamp_utc,
                "side": "flat" if position is None else position["side"],
                "quantity": position_quantity,
                "entry_price_i64": _position_entry_price(position),
                "unrealized_pnl_i64": unrealized,
            }
        )
        equity.append(
            {
                "bar_index": bar.bar_index,
                "timestamp_utc": bar.timestamp_utc,
                "equity_i64": equity_value,
                "cash_i64": cash,
                "available_i64": equity_value - margin,
                "margin_i64": margin,
                "drawdown": drawdown,
            }
        )
        if risk_config is not None and risk_context is not None and market_observation is not None:
            close_snapshot = _risk_snapshot(
                bar_index=bar.bar_index,
                equity_i64=equity_value,
                day_start_equity_i64=day_start_equity,
                peak_equity_i64=peak,
                position=position,
            )
            kill_reason = evaluate_portfolio_kill_switch(
                risk_config,
                risk_context,
                risk_state,
                close_snapshot,
                market_observation,
            )
            if kill_reason is not None:
                _append_risk_decision(
                    risk_decisions,
                    strategy.events,
                    context=risk_context,
                    observation_data_revision=market_observation.data_revision,
                    bar_index=bar.bar_index,
                    timestamp_utc=bar.timestamp_utc,
                    price_i64=bar.close_i64,
                    decision=_kill_switch_decision(risk_state, kill_reason),
                    signal=None,
                    requested_execution_bar_index=bar.bar_index,
                )
    for fill_index, pending in due.items():
        if fill_index <= end:
            continue
        for signal in pending:
            if risk_context is not None:
                final_bar = bars[-1]
                final_observation = risk_context.observation_at(final_bar.bar_index)
                _append_risk_decision(
                    risk_decisions,
                    strategy.events,
                    context=risk_context,
                    observation_data_revision=final_observation.data_revision,
                    bar_index=final_bar.bar_index,
                    timestamp_utc=final_bar.timestamp_utc,
                    price_i64=final_bar.close_i64,
                    decision=RiskDecision(
                        decision_type="blocked_decision",
                        reason_code="NO_NEXT_EXECUTABLE_BAR",
                        approved_action=None,
                        approved_quantity=0,
                        requested_target_risk_weight_ppm=0,
                        approved_target_risk_weight_ppm=0,
                        scheduled_execution_bar_index=None,
                        retryable=False,
                        kill_switch_active=risk_state.kill_switch_active,
                    ),
                    signal=signal,
                    requested_execution_bar_index=fill_index,
                )
                continue
            orders.append(
                {
                    "order_id": f"ORDER-{signal['signal_id']}",
                    "signal_id": signal["signal_id"],
                    "created_at_bar_index": signal["known_at_bar_index"],
                    "fill_bar_index": None,
                    "action": signal["action"],
                    "quantity": _trade_signal_quantity(signal),
                    "status": "rejected",
                    "reason_code": "NO_NEXT_BAR",
                }
            )
    strategy.events.sort(
        key=lambda event: (int(event["known_at_bar_index"]), int(event["event_seq"]))
    )
    for event_seq, event in enumerate(strategy.events):
        event["event_seq"] = event_seq
    summary = _summary(capital, equity, trades, total_commission, total_slippage, risk_decisions)
    output = guard.resolve(str(payload["output_path"]))
    if output.exists():
        raise ValueError("formal backtest run directory already exists")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.parent / f".{output.name}.tmp-{uuid.uuid4().hex}"
    temporary.mkdir()
    try:
        table_rows = {
            "indicator_values": strategy.indicator_values,
            "strategy_states": strategy.strategy_states,
            "stage_signals": strategy.stage_signals,
            "trade_signals": strategy.trade_signals,
            "risk_decisions": risk_decisions,
            "orders": orders,
            "fills": fills,
            "trades": trades,
            "positions": positions,
            "equity": equity,
        }
        fact_hashes: dict[str, str] = {}
        for name, rows in table_rows.items():
            path = temporary / f"{name}.parquet"
            pq.write_table(
                pa.Table.from_pylist(rows, schema=SCHEMAS[name]), path, compression="zstd"
            )
            fact_hashes[name] = _sha256(path)
        chart_path = temporary / "chart_events.parquet"
        pq.write_table(
            pa.Table.from_pylist(strategy.events, schema=EVENT_SCHEMA),
            chart_path,
            compression="zstd",
        )
        fact_hashes["chart_events"] = _sha256(chart_path)
        (temporary / "summary.json").write_text(
            json.dumps(summary, separators=(",", ":")), encoding="utf-8"
        )
        (temporary / "status.json").write_text(
            '{"status":"completed","progress":1}', encoding="utf-8"
        )
        if algorithm["algorithm_id"] == "aux_macd_zero_axis_defense":
            dependency_ids = ["macd"]
        elif algorithm["algorithm_id"] == "aux_boll_bardo_warning":
            dependency_ids = ["boll"]
        else:
            dependency_ids = ["ma"]
        if (
            algorithm["algorithm_id"] == "aux_ma_kiss_legacy"
            and parameters.get("enable_legacy_b1_macd_proxy") is True
        ):
            dependency_ids.append("macd")
        indicator_dependencies: list[dict[str, str]] = []
        for algorithm_id in dependency_ids:
            indicator = resolve(algorithm_id)
            assert indicator is not None
            indicator_dependencies.append(
                {
                    "algorithm_id": algorithm_id,
                    "version": indicator[0]["algorithm_version"],
                    "source_hash": indicator[0]["source_hash"],
                }
            )
        manifest = {
            "schema_version": 1,
            "run_id": payload["run_id"],
            "run_signature": payload["run_signature"],
            "trace_id": payload["trace_id"],
            "dataset": {
                "dataset_id": dataset["dataset_id"],
                "data_revision": dataset["data_revision"],
            },
            "range": range_value,
            "strategy": {
                "strategy_id": algorithm["algorithm_id"],
                "version": algorithm["algorithm_version"],
                "source_hash": algorithm["source_hash"],
                "parameters": parameters,
                "indicator_dependencies": indicator_dependencies,
            },
            "execution": execution_input,
            "capital": capital,
            "engine": {
                "engine_version": ENGINE_VERSION,
                "python_version": "3.14",
                "contract_version": CONTRACT_VERSION,
            },
            "random_seed": int(payload["random_seed"]),
            "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }
        if algorithm["algorithm_id"] == "aux_ma_sector_rotation":
            ranking_context = payload.get("ranking_context")
            if not isinstance(ranking_context, dict):
                raise ValueError("ranking_context is required for MA sector rotation")
            manifest["ranking_context"] = ranking_context
        if risk_overlay is not None:
            manifest["risk_overlay"] = risk_overlay[0]
        (temporary / "run.json").write_text(
            json.dumps(manifest, separators=(",", ":")), encoding="utf-8"
        )
        log_events = _formal_log_events(
            payload, strategy, risk_decisions, orders, fills, fact_hashes
        )
        (temporary / "log.ndjson").write_text(
            "".join(_format_formal_log_event(event) + "\n" for event in log_events),
            encoding="utf-8",
        )
        (temporary / "_SUCCESS").write_bytes(b"")
        os.replace(temporary, output)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return guard.relative(output)


def _formal_log_events(
    payload: dict[str, Any],
    strategy: Any,
    risk_decisions: list[dict[str, Any]],
    orders: list[dict[str, Any]],
    fills: list[dict[str, Any]],
    fact_hashes: dict[str, str],
) -> list[dict[str, Any]]:
    timestamp = datetime.now().astimezone()
    common = {
        "timestamp": timestamp,
        "level": "INFO",
        "source_file": "tvbt/backtest.py",
        "source_line": run_backtest.__code__.co_firstlineno,
        "source_function": "run_backtest",
        "run_id": payload["run_id"],
        "trace_id": payload["trace_id"],
    }
    result: list[dict[str, Any]] = []
    for state in strategy.strategy_states:
        result.append(
            {
                **common,
                "event": "strategy.state.changed",
                "message": "strategy state changed",
                "bar_index": state["known_at_bar_index"],
                "state_from": state["state_from"],
                "state_to": state["state_to"],
                "reason_code": state["reason_code"],
            }
        )
    for stage in strategy.stage_signals:
        result.append(
            {
                **common,
                "event": "strategy.stage.signal",
                "message": "strategy stage signal emitted",
                "bar_index": stage["known_at_bar_index"],
                "stage_signal_id": stage["stage_signal_id"],
                "stage": stage["stage"],
                "status": stage["status"],
                "reason_code": stage["reason_code"],
            }
        )
    for signal in strategy.trade_signals:
        result.append(
            {
                **common,
                "event": "strategy.trade.signal",
                "message": "trade signal emitted",
                "bar_index": signal["known_at_bar_index"],
                "signal_id": signal["signal_id"],
                "parent_stage_signal_id": signal["parent_stage_signal_id"],
                "action": signal["action"],
                "reason_code": signal["reason_code"],
            }
        )
    for decision in risk_decisions:
        result.append(
            {
                **common,
                "event": "risk.decision.recorded",
                "message": "risk overlay decision recorded",
                "bar_index": decision["known_at_bar_index"],
                "risk_decision_id": decision["risk_decision_id"],
                "signal_id": decision["signal_id"],
                "decision_type": decision["decision_type"],
                "reason_code": decision["reason_code"],
                "approved_quantity": decision["approved_quantity"],
            }
        )
    for order in orders:
        result.append(
            {
                **common,
                "event": "backtest.order.recorded",
                "message": "backtest order recorded",
                "bar_index": order["created_at_bar_index"],
                "order_id": order["order_id"],
                "signal_id": order["signal_id"],
                "status": order["status"],
                "reason_code": order["reason_code"],
            }
        )
    for fill in fills:
        result.append(
            {
                **common,
                "event": "backtest.fill.recorded",
                "message": "backtest fill recorded",
                "bar_index": fill["bar_index"],
                "fill_id": fill["fill_id"],
                "order_id": fill["order_id"],
                "action": fill["action"],
            }
        )
    result.append(
        {
            **common,
            "event": "backtest.completed",
            "message": "backtest run completed",
            "fact_hashes": fact_hashes,
            "python_runtime": platform.python_version(),
        }
    )
    return result


def _format_formal_log_event(event: dict[str, Any]) -> str:
    fields = {key: value for key, value in event.items() if key not in LOG_METADATA_FIELDS}
    timestamp = event["timestamp"]
    assert isinstance(timestamp, datetime)
    return format_fixed_text_entry(
        timestamp=timestamp,
        level=str(event["level"]),
        source_file=str(event["source_file"]),
        source_line=int(event["source_line"]),
        event=str(event["event"]),
        message=str(event["message"]),
        fields=fields,
    )


def _summary(
    capital: dict[str, Any],
    equity: list[dict[str, Any]],
    trades: list[dict[str, Any]],
    commission: int,
    slippage: int,
    risk_decisions: list[dict[str, Any]],
) -> dict[str, Any]:
    initial = int(capital["initial_cash_i64"])
    final = equity[-1]["equity_i64"] if equity else initial
    wins = [trade["net_pnl_i64"] for trade in trades if trade["net_pnl_i64"] > 0]
    losses = [trade["net_pnl_i64"] for trade in trades if trade["net_pnl_i64"] < 0]
    returns = [
        (equity[index]["equity_i64"] - equity[index - 1]["equity_i64"])
        / equity[index - 1]["equity_i64"]
        for index in range(1, len(equity))
        if equity[index - 1]["equity_i64"] != 0
    ]
    mean = sum(returns) / len(returns) if returns else 0
    variance = (
        sum((value - mean) ** 2 for value in returns) / (len(returns) - 1)
        if len(returns) > 1
        else 0
    )
    sharpe = mean / math.sqrt(variance) * math.sqrt(252) if variance > 0 else None
    profit_factor = sum(wins) / abs(sum(losses)) if losses else None
    average_win = round(sum(wins) / len(wins)) if wins else None
    average_loss = round(sum(losses) / len(losses)) if losses else None
    drawdown_end = max(equity, key=lambda row: row["drawdown"], default=None)
    drawdown_start = None
    if drawdown_end is not None:
        candidates = [row for row in equity if row["bar_index"] <= drawdown_end["bar_index"]]
        drawdown_start = max(candidates, key=lambda row: row["equity_i64"], default=None)
    return {
        "total_return": 0 if initial == 0 else (final - initial) / initial,
        "annualized_return": None,
        "annualized_return_reason": "timeframe_annualization_not_configured",
        "max_drawdown": max((row["drawdown"] for row in equity), default=0),
        "max_drawdown_start_bar_index": (
            None if drawdown_start is None else drawdown_start["bar_index"]
        ),
        "max_drawdown_end_bar_index": None if drawdown_end is None else drawdown_end["bar_index"],
        "sharpe": sharpe,
        "sharpe_risk_free_rate": 0,
        "sharpe_annualization_factor": 252,
        "trade_count": len(trades),
        "win_rate": len(wins) / len(trades) if trades else None,
        "average_win_i64": average_win,
        "average_loss_i64": average_loss,
        "profit_loss_ratio": abs(average_win / average_loss)
        if average_win is not None and average_loss not in (None, 0)
        else None,
        "profit_factor": profit_factor,
        "expectancy_i64": sum(trade["net_pnl_i64"] for trade in trades) / len(trades)
        if trades
        else None,
        "max_consecutive_wins": _streak(trades, True),
        "max_consecutive_losses": _streak(trades, False),
        "total_commission_i64": commission,
        "total_slippage_i64": slippage,
        "risk_approved_count": sum(
            value["decision_type"] == "approved_order_intent" for value in risk_decisions
        ),
        "risk_reduced_count": sum(
            value["decision_type"] == "reduced_order_intent" for value in risk_decisions
        ),
        "risk_blocked_count": sum(
            value["decision_type"] == "blocked_decision" for value in risk_decisions
        ),
        "risk_kill_switch_count": sum(
            value["decision_type"] == "kill_switch" for value in risk_decisions
        ),
        "long": {
            "trade_count": sum(trade["side"] == "long" for trade in trades),
            "net_pnl_i64": sum(trade["net_pnl_i64"] for trade in trades if trade["side"] == "long"),
        },
        "short": {
            "trade_count": sum(trade["side"] == "short" for trade in trades),
            "net_pnl_i64": sum(
                trade["net_pnl_i64"] for trade in trades if trade["side"] == "short"
            ),
        },
    }


def _streak(trades: list[dict[str, Any]], wins: bool) -> int:
    best = current = 0
    for trade in trades:
        matched = trade["net_pnl_i64"] > 0 if wins else trade["net_pnl_i64"] < 0
        current = current + 1 if matched else 0
        best = max(best, current)
    return best
