from __future__ import annotations

import json
import threading
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from tvbt.backtest import backtest_steps, run_backtest
from tvbt.chan.engine import Fractal, LineObject
from tvbt.chan.signals import ChanSignal, StructuralCenter, chan_divergences
from tvbt.divergence_simulation import (
    SegmentEndDetector,
    SimulationBarOpen,
    SimulationPosition,
    SimulationStep,
    run_simulation,
    simulation_phases,
    simulation_steps,
)
from tvbt.storage.path_guard import PathGuard
from tvbt.strategy import divergence_simulation_definition
from tvbt.training import TrainingStore


def line(i: int, start: int, end: int) -> LineObject:
    direction = "up" if end > start else "down"
    first = Fractal(f"f{i}", "bottom" if direction == "up" else "top", i, i, i * 60000, start, i, i)
    last = Fractal(
        f"f{i + 1}",
        "top" if direction == "up" else "bottom",
        i + 1,
        i + 1,
        (i + 1) * 60000,
        end,
        i + 1,
        i + 1,
    )
    return LineObject(f"s{i}", first, last, direction, i + 1, i + 1)


def center(
    base: int, end: int, zd: int, zg: int, exit_index: int | None = None
) -> StructuralCenter:
    return StructuralCenter(
        base,
        base + 2,
        end,
        exit_index,
        base,
        end + 1,
        base * 60000,
        (end + 1) * 60000,
        zd,
        zg,
        base + 3,
        "extended" if exit_index is None else "left",
        None if exit_index is None else "up",
    )


def engine_stub(lines: list[LineObject], centers: list[StructuralCenter]) -> Any:
    return SimpleNamespace(
        _segment_lines=lines,
        _segment_structural_centers=lambda: (centers, [f"z{i}" for i in range(len(centers))]),
        _macd_histogram={i: 1.0 for i in range(50)},
        _macd_diff_by_bar={},
        _macd_dea_by_bar={},
        _raw_position_by_bar_index={i: i for i in range(50)},
    )


def test_trend_executes_on_c_confirmation_without_reverse_segment_or_internal_proof() -> None:
    prices = [0, 10, 4, 8, 5, 9, 6, 12, 11, 15, 11, 16]
    lines = [line(i, prices[i], prices[i + 1]) for i in range(len(prices) - 1)]
    first = center(1, 5, 5, 8, 6)
    second = replace(center(7, 9, 11, 12), relative_dir="UP")
    engine = engine_stub(lines[:-1], [first, second])
    engine._forming_segment = lines[-1]
    detector = SegmentEndDetector()
    assert not any(sid == "s10" for sid, _ in detector.update(engine, 10))
    engine._segment_lines = [
        *lines[:-1],
        replace(lines[-1], known_at_bar_index=20, confirmed_at_bar_index=20),
    ]
    result = detector.update(engine, 20)
    trend = next(signal for _, signal in result if signal.divergence_kind == "trend")
    assert trend.bar_index == 11 and trend.known_at_bar_index == 20
    assert trend.status == "confirmed" and trend.follow_through_status == "pending"
    assert trend.c_meets_sublevel is None
    assert trend.strength_relation == "weaker"
    assert detector.update(engine, 21) == []
    # Standard completed comparison deliberately cannot confirm yet.
    assert not [
        s
        for s in chan_divergences(lines, [first, second], ["z0", "z1"], engine._macd_histogram)
        if s.divergence_kind == "trend"
    ]


def test_external_consolidation_and_oscillation_use_authoritative_comparisons() -> None:
    prices = [0, 10, 4, 8, 5, 9, 6, 12]
    lines = [line(i, prices[i], prices[i + 1]) for i in range(len(prices) - 1)]
    detector = SegmentEndDetector()
    result = detector.update(engine_stub(lines, [center(1, 5, 5, 8)]), 7)
    external = next(s for sid, s in result if sid == "s6" and s.divergence_kind == "consolidation")
    assert external.comparison_reference_object_id == "s0"
    assert external.comparison_current_object_id == "s6"
    assert external.known_at_bar_index == 7
    assert any(s.divergence_kind == "center_oscillation" for _, s in result)


