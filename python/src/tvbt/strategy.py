from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import pyarrow.parquet as pq

from tvbt.chan.algorithm import definition as chan_definition
from tvbt.chan.algorithm import run_chan
from tvbt.storage.path_guard import PathGuard


def _source_hash() -> str:
    digest = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    return "sha256:" + digest


def definition() -> dict[str, Any]:
    return {
        "kind": "strategy",
        "algorithm_id": "ma20_retest_short",
        "algorithm_version": "1.0.0",
        "source_hash": _source_hash(),
        "name": "MA20 Retest Failure Short",
        "input_schema": "bars.v1",
        "parameter_schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "ma_period": {"type": "integer", "minimum": 2, "maximum": 500, "default": 20},
                "touch_tolerance_ticks": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 100,
                    "default": 1,
                },
                "max_retest_bars": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 500,
                    "default": 20,
                },
            },
            "required": ["ma_period", "touch_tolerance_ticks", "max_retest_bars"],
        },
        "outputs": [
            {
                "name": kind,
                "display_name": label,
                "pane": "main",
                "series_type": "semantic_objects",
                "object_type": kind,
            }
            for kind, label in (
                ("strategy_state", "策略状态"),
                ("stage_signal", "阶段信号"),
                ("trade_signal", "交易信号"),
                ("chart_event", "图表事件"),
            )
        ],
        "warmup": {"kind": "formula", "expression": "ma_period"},
        "causal": True,
    }


def fixed_level_centre_definition() -> dict[str, Any]:
    digest = hashlib.sha256()
    digest.update(Path(__file__).read_bytes())
    digest.update(chan_definition()["source_hash"].encode())
    return {
        "kind": "strategy",
        "algorithm_id": "fixed_level_centre_decision_tree",
        "algorithm_version": "1.0.0",
        "source_hash": "sha256:" + digest.hexdigest(),
        "name": "固定级别中枢决策树",
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
                "allow_long": {"type": "boolean", "default": True},
                "allow_short": {"type": "boolean", "default": True},
            },
            "required": ["checkpoint_interval", "allow_long", "allow_short"],
        },
        "outputs": [
            {
                "name": kind,
                "display_name": label,
                "pane": "main",
                "series_type": "semantic_objects",
                "object_type": kind,
            }
            for kind, label in (
                ("strategy_state", "中枢决策状态"),
                ("stage_signal", "状态转换"),
                ("trade_signal", "交易信号"),
                ("chart_event", "策略图表事件"),
            )
        ],
        "warmup": {"kind": "formula", "expression": "full-history causal Chan state"},
        "causal": True,
    }


def _chan_strategy_definition(algorithm_id: str, name: str, output_prefix: str) -> dict[str, Any]:
    digest = hashlib.sha256()
    digest.update(Path(__file__).read_bytes())
    digest.update(chan_definition()["source_hash"].encode())
    digest.update(algorithm_id.encode())
    return {
        "kind": "strategy",
        "algorithm_id": algorithm_id,
        "algorithm_version": "1.0.0",
        "source_hash": "sha256:" + digest.hexdigest(),
        "name": name,
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
                }
            },
            "required": ["checkpoint_interval"],
        },
        "outputs": [
            {
                "name": kind,
                "display_name": label,
                "pane": "main",
                "series_type": "semantic_objects",
                "object_type": kind,
            }
            for kind, label in (
                ("strategy_state", f"{output_prefix}状态"),
                ("stage_signal", f"{output_prefix}阶段"),
                ("trade_signal", f"{output_prefix}交易信号"),
                ("chart_event", f"{output_prefix}图表事件"),
            )
        ],
        "warmup": {"kind": "formula", "expression": "full-history causal Chan state"},
        "causal": True,
    }


def downtrend_reversal_definition() -> dict[str, Any]:
    return _chan_strategy_definition("downtrend_reversal_only", "下跌趋势一买反转", "一买反转")


def trend_divergence_reversal_definition() -> dict[str, Any]:
    return _chan_strategy_definition(
        "trend_divergence_reversal", "趋势背驰双向反转", "趋势背驰反转"
    )


def consolidation_reversion_definition() -> dict[str, Any]:
    return _chan_strategy_definition(
        "consolidation_divergence_centre_reversion",
        "盘整背驰中枢回归",
        "中枢回归",
    )


def third_point_migration_definition() -> dict[str, Any]:
    return _chan_strategy_definition(
        "third_buy_centre_migration_hold",
        "三买三卖中枢迁移持有",
        "中枢迁移持有",
    )


def first_centre_rotation_definition() -> dict[str, Any]:
    return _chan_strategy_definition(
        "first_centre_B3_rotation",
        "首中枢三买三卖轮动",
        "首中枢轮动",
    )


def definitions() -> list[dict[str, Any]]:
    return [
        definition(),
        fixed_level_centre_definition(),
        downtrend_reversal_definition(),
        trend_divergence_reversal_definition(),
        consolidation_reversion_definition(),
        third_point_migration_definition(),
        first_centre_rotation_definition(),
    ]


@dataclass(frozen=True)
class StrategyBar:
    bar_index: int
    timestamp_utc: int
    open_i64: int
    high_i64: int
    low_i64: int
    close_i64: int


@dataclass
class StrategyState:
    name: str = "waiting_break"
    stage_signal_id: str | None = None
    stage_started: int | None = None
    previous_close: int | None = None
    previous_ma: float | None = None


@dataclass
class Transition:
    state: StrategyState
    state_changes: list[dict[str, Any]] = field(default_factory=list)
    stage_signals: list[dict[str, Any]] = field(default_factory=list)
    trade_signals: list[dict[str, Any]] = field(default_factory=list)
    chart_events: list[dict[str, Any]] = field(default_factory=list)


class Strategy(Protocol):
    def metadata(self) -> dict[str, Any]: ...
    def initialize(self) -> StrategyState: ...
    def on_bar(
        self, state: StrategyState, bar: StrategyBar, snapshot: dict[str, float]
    ) -> Transition: ...
    def finalize(self, state: StrategyState) -> dict[str, Any]: ...


