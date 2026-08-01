from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import pyarrow.parquet as pq

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
    expected = definition()
    for key in ("kind", "algorithm_id", "algorithm_version", "source_hash"):
        if algorithm.get(key) != expected[key]:
            raise ValueError(f"strategy {key} does not match engine definition")
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