def facts(tmp_path: Path) -> dict[str, Any]:
    path = tmp_path / "normalized" / "test"
    path.mkdir(parents=True)
    pq.write_table(
        pa.table(
            {
                "bar_index": list(range(9)),
                "timestamp_utc": [i * 60000 for i in range(9)],
                "trading_day": ["2026-01-05"] * 9,
                "open_i64": [100 + i for i in range(9)],
                "high_i64": [110 + i for i in range(9)],
                "low_i64": [90 + i for i in range(9)],
                "close_i64": [101 + i for i in range(9)],
                "volume": [100] * 9,
            }
        ),
        path / "bars.parquet",
    )
    (path / "meta.json").write_text(
        json.dumps({"price": {"price_scale": 1, "tick_size_i64": 1}}), encoding="utf-8"
    )
    definition = divergence_simulation_definition()
    return {
        "dataset": {
            "dataset_id": "TEST",
            "data_revision": "sha256:" + "1" * 64,
            "bars_path": "normalized/test/bars.parquet",
            "meta_path": "normalized/test/meta.json",
        },
        "algorithm": {
            key: definition[key]
            for key in ("kind", "algorithm_id", "algorithm_version", "source_hash")
        },
        "parameters": {"checkpoint_interval": 1024},
        "range": {"from_bar_index": 0, "warmup_from_bar_index": 0, "to_bar_index": 8},
    }


def scripted_decisions(monkeypatch: pytest.MonkeyPatch) -> None:
    def update(self: SegmentEndDetector, engine: Any, n: int) -> list[tuple[str, ChanSignal]]:
        engine._segment_lines = [line(i, 100, 101) for i in range(9)]
        if n not in {2, 3, 5, 8}:
            return []
        kind = {2: "trend", 3: "trend", 5: "consolidation", 8: "center_oscillation"}[n]
        signal = ChanSignal(
            "bottom_divergence" if n in {2, 3, 8} else "top_divergence",
            kind,
            None,
            None,
            n,
            n - 1,
            (n - 1) * 60000,
            100,
            "z",
            10.0,
            5.0,
            n,
        )
        return [(f"s{n}", signal), (f"s{n}", signal)]

    monkeypatch.setattr(SegmentEndDetector, "update", update)


def test_prefix_dedupe_reversal_and_kind_filter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scripted_decisions(monkeypatch)
    payload = facts(tmp_path)
    full = run_simulation(payload, PathGuard(tmp_path), threading.Event(), last_bar_index=8)
    prefix = run_simulation(payload, PathGuard(tmp_path), threading.Event(), last_bar_index=5)
    assert prefix.trade_signals == [s for s in full.trade_signals if s["known_at_bar_index"] <= 5]
    assert [(s["known_at_bar_index"], s["action"]) for s in full.trade_signals] == [
        (2, "open_long"),
        (5, "close_long"),
        (5, "open_short"),
        (8, "close_short"),
        (8, "open_long"),
    ]
    assert len(full.stage_signals) == 4
    assert full.stage_signals[0]["segment_end_price_i64"] == 100
    assert full.stage_signals[0]["segment_end_timestamp_utc"] == 60000
    payload["parameters"]["allow_short"] = False
    long_only = run_simulation(payload, PathGuard(tmp_path), threading.Event(), last_bar_index=8)
    assert [s["action"] for s in long_only.trade_signals] == [
        "open_long",
        "close_long",
        "open_long",
    ]
    payload["parameters"]["enable_trend"] = False
    filtered = run_simulation(payload, PathGuard(tmp_path), threading.Event(), last_bar_index=8)
    assert [s["divergence_kind"] for s in filtered.stage_signals] == [
        "consolidation",
        "center_oscillation",
    ]


def execution_facts(tmp_path: Path) -> dict[str, Any]:
    payload = facts(tmp_path)
    payload.update(
        {
            "run_id": "sim-test",
            "trace_id": "sim-test-trace",
            "run_signature": "sha256:" + "2" * 64,
            "output_path": "runs/sim-test",
            "random_seed": 16,
            "capital": {"initial_cash_i64": 1000000, "currency": "CNY", "money_scale": 100},
            "execution": {
                "semantic_version": "1.0.0",
                "signal_timing": "bar_close",
                "fill_timing": "next_bar_open",
                "commission": {"mode": "fixed_per_contract", "amount_i64": 100, "money_scale": 100},
                "slippage": {"mode": "ticks", "value": 1},
                "contract_multiplier": 1,
                "contract_multiplier_source": "instrument_config",
                "margin_ratio": 0.1,
                "intrabar_conflict_rule": "worst_case",
                "stress_scenario_id": "baseline",
                "cost_multiplier": 1.0,
                "additional_slippage_ticks": 0.0,
                "additional_delay_bars": 0,
                "fill_mode": "unlimited",
            },
        }
    )
    return payload