class MA20RetestShort:
    def __init__(self, parameters: dict[str, Any], tick_size_i64: int) -> None:
        self.period = int(parameters["ma_period"])
        self.tolerance = int(parameters["touch_tolerance_ticks"]) * max(1, tick_size_i64)
        self.max_retest = int(parameters["max_retest_bars"])

    def metadata(self) -> dict[str, Any]:
        return definition()

    def initialize(self) -> StrategyState:
        return StrategyState()

    def on_bar(
        self, state: StrategyState, bar: StrategyBar, snapshot: dict[str, float]
    ) -> Transition:
        ma = snapshot["ma"]
        previous_name = state.name
        reason: str | None = None
        stage_signals: list[dict[str, Any]] = []
        trade_signals: list[dict[str, Any]] = []
        if (
            state.name == "waiting_break"
            and state.previous_close is not None
            and state.previous_ma is not None
            and state.previous_close >= state.previous_ma
            and bar.close_i64 < ma
        ):
            state.name = "waiting_retest"
            state.stage_started = bar.bar_index
            state.stage_signal_id = f"STAGE-{bar.bar_index}-BREAK-DOWN"
            reason = "CLOSE_BROKE_BELOW_MA"
            stage_signals.append(
                {
                    "stage_signal_id": state.stage_signal_id,
                    "known_at_bar_index": bar.bar_index,
                    "stage": "break_down",
                    "status": "opened",
                    "price_i64": bar.close_i64,
                    "reason_code": reason,
                }
            )
        elif state.name in {"waiting_retest", "waiting_retest_failure"}:
            age = bar.bar_index - int(state.stage_started or bar.bar_index)
            if age > self.max_retest:
                state.name = "waiting_break"
                reason = "RETEST_TIMEOUT"
                stage_signals.append(self._close_stage(state, bar, "expired", reason))
            elif state.name == "waiting_retest" and bar.high_i64 >= ma - self.tolerance:
                state.name = "waiting_retest_failure"
                reason = "PRICE_TOUCHED_MA"
                stage_signals.append(
                    {
                        "stage_signal_id": state.stage_signal_id,
                        "known_at_bar_index": bar.bar_index,
                        "stage": "retest",
                        "status": "updated",
                        "price_i64": bar.high_i64,
                        "reason_code": reason,
                    }
                )
            elif state.name == "waiting_retest_failure" and bar.close_i64 > ma:
                state.name = "waiting_break"
                reason = "RETEST_RECLAIMED_MA"
                stage_signals.append(self._close_stage(state, bar, "invalidated", reason))
            elif (
                state.name == "waiting_retest_failure"
                and bar.close_i64 < bar.open_i64
                and bar.close_i64 < ma
            ):
                state.name = "short_open"
                reason = "RETEST_FAILED_BREAK_OPEN"
                signal_id = f"SIG-{bar.bar_index}-SHORT-OPEN"
                trade_signals.append(
                    {
                        "signal_id": signal_id,
                        "parent_stage_signal_id": state.stage_signal_id,
                        "known_at_bar_index": bar.bar_index,
                        "side": "short",
                        "action": "open_short",
                        "price_i64": bar.close_i64,
                        "reason_code": reason,
                    }
                )
                stage_signals.append(self._close_stage(state, bar, "completed", reason))
        elif state.name == "short_open" and bar.close_i64 > ma:
            state.name = "waiting_break"
            reason = "CLOSE_RECOVERED_ABOVE_MA"
            trade_signals.append(
                {
                    "signal_id": f"SIG-{bar.bar_index}-SHORT-CLOSE",
                    "parent_stage_signal_id": None,
                    "known_at_bar_index": bar.bar_index,
                    "side": "long",
                    "action": "close_short",
                    "price_i64": bar.close_i64,
                    "reason_code": reason,
                }
            )
        changes: list[dict[str, Any]] = []
        if state.name != previous_name:
            changes.append(
                {
                    "known_at_bar_index": bar.bar_index,
                    "state_from": previous_name,
                    "state_to": state.name,
                    "reason_code": reason,
                }
            )
        state.previous_close = bar.close_i64
        state.previous_ma = ma
        chart_events = [
            {
                "event_id": signal["signal_id"],
                "known_at_bar_index": bar.bar_index,
                "bar_index": bar.bar_index,
                "timestamp_utc": bar.timestamp_utc,
                "price_i64": signal["price_i64"],
                "event_type": signal["action"],
                "reason_code": signal["reason_code"],
            }
            for signal in trade_signals
        ]
        return Transition(state, changes, stage_signals, trade_signals, chart_events)

    @staticmethod
    def _close_stage(
        state: StrategyState, bar: StrategyBar, status: str, reason: str
    ) -> dict[str, Any]:
        return {
            "stage_signal_id": state.stage_signal_id,
            "known_at_bar_index": bar.bar_index,
            "stage": "retest",
            "status": status,
            "price_i64": bar.close_i64,
            "reason_code": reason,
        }

    def finalize(self, state: StrategyState) -> dict[str, Any]:
        return {"final_state": state.name}


@dataclass
class StrategyRun:
    bars: list[StrategyBar]
    indicator_values: list[dict[str, Any]]
    strategy_states: list[dict[str, Any]]
    stage_signals: list[dict[str, Any]]
    trade_signals: list[dict[str, Any]]
    chart_events: list[dict[str, Any]]
    events: list[dict[str, Any]]


