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
            ("price_i64", pa.int64()),
            ("reason_code", pa.string()),
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


def _fill_price(
    open_i64: int, action: str, execution: dict[str, Any], tick_size: int
) -> tuple[int, int]:
    slippage = execution["slippage"]
    selling = action in {"open_short", "close_long"}
    if slippage["mode"] == "ticks":
        delta = round(float(slippage["value"]) * tick_size)
    else:
        delta = round(open_i64 * float(slippage["value"]) / 10_000)
    return (open_i64 - delta if selling else open_i64 + delta), abs(delta)


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
    fills: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    positions: list[dict[str, Any]] = []
    equity: list[dict[str, Any]] = []
    total_commission = 0
    total_slippage = 0
    bars = [bar for bar in strategy.bars if start <= bar.bar_index <= end]
    for bar in bars:
        if cancelled.is_set():
            raise InterruptedError("backtest cancelled")
        for signal in due.get(bar.bar_index, []):
            action = str(signal["action"])
            order_id = f"ORDER-{signal['signal_id']}"
            source_price = (
                bar.open_i64 if execution["fill_timing"] == "next_bar_open" else bar.close_i64
            )
            fill_price, slippage_price = _fill_price(source_price, action, execution, tick_size)
            slippage_cost = _money(
                slippage_price, price_scale, float(execution["contract_multiplier"]), money_scale
            )
            notional = _money(
                fill_price, price_scale, float(execution["contract_multiplier"]), money_scale
            )
            commission = _commission(execution, notional, 1)
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
                        "entry_bar_index": bar.bar_index,
                        "entry_time": bar.timestamp_utc,
                        "entry_price_i64": fill_price,
                        "entry_commission_i64": commission,
                        "entry_slippage_i64": slippage_cost,
                    }
            elif (
                action == "close_short" and position is not None and position["side"] == "short"
            ) or (action == "close_long" and position is not None and position["side"] == "long"):
                price_delta = (
                    position["entry_price_i64"] - fill_price
                    if position["side"] == "short"
                    else fill_price - position["entry_price_i64"]
                )
                gross = _money(
                    price_delta,
                    price_scale,
                    float(execution["contract_multiplier"]),
                    money_scale,
                )
                cash += gross - commission
                trade_commission = int(position["entry_commission_i64"]) + commission
                trade_slippage = int(position["entry_slippage_i64"]) + slippage_cost
                trades.append(
                    {
                        "trade_id": f"TRADE-{position['entry_bar_index']}-{bar.bar_index}",
                        "side": position["side"],
                        "entry_bar_index": position["entry_bar_index"],
                        "entry_time": position["entry_time"],
                        "entry_price_i64": position["entry_price_i64"],
                        "exit_bar_index": bar.bar_index,
                        "exit_time": bar.timestamp_utc,
                        "exit_price_i64": fill_price,
                        "quantity": 1,
                        "gross_pnl_i64": gross,
                        "net_pnl_i64": gross - trade_commission,
                        "commission_i64": trade_commission,
                        "slippage_i64": trade_slippage,
                    }
                )
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
                    "quantity": 1,
                    "status": status,
                    "reason_code": reason,
                }
            )
            if status == "filled":
                total_commission += commission
                total_slippage += slippage_cost
                fills.append(
                    {
                        "fill_id": f"FILL-{signal['signal_id']}",
                        "order_id": order_id,
                        "bar_index": bar.bar_index,
                        "timestamp_utc": bar.timestamp_utc,
                        "action": action,
                        "quantity": 1,
                        "price_i64": fill_price,
                        "commission_i64": commission,
                        "slippage_i64": slippage_cost,
                    }
                )
        unrealized_delta = (
            0
            if position is None
            else (
                position["entry_price_i64"] - bar.close_i64
                if position["side"] == "short"
                else bar.close_i64 - position["entry_price_i64"]
            )
        )
        unrealized = _money(
            unrealized_delta,
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
                "quantity": 0 if position is None else 1,
                "entry_price_i64": None if position is None else position["entry_price_i64"],
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
    for fill_index, pending in due.items():
        if fill_index <= end:
            continue
        for signal in pending:
            orders.append(
                {
                    "order_id": f"ORDER-{signal['signal_id']}",
                    "signal_id": signal["signal_id"],
                    "created_at_bar_index": signal["known_at_bar_index"],
                    "fill_bar_index": None,
                    "action": signal["action"],
                    "quantity": 1,
                    "status": "rejected",
                    "reason_code": "NO_NEXT_BAR",
                }
            )
    summary = _summary(capital, equity, trades, total_commission, total_slippage)
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
        indicator = resolve("ma")
        assert indicator is not None
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
                "indicator_dependencies": [
                    {
                        "algorithm_id": "ma",
                        "version": indicator[0]["algorithm_version"],
                        "source_hash": indicator[0]["source_hash"],
                    }
                ],
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
        (temporary / "run.json").write_text(
            json.dumps(manifest, separators=(",", ":")), encoding="utf-8"
        )
        log_events = _formal_log_events(payload, strategy, orders, fills, fact_hashes)
        (temporary / "log.ndjson").write_text(
            "".join(json.dumps(event, separators=(",", ":")) + "\n" for event in log_events),
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
    orders: list[dict[str, Any]],
    fills: list[dict[str, Any]],
    fact_hashes: dict[str, str],
) -> list[dict[str, Any]]:
    timestamp = datetime.now(UTC).isoformat().replace("+00:00", "Z")
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


def _summary(
    capital: dict[str, Any],
    equity: list[dict[str, Any]],
    trades: list[dict[str, Any]],
    commission: int,
    slippage: int,
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