def test_actual_next_open_fills_costs_and_no_final_bar_fill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scripted_decisions(monkeypatch)
    payload = execution_facts(tmp_path)
    output = tmp_path / run_backtest(payload, PathGuard(tmp_path), threading.Event())
    fills = pq.read_table(output / "fills.parquet").to_pylist()
    assert [(f["bar_index"], f["action"], f["price_i64"]) for f in fills] == [
        (3, "open_long", 104),
        (6, "close_long", 105),
        (6, "open_short", 105),
    ]
    assert all(f["commission_i64"] == 100 and f["slippage_i64"] == 100 for f in fills)
    events = pq.read_table(output / "chart_events.parquet").to_pylist()
    fill_events = [
        json.loads(e["payload_json"])
        for e in events
        if json.loads(e["payload_json"]).get("event_type") == "simulation_fill"
    ]
    assert [e["known_at_bar_index"] for e in fill_events] == [3, 6, 6]
    assert [e["fill_id"] for e in fill_events] == [f["fill_id"] for f in fills]
    assert [(e["position_after_side"], e["position_after_quantity"]) for e in fill_events] == [
        ("long", 1),
        ("flat", 0),
        ("short", 1),
    ]
    assert fill_events[0]["closed_gross_pnl_i64"] is None
    assert fill_events[0]["closed_net_pnl_i64"] is None
    assert fill_events[1]["closed_gross_pnl_i64"] == 100
    assert fill_events[1]["closed_net_pnl_i64"] == -100
    assert fill_events[2]["closed_net_pnl_i64"] is None
    account = [
        json.loads(e["payload_json"])
        for e in events
        if json.loads(e["payload_json"]).get("event_type") == "simulation_account"
    ]
    assert len(account) == 9
    assert [a["object_revision"] for a in account] == list(range(1, 10))
    assert account[3]["position_quantity"] == 1 and account[3]["unrealized_pnl_i64"] == 0
    assert account[6]["position_side"] == "short" and account[6]["unrealized_pnl_i64"] == -200
    assert account[3]["closed_trade_count"] == 0 and account[3]["win_rate"] is None
    assert account[6]["closed_trade_count"] == 1 and account[6]["realized_net_pnl_i64"] == -100
    assert account[6]["net_pnl_i64"] == -400 and account[6]["total_commission_i64"] == 300
    orders = pq.read_table(output / "orders.parquet").to_pylist()
    assert sum(o["reason_code"] == "NO_NEXT_BAR" for o in orders) == 2
    rejected = [
        json.loads(e["payload_json"])
        for e in events
        if json.loads(e["payload_json"]).get("event_type") == "simulation_order_rejected"
    ]
    assert len(rejected) == 2 and all(e["known_at_bar_index"] == 8 for e in rejected)
    assert (output / "_SUCCESS").is_file()


def test_lazy_strategy_does_not_advance_until_requested_and_retains_prefix_deltas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tvbt.chan.engine import ChanEngine

    scripted_decisions(monkeypatch)
    payload = facts(tmp_path)
    updated: list[int] = []
    original = ChanEngine.update

    def track(self: ChanEngine, bar: Any) -> None:
        updated.append(bar.bar_index)
        original(self, bar)

    monkeypatch.setattr(ChanEngine, "update", track)
    iterator = simulation_steps(payload, PathGuard(tmp_path), threading.Event(), last_bar_index=8)
    assert updated == []
    first = next(iterator)
    assert updated == [0] and first.bar.bar_index == 0
    first_events = first.events
    later = list(iterator)
    assert updated == list(range(9)) and first.events == first_events
    full = run_simulation(payload, PathGuard(tmp_path), threading.Event(), last_bar_index=8)
    assert [
        signal for step in [first, *later] for signal in step.trade_signals
    ] == full.trade_signals
    assert [event for step in [first, *later] for event in step.events] == full.events