def run_strategy(
    payload: dict[str, Any],
    guard: PathGuard,
    cancelled: threading.Event,
    *,
    last_bar_index: int | None = None,
) -> StrategyRun:
    dataset = payload.get("dataset")
    algorithm = payload.get("algorithm")
    parameters = payload.get("parameters")
    if (
        not isinstance(dataset, dict)
        or not isinstance(algorithm, dict)
        or not isinstance(parameters, dict)
    ):
        raise ValueError("dataset, algorithm and parameters are required")
    expected = next(
        (
            value
            for value in definitions()
            if value["algorithm_id"] == algorithm.get("algorithm_id")
        ),
        None,
    )
    if expected is None:
        raise ValueError("strategy algorithm_id is not supported")
    for key in ("kind", "algorithm_id", "algorithm_version", "source_hash"):
        if algorithm.get(key) != expected[key]:
            raise ValueError(f"strategy {key} does not match engine definition")
    if algorithm["algorithm_id"] == "fixed_level_centre_decision_tree":
        return _run_fixed_level_centre(payload, guard, cancelled, last_bar_index=last_bar_index)
    if algorithm["algorithm_id"] in {
        "downtrend_reversal_only",
        "trend_divergence_reversal",
    }:
        return _run_trend_reversal(
            payload,
            guard,
            cancelled,
            last_bar_index=last_bar_index,
            long_only=algorithm["algorithm_id"] == "downtrend_reversal_only",
        )
    if algorithm["algorithm_id"] == "consolidation_divergence_centre_reversion":
        return _run_consolidation_reversion(
            payload, guard, cancelled, last_bar_index=last_bar_index
        )
    if algorithm["algorithm_id"] == "third_buy_centre_migration_hold":
        return _run_third_point_migration_hold(
            payload, guard, cancelled, last_bar_index=last_bar_index
        )
    if algorithm["algorithm_id"] == "first_centre_B3_rotation":
        return _run_third_point_migration_hold(
            payload,
            guard,
            cancelled,
            last_bar_index=last_bar_index,
            first_only=True,
        )
    meta = json.loads(guard.resolve(str(dataset["meta_path"])).read_text(encoding="utf-8"))
    tick_size = int(meta["price"].get("tick_size_i64") or 1)
    table = pq.read_table(
        guard.resolve(str(dataset["bars_path"])),
        columns=["bar_index", "timestamp_utc", "open_i64", "high_i64", "low_i64", "close_i64"],
    ).to_pydict()
    strategy: Strategy = MA20RetestShort(parameters, tick_size)
    state = strategy.initialize()
    period = int(parameters["ma_period"])
    closes: list[int] = []
    bars: list[StrategyBar] = []
    indicator_values: list[dict[str, Any]] = []
    states: list[dict[str, Any]] = []
    stages: list[dict[str, Any]] = []
    signals: list[dict[str, Any]] = []
    chart_events: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    revisions: dict[tuple[str, str], int] = {}
    for position, value in enumerate(table["bar_index"]):
        bar_index = int(value)
        if last_bar_index is not None and bar_index > last_bar_index:
            break
        if position % 256 == 0 and cancelled.is_set():
            raise InterruptedError("strategy execution cancelled")
        bar = StrategyBar(
            bar_index,
            int(table["timestamp_utc"][position]),
            int(table["open_i64"][position]),
            int(table["high_i64"][position]),
            int(table["low_i64"][position]),
            int(table["close_i64"][position]),
        )
        bars.append(bar)
        closes.append(bar.close_i64)
        ma = sum(closes[-period:]) / period if len(closes) >= period else None
        indicator_values.append({"bar_index": bar_index, "ma": ma})
        if ma is None:
            state.previous_close = bar.close_i64
            continue
        transition = strategy.on_bar(state, bar, {"ma": ma})
        for object_type, id_field, rows in (
            ("strategy_state", "", transition.state_changes),
            ("stage_signal", "stage_signal_id", transition.stage_signals),
            ("trade_signal", "signal_id", transition.trade_signals),
            ("chart_event", "event_id", transition.chart_events),
        ):
            for row in rows:
                rows_target = {
                    "strategy_state": states,
                    "stage_signal": stages,
                    "trade_signal": signals,
                    "chart_event": chart_events,
                }[object_type]
                object_id = str(row.get(id_field) or f"{object_type}-{bar_index}")
                revision_key = (object_type, object_id)
                revision = revisions.get(revision_key, 0) + 1
                revisions[revision_key] = revision
                rows_target.append({**row, "object_revision": revision})
                payload_value = {**row, "object_id": object_id, "object_revision": revision}
                events.append(
                    {
                        "event_seq": len(events) + 1,
                        "known_at_bar_index": bar_index,
                        "object_type": object_type,
                        "object_id": object_id,
                        "operation": "upsert",
                        "object_revision": revision,
                        "payload_json": json.dumps(payload_value, separators=(",", ":")),
                    }
                )
    strategy.finalize(state)
    return StrategyRun(bars, indicator_values, states, stages, signals, chart_events, events)


