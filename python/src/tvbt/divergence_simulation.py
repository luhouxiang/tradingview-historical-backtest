from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Generator, Iterator
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pyarrow.parquet as pq

from tvbt.chan.engine import ChanEngine, ChanParameters, RawBar
from tvbt.chan.local_center import classify_center_relative_direction
from tvbt.chan.signals import ChanSignal, _high, _low, chan_divergences, chan_forming_divergences
from tvbt.storage.memory_guard import check_memory
from tvbt.storage.path_guard import PathGuard

if TYPE_CHECKING:
    from tvbt.strategy import StrategyBar, StrategyRun


def source_hash() -> str:
    return "sha256:" + hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


class SegmentEndDetector:
    """Use authoritative comparisons at the first causal completion of each segment.

    Outward comparisons reuse the forming-context function with a now-completed
    segment. This simulation confirmation does not change standard Chan status.
    """

    def __init__(self) -> None:
        self.seen: set[str] = set()

    def update(self, engine: ChanEngine, known_at: int) -> list[tuple[str, ChanSignal]]:
        lines = engine._segment_lines
        fresh = [(i, line) for i, line in enumerate(lines) if line.object_id not in self.seen]
        if not fresh:
            return []
        centers, ids = engine._segment_structural_centers()
        common: dict[str, Any] = {
            "diff": engine._macd_diff_by_bar,
            "dea": engine._macd_dea_by_bar,
            "raw_positions": engine._raw_position_by_bar_index,
            "certified_complete_positions": True,
            "strength_profile": "price_displacement_speed_v1",
        }
        oscillation = chan_divergences(lines, centers, ids, engine._macd_histogram, **common)
        result: list[tuple[str, ChanSignal]] = []
        for i, line in fresh:
            self.seen.add(line.object_id)
            values = [
                signal
                for signal in oscillation
                if signal.segment_index == i
                and signal.divergence_kind == "center_oscillation"
                and signal.known_at_bar_index <= known_at
            ]
            # Keep only established centers that precede c. The current c must
            # not be a seed or borrow a center formed from its own completion.
            preceding = [
                (center, object_id)
                for center, object_id in zip(centers, ids, strict=True)
                if center.seed_end_index < i
                and center.known_at_bar_index <= known_at
                and (center.exit_index is None or center.exit_index >= i)
            ]
            if preceding and i > 0:
                center, object_id = preceding[-1]
                prefix = lines[:i]
                components = prefix[center.base_index :]
                comparable = [
                    member
                    for member in components
                    if member.object_id != center.comparison_excluded_entry_id
                ]
                if comparable:
                    active = replace(
                        center,
                        end_index=i - 1,
                        exit_index=None,
                        end_bar_index=prefix[-1].end.bar_index,
                        end_time=prefix[-1].end.time,
                        status="extended",
                        leave_direction=None,
                        comparison_dd_i64=min(_low(member) for member in comparable),
                        comparison_gg_i64=max(_high(member) for member in comparable),
                    )
                    prior = [
                        (other, other_id)
                        for other, other_id in zip(centers, ids, strict=True)
                        if other.base_index < center.base_index
                        and other.known_at_bar_index <= known_at
                    ]
                    if prior:
                        previous = prior[-1][0]
                        previous_members = lines[previous.base_index : previous.end_index + 1]
                        active = replace(
                            active,
                            relative_dir=classify_center_relative_direction(
                                previous.zd_i64,
                                previous.zg_i64,
                                previous.comparison_dd_i64
                                if previous.comparison_dd_i64 is not None
                                else min(_low(member) for member in previous_members),
                                previous.comparison_gg_i64
                                if previous.comparison_gg_i64 is not None
                                else max(_high(member) for member in previous_members),
                                active.zd_i64,
                                active.zg_i64,
                                min(_low(member) for member in comparable),
                                max(_high(member) for member in comparable),
                                previous_confirmed=previous.status == "left",
                            ),
                        )
                    values.extend(
                        chan_forming_divergences(
                            prefix,
                            [other for other, _ in prior] + [active],
                            [other_id for _, other_id in prior] + [object_id],
                            replace(line, known_at_bar_index=known_at),
                            engine._macd_histogram,
                            **common,
                        )
                    )
            # At a coincident external/trend context, execute one strongest
            # classification; never place duplicate orders for the same c.
            priority = {"trend": 0, "consolidation": 1, "center_oscillation": 2}
            values.sort(key=lambda value: priority.get(str(value.divergence_kind), 3))
            for signal in values:
                result.append(
                    (
                        line.object_id,
                        replace(
                            signal,
                            known_at_bar_index=known_at,
                            status="confirmed",
                            follow_through_status="pending",
                        ),
                    )
                )
        return result