def test_open_phase_accepts_actual_position_before_close_decision_and_cancels_immediately(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scripted_decisions(monkeypatch)
    payload = facts(tmp_path)
    cancelled = threading.Event()
    phases = simulation_phases(payload, PathGuard(tmp_path), cancelled, last_bar_index=8)
    decisions: list[SimulationStep] = []
    for n in range(4):
        opening = next(phases)
        assert isinstance(opening, SimulationBarOpen) and opening.bar.bar_index == n
        # The broker rejected the earlier opening intent; actual position is flat.
        closing = phases.send(SimulationPosition("flat", None))
        assert isinstance(closing, SimulationStep)
        decisions.append(closing)
    assert [s["action"] for s in decisions[2].trade_signals] == ["open_long"]
    assert [s["action"] for s in decisions[3].trade_signals] == ["open_long"]
    assert isinstance(next(phases), SimulationBarOpen)
    cancelled.set()
    with pytest.raises(InterruptedError):
        phases.send(SimulationPosition("flat", None))


def test_execution_driver_pauses_at_each_bar_and_matches_committed_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scripted_decisions(monkeypatch)
    from tvbt.chan.engine import ChanEngine

    updated: list[int] = []
    original = ChanEngine.update

    def track(self: ChanEngine, bar: Any) -> None:
        updated.append(bar.bar_index)
        original(self, bar)

    monkeypatch.setattr(ChanEngine, "update", track)
    payload = execution_facts(tmp_path)
    cancelled = threading.Event()
    driver = backtest_steps(payload, PathGuard(tmp_path), cancelled)
    assert updated == []
    assert not (tmp_path / "runs/sim-test").exists()
    frames = [next(driver) for _ in range(4)]
    assert [frame.bar_index for frame in frames] == [0, 1, 2, 3]
    assert updated == [0, 1, 2, 3]
    assert frames[2].fills == () and len(frames[3].fills) == 1
    assert frames[3].fills[0]["action"] == "open_long"
    retained = frames[3].account.copy()
    frames.extend(next(driver) for _ in range(5))
    assert frames[-1].terminal_bar and frames[3].account == retained
    assert sum(order["reason_code"] == "NO_NEXT_BAR" for order in frames[-1].orders) == 2
    assert not (tmp_path / "runs/sim-test/_SUCCESS").exists()
    with pytest.raises(StopIteration) as finished:
        next(driver)
    output = tmp_path / str(finished.value.value)
    persisted = pq.read_table(output / "fills.parquet").to_pylist()
    assert [fill for frame in frames for fill in frame.fills] == persisted
    assert [frame.account for frame in frames] == pq.read_table(
        output / "equity.parquet"
    ).to_pylist()
    assert [event for frame in frames for event in frame.events] == pq.read_table(
        output / "chart_events.parquet"
    ).to_pylist()
    assert (output / "_SUCCESS").is_file()


def test_training_driver_warms_from_dataset_start_but_starts_trading_flat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scripted_decisions(monkeypatch)
    payload = execution_facts(tmp_path)
    payload["range"]["from_bar_index"] = 5
    driver = backtest_steps(payload, PathGuard(tmp_path), threading.Event())
    first = next(driver)
    assert first.bar_index == 5 and first.fills == ()
    second = next(driver)
    assert [fill["action"] for fill in second.fills] == ["open_short"]
    # Never finalize a cancelled/abandoned prefix as a completed formal run.
    driver.close()
    assert not (tmp_path / "runs/sim-test/_SUCCESS").exists()


def test_rejected_open_does_not_create_phantom_position_in_next_strategy_decision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scripted_decisions(monkeypatch)
    payload = execution_facts(tmp_path)
    payload["capital"]["initial_cash_i64"] = 50
    output = tmp_path / run_backtest(payload, PathGuard(tmp_path), threading.Event())
    orders = pq.read_table(output / "orders.parquet").to_pylist()
    assert [(order["created_at_bar_index"], order["action"]) for order in orders] == [
        (2, "open_long"),
        (3, "open_long"),
        (5, "open_short"),
        (8, "open_long"),
    ]
    assert all(order["status"] == "rejected" for order in orders)
    assert pq.read_table(output / "fills.parquet").num_rows == 0


def training_command(
    payload: dict[str, Any], operation: str, action: str, **control: Any
) -> dict[str, Any]:
    return {
        **payload,
        "job_id": operation,
        "output_path": f"runs/training/{payload['run_id']}/commands/{operation}",
        "training": {
            "session_id": payload["run_id"],
            "operation_id": operation,
            "action": action,
            **control,
        },
    }


def training_snapshot(tmp_path: Path, reference: str) -> dict[str, Any]:
    assert (tmp_path / reference / "_SUCCESS").is_file()
    return json.loads((tmp_path / reference / "snapshot.json").read_text(encoding="utf-8"))


def test_training_session_only_advances_on_commands_and_matches_formal_fills(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scripted_decisions(monkeypatch)
    payload = execution_facts(tmp_path)
    store = TrainingStore(PathGuard(tmp_path))
    cancel = threading.Event()
    first = training_snapshot(
        tmp_path, store.command(training_command(payload, "create", "create"), cancel)
    )
    assert first["cursor"] == first["processed_until_bar_index"] == 0
    assert first["state"] == "live" and first["reset"]
    assert all(event["known_at_bar_index"] <= 0 for event in first["events"])
    assert not (tmp_path / "runs/sim-test/_SUCCESS").exists()
    command = training_command(payload, "advance", "advance", expected_cursor=0, count=3)
    reference = store.command(command, cancel)
    snapshot = training_snapshot(tmp_path, reference)
    assert snapshot["cursor"] == 3 and snapshot["account"]["position_side"] == "long"
    assert not snapshot["reset"] and snapshot["account"]["total_commission_i64"] == 100
    # Same logical operation even with a new transport job returns the old ref.
    retry = {**command, "job_id": "retry", "output_path": "runs/training/sim-test/commands/retry"}
    assert store.command(retry, cancel) == reference
    assert all(record[3] is None for record in store._sessions["sim-test"].commands.values())
    final = training_snapshot(
        tmp_path,
        store.command(
            training_command(payload, "finish", "advance", expected_cursor=3, count=100), cancel
        ),
    )
    assert final["state"] == "completed" and final["cursor"] == 8
    assert (tmp_path / final["completed_run_ref"] / "_SUCCESS").is_file()
    all_events = first["events"] + snapshot["events"] + final["events"]
    persisted = pq.read_table(
        tmp_path / final["completed_run_ref"] / "chart_events.parquet"
    ).to_pylist()
    assert all_events == [
        {
            **{key: value for key, value in row.items() if key != "payload_json"},
            "payload": json.loads(row["payload_json"]),
        }
        for row in persisted
    ]
    assert final["account"]["total_commission_i64"] == 300
    latest = json.loads((tmp_path / "runs/training/sim-test/latest.json").read_text())
    store.command(command, cancel)
    assert json.loads((tmp_path / "runs/training/sim-test/latest.json").read_text()) == latest


def test_training_lookback_does_not_reexecute_orders_or_expose_future(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scripted_decisions(monkeypatch)
    payload = execution_facts(tmp_path)
    store = TrainingStore(PathGuard(tmp_path))
    cancel = threading.Event()
    store.command(training_command(payload, "create", "create"), cancel)
    store.command(
        training_command(payload, "advance", "advance", expected_cursor=0, count=6), cancel
    )
    previous = training_snapshot(
        tmp_path,
        store.command(
            training_command(payload, "back", "seek", expected_cursor=6, target_bar_index=2), cancel
        ),
    )
    assert previous["reset"] and previous["state"] == "lookback"
    assert previous["processed_until_bar_index"] == 6
    assert previous["account"]["position_side"] == "flat"
    assert all(e["known_at_bar_index"] <= 2 for e in previous["events"])
    resumed = training_snapshot(
        tmp_path,
        store.command(
            training_command(payload, "view", "advance", expected_cursor=2, count=4), cancel
        ),
    )
    assert resumed["state"] == "live" and resumed["cursor"] == 6
    assert resumed["account"]["total_commission_i64"] == 300
    assert sum(e["payload"].get("event_type") == "simulation_fill" for e in resumed["events"]) == 3
    with pytest.raises(ValueError, match="processed training prefix"):
        store.command(
            training_command(payload, "future", "seek", expected_cursor=6, target_bar_index=7),
            cancel,
        )


def test_training_control_rejects_stale_cursor_changed_facts_and_duplicate_intent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scripted_decisions(monkeypatch)
    payload = execution_facts(tmp_path)
    store = TrainingStore(PathGuard(tmp_path))
    cancel = threading.Event()
    store.command(training_command(payload, "create", "create"), cancel)
    command = training_command(payload, "advance", "advance", expected_cursor=0, count=3)
    store.command(command, cancel)
    with pytest.raises(ValueError, match="reused"):
        store.command({**command, "training": {**command["training"], "count": 4}}, cancel)
    with pytest.raises(ValueError, match="cursor conflict"):
        store.command(
            training_command(payload, "stale", "advance", expected_cursor=0, count=1), cancel
        )
    changed = {**payload, "capital": {**payload["capital"], "initial_cash_i64": 2000000}}
    with pytest.raises(ValueError, match="facts changed"):
        store.command(
            training_command(changed, "changed", "advance", expected_cursor=3, count=1), cancel
        )
    for count in [0, 101, True]:
        with pytest.raises(ValueError, match="invalid count"):
            store.command(
                training_command(
                    payload, f"bad-{count}", "advance", expected_cursor=3, count=count
                ),
                cancel,
            )


def test_training_cancel_preserves_known_facts_without_completed_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scripted_decisions(monkeypatch)
    payload = execution_facts(tmp_path)
    store = TrainingStore(PathGuard(tmp_path))
    cancel = threading.Event()
    store.command(training_command(payload, "create", "create"), cancel)
    store.command(
        training_command(payload, "advance", "advance", expected_cursor=0, count=3), cancel
    )
    stopped = training_snapshot(
        tmp_path,
        store.command(training_command(payload, "stop", "cancel", expected_cursor=3), cancel),
    )
    assert stopped["state"] == "cancelled" and stopped["reset"]
    assert stopped["account"]["position_quantity"] == 1
    assert "completed_run_ref" not in stopped
    assert not (tmp_path / "runs/sim-test/_SUCCESS").exists()
    with pytest.raises(ValueError, match="not executable"):
        store.command(
            training_command(payload, "late", "advance", expected_cursor=3, count=1), cancel
        )
    # A restarted Python process may read persisted prefixes, not re-create its driver.
    with pytest.raises(ValueError, match="persisted facts"):
        TrainingStore(PathGuard(tmp_path)).command(
            training_command(payload, "restart", "create"), cancel
        )
    with pytest.raises(ValueError, match="unavailable"):
        TrainingStore(PathGuard(tmp_path)).command(
            training_command(payload, "lost", "advance", expected_cursor=3, count=1), cancel
        )


def test_training_publication_retry_does_not_duplicate_fills(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tvbt.training as training

    scripted_decisions(monkeypatch)
    payload = execution_facts(tmp_path)
    store = TrainingStore(PathGuard(tmp_path))
    cancel = threading.Event()
    store.command(training_command(payload, "create", "create"), cancel)
    original = training._atomic_json
    failed = False

    def fail_once(path: Path, value: Any) -> None:
        nonlocal failed
        if path.name == "latest.json" and not failed:
            failed = True
            raise OSError("simulated publication failure")
        original(path, value)

    monkeypatch.setattr(training, "_atomic_json", fail_once)
    command = training_command(payload, "advance", "advance", expected_cursor=0, count=3)
    with pytest.raises(OSError):
        store.command(command, cancel)
    snapshot = training_snapshot(tmp_path, store.command(command, cancel))
    assert snapshot["cursor"] == 3 and snapshot["account"]["total_commission_i64"] == 100
    assert json.loads((tmp_path / "runs/training/sim-test/latest.json").read_text())[
        "snapshot_ref"
    ].endswith("/advance")


def test_training_session_rejects_output_escape_and_non_next_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scripted_decisions(monkeypatch)
    payload = execution_facts(tmp_path)
    store = TrainingStore(PathGuard(tmp_path))
    command = training_command(payload, "create", "create")
    with pytest.raises(ValueError, match="designated command directory"):
        store.command({**command, "output_path": "runs/../../escape"}, threading.Event())
    with pytest.raises(ValueError, match="next_bar_open"):
        store.command(
            {**command, "execution": {**payload["execution"], "fill_timing": "bar_close"}},
            threading.Event(),
        )


def test_cancel_advance_keeps_partial_prefix_resumable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tvbt.chan.engine import ChanEngine

    scripted_decisions(monkeypatch)
    payload = execution_facts(tmp_path)
    store = TrainingStore(PathGuard(tmp_path))
    cancel = threading.Event()
    store.command(training_command(payload, "create", "create"), cancel)
    original = ChanEngine.update

    def cancel_after_fill(self: ChanEngine, bar: Any) -> None:
        original(self, bar)
        if bar.bar_index == 3:
            cancel.set()

    monkeypatch.setattr(ChanEngine, "update", cancel_after_fill)
    partial = training_snapshot(
        tmp_path,
        store.command(
            training_command(payload, "advance", "advance", expected_cursor=0, count=8), cancel
        ),
    )
    assert partial["cursor"] == 3 and partial["state"] == "live"
    assert partial["account"]["total_commission_i64"] == 100
    resumed = training_snapshot(
        tmp_path,
        store.command(
            training_command(payload, "continue", "advance", expected_cursor=3, count=1),
            threading.Event(),
        ),
    )
    assert resumed["cursor"] == 4 and resumed["account"]["total_commission_i64"] == 100


def test_internal_training_jobs_release_compute_slot_between_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import time
    from http.client import HTTPConnection

    from tvbt.api.server import InternalServer
    from tvbt.testing.logging_proxy import structured_logger

    scripted_decisions(monkeypatch)
    payload = execution_facts(tmp_path)
    payload["contract_version"] = "1.0.0"
    server = InternalServer(("127.0.0.1", 0), structured_logger, PathGuard(tmp_path))
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    connection = HTTPConnection("127.0.0.1", server.server_port, timeout=3)

    def submit(kind: str, value: dict[str, Any]) -> dict[str, Any]:
        connection.request(
            "POST",
            f"/internal/v1/job-submissions/{kind}",
            body=json.dumps(value),
            headers={"Content-Type": "application/json"},
        )
        accepted = connection.getresponse()
        result = json.loads(accepted.read())
        assert accepted.status == 202
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            connection.request("GET", f"/internal/v1/jobs/{result['job_id']}")
            response = connection.getresponse()
            status = json.loads(response.read())
            if status["status"] in {"completed", "failed", "cancelled"}:
                return status
            time.sleep(0.005)
        raise AssertionError("internal job did not release/complete within deadline")

    try:
        created = submit("training", training_command(payload, "create", "create"))
        assert created["status"] == "completed"
        assert training_snapshot(tmp_path, created["result_ref"])["cursor"] == 0
        # This ordinary task must acquire the slot while training is paused.
        other = submit(
            "calculation",
            {"contract_version": "1.0.0", "job_id": "ordinary", "output_path": "cache/ordinary"},
        )
        assert other["status"] == "failed"
        advanced = submit(
            "training", training_command(payload, "advance", "advance", expected_cursor=0, count=3)
        )
        assert advanced["status"] == "completed"
        assert training_snapshot(tmp_path, advanced["result_ref"])["cursor"] == 3
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_training_memory_failure_commits_actual_prefix_without_completed_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tvbt.chan.engine import ChanEngine

    scripted_decisions(monkeypatch)
    payload = execution_facts(tmp_path)
    store = TrainingStore(PathGuard(tmp_path))
    cancel = threading.Event()
    store.command(training_command(payload, "create", "create"), cancel)
    original = ChanEngine.update

    def memory_limit(self: ChanEngine, bar: Any) -> None:
        if bar.bar_index == 4:
            raise MemoryError("test resource guard")
        original(self, bar)

    monkeypatch.setattr(ChanEngine, "update", memory_limit)
    with pytest.raises(MemoryError):
        store.command(
            training_command(payload, "advance", "advance", expected_cursor=0, count=8), cancel
        )
    reference = json.loads((tmp_path / "runs/training/sim-test/latest.json").read_text())[
        "snapshot_ref"
    ]
    prefix = training_snapshot(tmp_path, reference)
    assert prefix["state"] == "interrupted" and prefix["cursor"] == 3
    assert prefix["account"]["total_commission_i64"] == 100
    assert sum(e["payload"].get("event_type") == "simulation_fill" for e in prefix["events"]) == 1
    assert not (tmp_path / "runs/sim-test/_SUCCESS").exists()