def _run_fixed_level_centre(
    payload: dict[str, Any],
    guard: PathGuard,
    cancelled: threading.Event,
    *,
    last_bar_index: int | None,
) -> StrategyRun:
    dataset = payload["dataset"]
    parameters = payload["parameters"]
    chan = chan_definition()
    chan_payload = {
        "dataset": dataset,
        "algorithm": {
            key: chan[key] for key in ("kind", "algorithm_id", "algorithm_version", "source_hash")
        },
        "parameters": {"checkpoint_interval": int(parameters["checkpoint_interval"])},
    }
    runtime, _, _ = run_chan(
        chan_payload, guard, cancelled, last_bar_index=last_bar_index, write_checkpoints=False
    )
    table = pq.read_table(
        guard.resolve(str(dataset["bars_path"])),
        columns=["bar_index", "timestamp_utc", "open_i64", "high_i64", "low_i64", "close_i64"],
    ).to_pydict()
    bars: list[StrategyBar] = []
    for row_position, value in enumerate(table["bar_index"]):
        bar_index = int(value)
        if last_bar_index is not None and bar_index > last_bar_index:
            break
        bars.append(
            StrategyBar(
                bar_index,
                int(table["timestamp_utc"][row_position]),
                int(table["open_i64"][row_position]),
                int(table["high_i64"][row_position]),
                int(table["low_i64"][row_position]),
                int(table["close_i64"][row_position]),
            )
        )
    events_by_bar: dict[int, list[Any]] = {}
    for event in runtime.emitter.events:
        events_by_bar.setdefault(event.known_at_bar_index, []).append(event)

    centers: dict[str, dict[str, Any]] = {}
    third_points: dict[str, dict[str, Any]] = {}
    current_state = "waiting_for_centre"
    position_side = "flat"
    states: list[dict[str, Any]] = []
    stages: list[dict[str, Any]] = []
    signals: list[dict[str, Any]] = []
    chart_events: list[dict[str, Any]] = []
    causal_events: list[dict[str, Any]] = []
    revisions: dict[tuple[str, str], int] = {}

    def publish(object_type: str, object_id: str, row: dict[str, Any], bar_index: int) -> None:
        revision_key = (object_type, object_id)
        revision = revisions.get(revision_key, 0) + 1
        revisions[revision_key] = revision
        target = {
            "strategy_state": states,
            "stage_signal": stages,
            "trade_signal": signals,
            "chart_event": chart_events,
        }[object_type]
        target.append({**row, "object_revision": revision})
        event_payload = {**row, "object_id": object_id, "object_revision": revision}
        causal_events.append(
            {
                "event_seq": len(causal_events) + 1,
                "known_at_bar_index": bar_index,
                "object_type": object_type,
                "object_id": object_id,
                "operation": "upsert",
                "object_revision": revision,
                "payload_json": json.dumps(
                    event_payload, ensure_ascii=False, separators=(",", ":")
                ),
            }
        )

    def trade(bar: StrategyBar, action: str, reason: str, center_id: str) -> None:
        nonlocal position_side
        signal_id = f"CHAN-FIXED-{bar.bar_index}-{action}-{center_id[-8:]}"
        side = "long" if action in {"open_long", "close_short"} else "short"
        publish(
            "trade_signal",
            signal_id,
            {
                "signal_id": signal_id,
                "parent_stage_signal_id": f"CHAN-STATE-{bar.bar_index}-{center_id[-8:]}",
                "known_at_bar_index": bar.bar_index,
                "timestamp_utc": bar.timestamp_utc,
                "side": side,
                "action": action,
                "price_i64": bar.close_i64,
                "reason_code": reason,
            },
            bar.bar_index,
        )
        event_id = f"EVENT-{signal_id}"
        publish(
            "chart_event",
            event_id,
            {
                "event_id": event_id,
                "known_at_bar_index": bar.bar_index,
                "bar_index": bar.bar_index,
                "timestamp_utc": bar.timestamp_utc,
                "price_i64": bar.close_i64,
                "event_type": action,
                "reason_code": reason,
            },
            bar.bar_index,
        )
        position_side = {
            "open_long": "long",
            "open_short": "short",
            "close_long": "flat",
            "close_short": "flat",
        }[action]

    for bar in bars:
        if bar.bar_index % 256 == 0 and cancelled.is_set():
            raise InterruptedError("strategy execution cancelled")
        for event in events_by_bar.get(bar.bar_index, []):
            if event.object_type not in {"segment_zhongshu", "trade_point"}:
                continue
            target = centers if event.object_type == "segment_zhongshu" else third_points
            if event.operation == "delete":
                target.pop(event.object_id, None)
                continue
            value = json.loads(event.payload_json)
            if event.object_type == "trade_point" and value.get("signal_type") not in {
                "buy_3",
                "sell_3",
            }:
                continue
            target[event.object_id] = value
        confirmed_centers = [
            value
            for value in centers.values()
            if value.get("confirmed") and int(value["known_at_bar_index"]) <= bar.bar_index
        ]
        if not confirmed_centers:
            continue
        center = max(
            confirmed_centers,
            key=lambda value: (int(value["start_bar_index"]), int(value["known_at_bar_index"])),
        )
        center_id = str(center["object_id"])
        related_types = {
            str(value["signal_type"])
            for value in third_points.values()
            if value.get("reference_object_id") == center_id
            and int(value["known_at_bar_index"]) <= bar.bar_index
        }
        if bar.close_i64 > int(center["zg_i64"]):
            next_state = "above_with_B3" if "buy_3" in related_types else "above_without_B3"
        elif bar.close_i64 < int(center["zd_i64"]):
            next_state = "below_with_S3" if "sell_3" in related_types else "below_without_S3"
        else:
            next_state = "inside"
        if next_state == current_state:
            continue
        previous = current_state
        current_state = next_state
        reason = f"CENTRE_STATE_{next_state.upper()}"
        state_id = f"strategy_state-{bar.bar_index}"
        publish(
            "strategy_state",
            state_id,
            {
                "known_at_bar_index": bar.bar_index,
                "timestamp_utc": bar.timestamp_utc,
                "state_from": previous,
                "state_to": next_state,
                "price_i64": bar.close_i64,
                "reason_code": reason,
            },
            bar.bar_index,
        )
        stage_id = f"CHAN-STATE-{bar.bar_index}-{center_id[-8:]}"
        publish(
            "stage_signal",
            stage_id,
            {
                "stage_signal_id": stage_id,
                "known_at_bar_index": bar.bar_index,
                "timestamp_utc": bar.timestamp_utc,
                "stage": next_state,
                "status": "confirmed",
                "price_i64": bar.close_i64,
                "reason_code": reason,
            },
            bar.bar_index,
        )
        if position_side == "long" and next_state in {
            "inside",
            "below_without_S3",
            "below_with_S3",
        }:
            trade(bar, "close_long", "CENTRE_LONG_INVALIDATED", center_id)
        elif position_side == "short" and next_state in {
            "inside",
            "above_without_B3",
            "above_with_B3",
        }:
            trade(bar, "close_short", "CENTRE_SHORT_INVALIDATED", center_id)
        if (
            next_state == "above_with_B3"
            and bool(parameters["allow_long"])
            and position_side == "flat"
        ):
            trade(bar, "open_long", "CONFIRMED_B3_ABOVE_CENTRE", center_id)
        elif (
            next_state == "below_with_S3"
            and bool(parameters["allow_short"])
            and position_side == "flat"
        ):
            trade(bar, "open_short", "CONFIRMED_S3_BELOW_CENTRE", center_id)

    indicator_values = [{"bar_index": bar.bar_index, "ma": None} for bar in bars]
    return StrategyRun(bars, indicator_values, states, stages, signals, chart_events, causal_events)