@dataclass(frozen=True, slots=True)
class SimulationStep:
    bar: StrategyBar
    stage_signals: tuple[dict[str, Any], ...]
    trade_signals: tuple[dict[str, Any], ...]
    chart_events: tuple[dict[str, Any], ...]
    events: tuple[dict[str, Any], ...]


@dataclass(frozen=True, slots=True)
class SimulationPosition:
    side: str
    source_segment_id: str | None


@dataclass(frozen=True, slots=True)
class SimulationBarOpen:
    bar: StrategyBar


def simulation_phases(
    payload: dict[str, Any],
    guard: PathGuard,
    cancelled: threading.Event,
    *,
    last_bar_index: int | None,
) -> Generator[SimulationBarOpen | SimulationStep, SimulationPosition | None]:
    # Local import avoids coupling strategy registration to the runner's types.
    from tvbt.strategy import StrategyBar

    dataset, parameters = payload["dataset"], payload["parameters"]
    check_memory()
    meta = json.loads(guard.resolve(str(dataset["meta_path"])).read_text(encoding="utf-8"))
    engine = ChanEngine(
        ChanParameters(checkpoint_interval=int(parameters["checkpoint_interval"])),
        stream_symbol=str(meta.get("instrument", {}).get("symbol", dataset["dataset_id"])),
        stream_timeframe=str(meta.get("timeframe", "unknown")),
        stream_anchor_id=f"{dataset['data_revision']}:dataset-start",
        price_tick_i64=int(meta["price"]["tick_size_i64"]),
    )
    detector = SegmentEndDetector()
    stages: list[dict[str, Any]] = []
    signals: list[dict[str, Any]] = []
    charts: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    state = "flat"
    source_segment: str | None = None
    from_bar = int(payload.get("range", {}).get("from_bar_index", 0))
    enabled = {
        "trend": bool(parameters.get("enable_trend", True)),
        "consolidation": bool(parameters.get("enable_consolidation", True)),
        "center_oscillation": bool(parameters.get("enable_oscillation", True)),
    }

    def publish(kind: str, object_id: str, row: dict[str, Any]) -> None:
        targets = {"stage_signal": stages, "trade_signal": signals, "chart_event": charts}
        targets[kind].append({**row, "object_revision": 1})
        events.append(
            {
                "event_seq": len(events),
                "known_at_bar_index": row["known_at_bar_index"],
                "object_type": kind,
                "object_id": object_id,
                "operation": "upsert",
                "object_revision": 1,
                "payload_json": json.dumps(
                    {**row, "object_id": object_id, "object_revision": 1},
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            }
        )

    def trade(
        bar: StrategyBar, action: str, source: str, evidence: dict[str, Any], reason: str
    ) -> None:
        signal_id = f"SIM-{bar.bar_index}-{action}-{source}"
        publish(
            "trade_signal",
            signal_id,
            {
                **evidence,
                "signal_id": signal_id,
                "parent_stage_signal_id": source,
                "known_at_bar_index": bar.bar_index,
                "timestamp_utc": bar.timestamp_utc,
                "price_i64": bar.close_i64,
                "action": action,
                "side": "long" if action in {"open_long", "close_short"} else "short",
                "quantity": int(parameters.get("operation_quantity", 1)),
                "reason_code": reason,
                "reference_object_id": source,
            },
        )

    table = pq.read_table(
        guard.resolve(str(dataset["bars_path"])),
        columns=[
            "bar_index",
            "timestamp_utc",
            "open_i64",
            "high_i64",
            "low_i64",
            "close_i64",
        ],
    ).to_pydict()
    consumed: set[str] = set()
    for position, value in enumerate(table["bar_index"]):
        n = int(value)
        if last_bar_index is not None and n > last_bar_index:
            break
        if cancelled.is_set():
            raise InterruptedError("divergence simulation cancelled")
        if position % 64 == 0:
            check_memory()
        stage_start, signal_start, chart_start, event_start = (
            len(stages),
            len(signals),
            len(charts),
            len(events),
        )
        bar = StrategyBar(
            n,
            int(table["timestamp_utc"][position]),
            *[
                int(table[key][position])
                for key in ("open_i64", "high_i64", "low_i64", "close_i64")
            ],
        )
        # The execution owner fills previous-bar intents before this bar's
        # close decision. No current-bar Chan processing happens before receipt.
        actual_position = yield SimulationBarOpen(bar)
        if cancelled.is_set():
            raise InterruptedError("divergence simulation cancelled")
        if actual_position is not None:
            if actual_position.side not in {"flat", "long", "short"}:
                raise ValueError("invalid authoritative simulation position side")
            state, source_segment = actual_position.side, actual_position.source_segment_id
        before = len(engine.emitter.events)
        engine.update(
            RawBar(n, bar.timestamp_utc, bar.open_i64, bar.high_i64, bar.low_i64, bar.close_i64)
        )
        for event in engine.emitter.events[before:]:
            events.append({**event.row(), "event_seq": len(events)})
        decisions = detector.update(engine, n)
        if n < from_bar:
            yield SimulationStep(bar, (), (), (), tuple(events[event_start:]))
            continue
        if (
            source_segment is not None
            and source_segment not in {line.object_id for line in engine._segment_lines}
            and state != "flat"
        ):
            trade(bar, f"close_{state}", source_segment, {}, "SIM_SOURCE_REVISED")
            state, source_segment = "flat", None
        for segment_id, signal in decisions:
            if not enabled.get(str(signal.divergence_kind), False) or segment_id in consumed:
                continue
            consumed.add(segment_id)
            stage_id = f"SIM-STAGE-{segment_id}"
            evidence = {
                **asdict(signal),
                "evidence_profile": "segment_end_simulation_v1",
                "segment_object_id": segment_id,
                "segment_end_bar_index": signal.bar_index,
                "segment_end_price_i64": signal.price_i64,
                "segment_end_timestamp_utc": signal.time,
                "segment_confirmed_at_bar_index": n,
                "standard_internal_proof_required": False,
            }
            publish(
                "stage_signal",
                stage_id,
                {
                    **evidence,
                    "stage_signal_id": stage_id,
                    "known_at_bar_index": n,
                    "timestamp_utc": bar.timestamp_utc,
                    "price_i64": bar.close_i64,
                    "status": "confirmed",
                    "stage": "segment_end_divergence",
                    "reason_code": "SIM_SEGMENT_END_DIVERGENCE",
                },
            )
            target = "long" if signal.signal_type == "bottom_divergence" else "short"
            if state == target:
                continue
            if state != "flat":
                trade(bar, f"close_{state}", stage_id, evidence, "SIM_OPPOSITE_DIVERGENCE")
                state, source_segment = "flat", None
            if target == "short" and parameters.get("allow_short", True) is False:
                continue
            trade(bar, f"open_{target}", stage_id, evidence, "SIM_SEGMENT_END_DIVERGENCE")
            state, source_segment = target, segment_id
        yield SimulationStep(
            bar,
            tuple(stages[stage_start:]),
            tuple(signals[signal_start:]),
            tuple(charts[chart_start:]),
            tuple(events[event_start:]),
        )


def simulation_steps(
    payload: dict[str, Any],
    guard: PathGuard,
    cancelled: threading.Event,
    *,
    last_bar_index: int | None,
) -> Iterator[SimulationStep]:
    """Intent-only callers do not supply execution receipts; no account is faked."""
    for phase in simulation_phases(payload, guard, cancelled, last_bar_index=last_bar_index):
        if isinstance(phase, SimulationStep):
            yield phase


def run_simulation(
    payload: dict[str, Any],
    guard: PathGuard,
    cancelled: threading.Event,
    *,
    last_bar_index: int | None,
) -> StrategyRun:
    """Batch mode consumes the same lazy per-bar strategy used by training."""
    from tvbt.strategy import StrategyRun

    result = StrategyRun([], [], [], [], [], [], [])
    for step in simulation_steps(payload, guard, cancelled, last_bar_index=last_bar_index):
        result.bars.append(step.bar)
        result.stage_signals.extend(step.stage_signals)
        result.trade_signals.extend(step.trade_signals)
        result.chart_events.extend(step.chart_events)
        result.events.extend(step.events)
    return result