def _run_trend_reversal(
    payload: dict[str, Any],
    guard: PathGuard,
    cancelled: threading.Event,
    *,
    last_bar_index: int | None,
    long_only: bool,
) -> StrategyRun:
    dataset = payload["dataset"]
    parameters = payload["parameters"]
    chan = chan_definition()
    chan_payload = {
        "dataset": dataset,
        "algorithm": {
            key: chan[key] for key in ("kind", "algorithm_id", "algorithm_version", "source_hash")
        },
        "parameters": {"checkpoint_interval": int(parameters["checkpoint_interval"])},
    }
    runtime, _, _ = run_chan(
        chan_payload, guard, cancelled, last_bar_index=last_bar_index, write_checkpoints=False
    )
    table = pq.read_table(
        guard.resolve(str(dataset["bars_path"])),
        columns=["bar_index", "timestamp_utc", "open_i64", "high_i64", "low_i64", "close_i64"],
    ).to_pydict()
    bars: list[StrategyBar] = []
    for row_position, value in enumerate(table["bar_index"]):
        bar_index = int(value)
        if last_bar_index is not None and bar_index > last_bar_index:
            break
        bars.append(
            StrategyBar(
                bar_index,
                int(table["timestamp_utc"][row_position]),
                int(table["open_i64"][row_position]),
                int(table["high_i64"][row_position]),
                int(table["low_i64"][row_position]),
                int(table["close_i64"][row_position]),
            )
        )

    events_by_bar: dict[int, list[Any]] = {}
    for event in runtime.emitter.events:
        if event.object_type == "trade_point" and event.operation == "upsert":
            events_by_bar.setdefault(event.known_at_bar_index, []).append(event)

    strategy_id = "DOWNREV" if long_only else "TRENDREV"
    current_state = "waiting_B1" if long_only else "waiting_trend_divergence"
    position_side = "flat"
    states: list[dict[str, Any]] = []
    stages: list[dict[str, Any]] = []
    signals: list[dict[str, Any]] = []
    chart_events: list[dict[str, Any]] = []
    causal_events: list[dict[str, Any]] = []
    revisions: dict[tuple[str, str], int] = {}

    def publish(object_type: str, object_id: str, row: dict[str, Any], bar_index: int) -> None:
        revision_key = (object_type, object_id)
        revision = revisions.get(revision_key, 0) + 1
        revisions[revision_key] = revision
        target = {
            "strategy_state": states,
            "stage_signal": stages,
            "trade_signal": signals,
            "chart_event": chart_events,
        }[object_type]
        target.append({**row, "object_revision": revision})
        event_payload = {**row, "object_id": object_id, "object_revision": revision}
        causal_events.append(
            {
                "event_seq": len(causal_events) + 1,
                "known_at_bar_index": bar_index,
                "object_type": object_type,
                "object_id": object_id,
                "operation": "upsert",
                "object_revision": revision,
                "payload_json": json.dumps(
                    event_payload, ensure_ascii=False, separators=(",", ":")
                ),
            }
        )

    def trade(bar: StrategyBar, action: str, reason: str, source_id: str) -> None:
        nonlocal position_side
        signal_id = f"CHAN-{strategy_id}-{bar.bar_index}-{action}-{source_id[-8:]}"
        side = "long" if action in {"open_long", "close_short"} else "short"
        parent_id = f"CHAN-{strategy_id}-STAGE-{bar.bar_index}-{source_id[-8:]}"
        publish(
            "trade_signal",
            signal_id,
            {
                "signal_id": signal_id,
                "parent_stage_signal_id": parent_id,
                "known_at_bar_index": bar.bar_index,
                "timestamp_utc": bar.timestamp_utc,
                "side": side,
                "action": action,
                "price_i64": bar.close_i64,
                "reason_code": reason,
                "reference_object_id": source_id,
            },
            bar.bar_index,
        )
        event_id = f"EVENT-{signal_id}"
        publish(
            "chart_event",
            event_id,
            {
                "event_id": event_id,
                "known_at_bar_index": bar.bar_index,
                "bar_index": bar.bar_index,
                "timestamp_utc": bar.timestamp_utc,
                "price_i64": bar.close_i64,
                "event_type": action,
                "reason_code": reason,
                "reference_object_id": source_id,
            },
            bar.bar_index,
        )
        position_side = {
            "open_long": "long",
            "open_short": "short",
            "close_long": "flat",
            "close_short": "flat",
        }[action]

    for bar in bars:
        if bar.bar_index % 256 == 0 and cancelled.is_set():
            raise InterruptedError("strategy execution cancelled")
        for event in events_by_bar.get(bar.bar_index, []):
            point = json.loads(event.payload_json)
            signal_type = str(point.get("signal_type"))
            if point.get("signal_class") != "standard" or signal_type not in {"buy_1", "sell_1"}:
                continue
            if long_only and signal_type == "sell_1" and position_side == "flat":
                continue

            source_id = event.object_id
            next_state = "long_after_B1" if signal_type == "buy_1" else "short_after_S1"
            reason = "CONFIRMED_TREND_B1" if signal_type == "buy_1" else "CONFIRMED_TREND_S1"
            previous_state = current_state
            current_state = next_state
            state_id = f"strategy_state-{strategy_id}-{bar.bar_index}-{source_id[-8:]}"
            publish(
                "strategy_state",
                state_id,
                {
                    "known_at_bar_index": bar.bar_index,
                    "timestamp_utc": bar.timestamp_utc,
                    "state_from": previous_state,
                    "state_to": next_state,
                    "price_i64": bar.close_i64,
                    "reason_code": reason,
                    "reference_object_id": source_id,
                },
                bar.bar_index,
            )
            stage_id = f"CHAN-{strategy_id}-STAGE-{bar.bar_index}-{source_id[-8:]}"
            publish(
                "stage_signal",
                stage_id,
                {
                    "stage_signal_id": stage_id,
                    "known_at_bar_index": bar.bar_index,
                    "timestamp_utc": bar.timestamp_utc,
                    "stage": next_state,
                    "status": "confirmed",
                    "price_i64": bar.close_i64,
                    "reason_code": reason,
                    "reference_object_id": source_id,
                },
                bar.bar_index,
            )

            if signal_type == "buy_1":
                if position_side == "short":
                    trade(bar, "close_short", "OPPOSITE_CONFIRMED_B1", source_id)
                if position_side == "flat":
                    trade(bar, "open_long", reason, source_id)
            else:
                if position_side == "long":
                    trade(bar, "close_long", "OPPOSITE_CONFIRMED_S1", source_id)
                if not long_only and position_side == "flat":
                    trade(bar, "open_short", reason, source_id)

    indicator_values = [{"bar_index": bar.bar_index, "ma": None} for bar in bars]
    return StrategyRun(bars, indicator_values, states, stages, signals, chart_events, causal_events)


def _run_consolidation_reversion(
    payload: dict[str, Any],
    guard: PathGuard,
    cancelled: threading.Event,
    *,
    last_bar_index: int | None,
) -> StrategyRun:
    dataset = payload["dataset"]
    parameters = payload["parameters"]
    chan = chan_definition()
    chan_payload = {
        "dataset": dataset,
        "algorithm": {
            key: chan[key] for key in ("kind", "algorithm_id", "algorithm_version", "source_hash")
        },
        "parameters": {"checkpoint_interval": int(parameters["checkpoint_interval"])},
    }
    runtime, _, _ = run_chan(
        chan_payload, guard, cancelled, last_bar_index=last_bar_index, write_checkpoints=False
    )
    table = pq.read_table(
        guard.resolve(str(dataset["bars_path"])),
        columns=["bar_index", "timestamp_utc", "open_i64", "high_i64", "low_i64", "close_i64"],
    ).to_pydict()
    bars = [
        StrategyBar(
            int(value),
            int(table["timestamp_utc"][row_position]),
            int(table["open_i64"][row_position]),
            int(table["high_i64"][row_position]),
            int(table["low_i64"][row_position]),
            int(table["close_i64"][row_position]),
        )
        for row_position, value in enumerate(table["bar_index"])
        if last_bar_index is None or int(value) <= last_bar_index
    ]
    events_by_bar: dict[int, list[Any]] = {}
    for event in runtime.emitter.events:
        if event.object_type in {"segment_zhongshu", "divergence", "trade_point"}:
            events_by_bar.setdefault(event.known_at_bar_index, []).append(event)

    centers: dict[str, dict[str, Any]] = {}
    divergences: dict[str, dict[str, Any]] = {}
    current_state = "waiting_consolidation_divergence"
    position_side = "flat"
    active_center_id: str | None = None
    opened_at: int | None = None
    states: list[dict[str, Any]] = []
    stages: list[dict[str, Any]] = []
    signals: list[dict[str, Any]] = []
    chart_events: list[dict[str, Any]] = []
    causal_events: list[dict[str, Any]] = []
    revisions: dict[tuple[str, str], int] = {}

    def publish(object_type: str, object_id: str, row: dict[str, Any], bar_index: int) -> None:
        revision_key = (object_type, object_id)
        revision = revisions.get(revision_key, 0) + 1
        revisions[revision_key] = revision
        {
            "strategy_state": states,
            "stage_signal": stages,
            "trade_signal": signals,
            "chart_event": chart_events,
        }[object_type].append({**row, "object_revision": revision})
        event_payload = {**row, "object_id": object_id, "object_revision": revision}
        causal_events.append(
            {
                "event_seq": len(causal_events) + 1,
                "known_at_bar_index": bar_index,
                "object_type": object_type,
                "object_id": object_id,
                "operation": "upsert",
                "object_revision": revision,
                "payload_json": json.dumps(
                    event_payload, ensure_ascii=False, separators=(",", ":")
                ),
            }
        )

    def transition(
        bar: StrategyBar, next_state: str, reason: str, reference_object_id: str
    ) -> None:
        nonlocal current_state
        previous_state = current_state
        current_state = next_state
        suffix = reference_object_id[-8:]
        state_id = f"strategy_state-CONSREV-{bar.bar_index}-{suffix}"
        common = {
            "known_at_bar_index": bar.bar_index,
            "timestamp_utc": bar.timestamp_utc,
            "price_i64": bar.close_i64,
            "reason_code": reason,
            "reference_object_id": reference_object_id,
        }
        publish(
            "strategy_state",
            state_id,
            {**common, "state_from": previous_state, "state_to": next_state},
            bar.bar_index,
        )
        stage_id = f"CHAN-CONSREV-STAGE-{bar.bar_index}-{suffix}"
        publish(
            "stage_signal",
            stage_id,
            {**common, "stage_signal_id": stage_id, "stage": next_state, "status": "confirmed"},
            bar.bar_index,
        )

    def trade(bar: StrategyBar, action: str, reason: str, source_id: str) -> None:
        nonlocal position_side
        signal_id = f"CHAN-CONSREV-{bar.bar_index}-{action}-{source_id[-8:]}"
        side = "long" if action in {"open_long", "close_short"} else "short"
        common = {
            "known_at_bar_index": bar.bar_index,
            "timestamp_utc": bar.timestamp_utc,
            "price_i64": bar.close_i64,
            "reason_code": reason,
            "reference_object_id": source_id,
        }
        publish(
            "trade_signal",
            signal_id,
            {
                **common,
                "signal_id": signal_id,
                "parent_stage_signal_id": f"CHAN-CONSREV-STAGE-{bar.bar_index}-{source_id[-8:]}",
                "side": side,
                "action": action,
            },
            bar.bar_index,
        )
        event_id = f"EVENT-{signal_id}"
        publish(
            "chart_event",
            event_id,
            {
                **common,
                "event_id": event_id,
                "bar_index": bar.bar_index,
                "event_type": action,
            },
            bar.bar_index,
        )
        position_side = {
            "open_long": "long",
            "open_short": "short",
            "close_long": "flat",
            "close_short": "flat",
        }[action]

    for bar in bars:
        if bar.bar_index % 256 == 0 and cancelled.is_set():
            raise InterruptedError("strategy execution cancelled")
        new_points: list[tuple[Any, dict[str, Any]]] = []
        third_points: list[tuple[Any, dict[str, Any]]] = []
        for event in events_by_bar.get(bar.bar_index, []):
            target = centers if event.object_type == "segment_zhongshu" else divergences
            if event.operation == "delete":
                if event.object_type in {"segment_zhongshu", "divergence"}:
                    target.pop(event.object_id, None)
                continue
            value = json.loads(event.payload_json)
            if event.object_type == "segment_zhongshu":
                centers[event.object_id] = value
            elif event.object_type == "divergence":
                divergences[event.object_id] = value
            elif value.get("signal_type") in {"class_buy_1", "class_sell_1"}:
                new_points.append((event, value))
            elif value.get("signal_type") in {"buy_3", "sell_3"}:
                third_points.append((event, value))

        converted = False
        for event, point in third_points:
            if active_center_id is None or point.get("reference_object_id") != active_center_id:
                continue
            signal_type = str(point["signal_type"])
            if position_side == "long" and signal_type == "sell_3":
                transition(bar, "converted_to_S3", "REVERSION_FAILED_CONFIRMED_S3", event.object_id)
                trade(bar, "close_long", "REVERSION_FAILED_CONFIRMED_S3", event.object_id)
                trade(bar, "open_short", "SWITCH_TO_CONFIRMED_S3", event.object_id)
                converted = True
            elif position_side == "short" and signal_type == "buy_3":
                transition(bar, "converted_to_B3", "REVERSION_FAILED_CONFIRMED_B3", event.object_id)
                trade(bar, "close_short", "REVERSION_FAILED_CONFIRMED_B3", event.object_id)
                trade(bar, "open_long", "SWITCH_TO_CONFIRMED_B3", event.object_id)
                converted = True

        if (
            not converted
            and position_side != "flat"
            and active_center_id in centers
            and opened_at is not None
            and bar.bar_index > opened_at
        ):
            center = centers[active_center_id]
            if int(center["zd_i64"]) <= bar.close_i64 <= int(center["zg_i64"]):
                transition(bar, "returned_to_centre", "PRICE_RETURNED_TO_CENTRE", active_center_id)
                action = "close_long" if position_side == "long" else "close_short"
                trade(bar, action, "CENTRE_REVERSION_TARGET_REACHED", active_center_id)
                active_center_id = None
                opened_at = None

        if position_side != "flat":
            continue
        for event, point in new_points:
            divergence = divergences.get(str(point.get("reference_object_id")))
            if not divergence or divergence.get("divergence_kind") != "consolidation":
                continue
            center_id = str(divergence.get("reference_object_id") or "")
            if center_id not in centers:
                continue
            signal_type = str(point["signal_type"])
            next_state = (
                "reverting_up_to_centre"
                if signal_type == "class_buy_1"
                else "reverting_down_to_centre"
            )
            reason = (
                "CONFIRMED_CONSOLIDATION_BOTTOM_DIVERGENCE"
                if signal_type == "class_buy_1"
                else "CONFIRMED_CONSOLIDATION_TOP_DIVERGENCE"
            )
            transition(bar, next_state, reason, event.object_id)
            trade(
                bar,
                "open_long" if signal_type == "class_buy_1" else "open_short",
                reason,
                event.object_id,
            )
            active_center_id = center_id
            opened_at = bar.bar_index
            break

    indicator_values = [{"bar_index": bar.bar_index, "ma": None} for bar in bars]
    return StrategyRun(bars, indicator_values, states, stages, signals, chart_events, causal_events)


def _run_third_point_migration_hold(
    payload: dict[str, Any],
    guard: PathGuard,
    cancelled: threading.Event,
    *,
    last_bar_index: int | None,
    first_only: bool = False,
) -> StrategyRun:
    dataset = payload["dataset"]
    parameters = payload["parameters"]
    chan = chan_definition()
    chan_payload = {
        "dataset": dataset,
        "algorithm": {
            key: chan[key] for key in ("kind", "algorithm_id", "algorithm_version", "source_hash")
        },
        "parameters": {"checkpoint_interval": int(parameters["checkpoint_interval"])},
    }
    runtime, _, _ = run_chan(
        chan_payload, guard, cancelled, last_bar_index=last_bar_index, write_checkpoints=False
    )
    table = pq.read_table(
        guard.resolve(str(dataset["bars_path"])),
        columns=["bar_index", "timestamp_utc", "open_i64", "high_i64", "low_i64", "close_i64"],
    ).to_pydict()
    bars = [
        StrategyBar(
            int(value),
            int(table["timestamp_utc"][row_position]),
            int(table["open_i64"][row_position]),
            int(table["high_i64"][row_position]),
            int(table["low_i64"][row_position]),
            int(table["close_i64"][row_position]),
        )
        for row_position, value in enumerate(table["bar_index"])
        if last_bar_index is None or int(value) <= last_bar_index
    ]
    events_by_bar: dict[int, list[Any]] = {}
    for event in runtime.emitter.events:
        if event.object_type in {"segment_zhongshu", "trade_point"}:
            events_by_bar.setdefault(event.known_at_bar_index, []).append(event)

    position_side = "flat"
    current_state = "waiting_strict_third_point"
    origin_center_id: str | None = None
    cycle_used = {"buy": False, "sell": False}
    strategy_tag = "ROTATE" if first_only else "MIGHOLD"
    states: list[dict[str, Any]] = []
    stages: list[dict[str, Any]] = []
    signals: list[dict[str, Any]] = []
    chart_events: list[dict[str, Any]] = []
    causal_events: list[dict[str, Any]] = []
    revisions: dict[tuple[str, str], int] = {}

    def publish(object_type: str, object_id: str, row: dict[str, Any], bar_index: int) -> None:
        revision_key = (object_type, object_id)
        revision = revisions.get(revision_key, 0) + 1
        revisions[revision_key] = revision
        {
            "strategy_state": states,
            "stage_signal": stages,
            "trade_signal": signals,
            "chart_event": chart_events,
        }[object_type].append({**row, "object_revision": revision})
        event_payload = {**row, "object_id": object_id, "object_revision": revision}
        causal_events.append(
            {
                "event_seq": len(causal_events) + 1,
                "known_at_bar_index": bar_index,
                "object_type": object_type,
                "object_id": object_id,
                "operation": "upsert",
                "object_revision": revision,
                "payload_json": json.dumps(
                    event_payload, ensure_ascii=False, separators=(",", ":")
                ),
            }
        )

    def transition(
        bar: StrategyBar, next_state: str, reason: str, reference_object_id: str
    ) -> None:
        nonlocal current_state
        previous_state = current_state
        current_state = next_state
        suffix = reference_object_id[-8:]
        common = {
            "known_at_bar_index": bar.bar_index,
            "timestamp_utc": bar.timestamp_utc,
            "price_i64": bar.close_i64,
            "reason_code": reason,
            "reference_object_id": reference_object_id,
        }
        state_id = f"strategy_state-{strategy_tag}-{bar.bar_index}-{suffix}"
        publish(
            "strategy_state",
            state_id,
            {**common, "state_from": previous_state, "state_to": next_state},
            bar.bar_index,
        )
        stage_id = f"CHAN-{strategy_tag}-STAGE-{bar.bar_index}-{suffix}"
        publish(
            "stage_signal",
            stage_id,
            {**common, "stage_signal_id": stage_id, "stage": next_state, "status": "confirmed"},
            bar.bar_index,
        )

    def trade(bar: StrategyBar, action: str, reason: str, source_id: str) -> None:
        nonlocal position_side
        signal_id = f"CHAN-{strategy_tag}-{bar.bar_index}-{action}-{source_id[-8:]}"
        side = "long" if action in {"open_long", "close_short"} else "short"
        common = {
            "known_at_bar_index": bar.bar_index,
            "timestamp_utc": bar.timestamp_utc,
            "price_i64": bar.close_i64,
            "reason_code": reason,
            "reference_object_id": source_id,
        }
        publish(
            "trade_signal",
            signal_id,
            {
                **common,
                "signal_id": signal_id,
                "parent_stage_signal_id": (
                    f"CHAN-{strategy_tag}-STAGE-{bar.bar_index}-{source_id[-8:]}"
                ),
                "side": side,
                "action": action,
            },
            bar.bar_index,
        )
        publish(
            "chart_event",
            f"EVENT-{signal_id}",
            {
                **common,
                "event_id": f"EVENT-{signal_id}",
                "bar_index": bar.bar_index,
                "event_type": action,
            },
            bar.bar_index,
        )
        position_side = {
            "open_long": "long",
            "open_short": "short",
            "close_long": "flat",
            "close_short": "flat",
        }[action]

    for bar in bars:
        if bar.bar_index % 256 == 0 and cancelled.is_set():
            raise InterruptedError("strategy execution cancelled")
        new_centers: list[Any] = []
        points: list[tuple[Any, dict[str, Any]]] = []
        for event in events_by_bar.get(bar.bar_index, []):
            if event.operation != "upsert":
                continue
            value = json.loads(event.payload_json)
            if event.object_type == "segment_zhongshu" and value.get("confirmed"):
                new_centers.append(event)
            elif event.object_type == "trade_point":
                points.append((event, value))

        if position_side != "flat":
            exit_event: Any | None = next(
                (event for event in new_centers if event.object_id != origin_center_id), None
            )
            exit_reason = "NEW_SAME_LEVEL_CENTRE_CONFIRMED" if exit_event else ""
            if exit_event is None:
                expected = "sell_1" if position_side == "long" else "buy_1"
                expected_class = "class_sell_1" if position_side == "long" else "class_buy_1"
                matched = next(
                    (
                        (event, point)
                        for event, point in points
                        if (
                            point.get("signal_type") == expected
                            and point.get("signal_class") == "standard"
                        )
                        or (first_only and point.get("signal_type") == expected_class)
                    ),
                    None,
                )
                if matched is not None:
                    exit_event, matched_point = matched
                    exit_reason = (
                        "CONSOLIDATION_DIVERGENCE_CONFIRMED"
                        if str(matched_point.get("signal_type")).startswith("class_")
                        else "SAME_LEVEL_TREND_DIVERGENCE_CONFIRMED"
                    )
            if exit_event is not None:
                transition(bar, "migration_hold_exited", exit_reason, exit_event.object_id)
                trade(
                    bar,
                    "close_long" if position_side == "long" else "close_short",
                    exit_reason,
                    exit_event.object_id,
                )
                origin_center_id = None

        if position_side != "flat":
            continue
        entry = next(
            (
                (event, point)
                for event, point in points
                if point.get("signal_type") in {"buy_3", "sell_3"}
                and point.get("signal_class") == "standard"
            ),
            None,
        )
        if entry is None:
            continue
        event, point = entry
        signal_type = str(point["signal_type"])
        direction = "buy" if signal_type == "buy_3" else "sell"
        opposite = "sell" if direction == "buy" else "buy"
        cycle_used[opposite] = False
        if first_only and cycle_used[direction]:
            transition(
                bar,
                f"later_centre_{signal_type.upper()}_filtered",
                "LATER_CENTRE_THIRD_POINT_FILTERED",
                event.object_id,
            )
            continue
        cycle_used[direction] = True
        next_state = (
            "holding_upward_migration" if signal_type == "buy_3" else "holding_downward_migration"
        )
        reason = (
            "CONFIRMED_B3_MIGRATION_ENTRY"
            if signal_type == "buy_3"
            else "CONFIRMED_S3_MIGRATION_ENTRY"
        )
        transition(bar, next_state, reason, event.object_id)
        trade(
            bar,
            "open_long" if signal_type == "buy_3" else "open_short",
            reason,
            event.object_id,
        )
        origin_center_id = str(point.get("reference_object_id") or "")

    indicator_values = [{"bar_index": bar.bar_index, "ma": None} for bar in bars]
    return StrategyRun(bars, indicator_values, states, stages, signals, chart_events, causal_events)
