"""Reproducible milestone-17 audit on explicitly supplied normalized files."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import threading
import uuid
from collections import Counter
from pathlib import Path

import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tvbt.backtest import run_backtest
from tvbt.chan.algorithm import definition, run_chan
from tvbt.chan.engine import ChanEngine
from tvbt.chan.storage import ChanResult, write_chan_cache
from tvbt.replay import generate_replay
from tvbt.storage.path_guard import PathGuard
from tvbt.strategy import (
    same_level_decomposition_program_definition,
    trend_divergence_reversal_definition,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", required=True, type=Path)
    parser.add_argument("--bars", required=True)
    parser.add_argument("--meta", required=True)
    args = parser.parse_args()
    guard = PathGuard(args.data_root)
    meta = json.loads(guard.resolve(args.meta).read_text(encoding="utf-8"))
    identity = definition()
    dataset = {
        "dataset_id": meta["dataset_id"],
        "data_revision": meta["data_revision"],
        "bars_path": args.bars,
        "meta_path": args.meta,
    }
    algorithm = {
        key: identity[key] for key in ("kind", "algorithm_id", "algorithm_version", "source_hash")
    }
    key = hashlib.sha256(json.dumps([dataset, algorithm], sort_keys=True).encode()).hexdigest()
    payload = {
        "dataset": dataset,
        "algorithm": algorithm,
        "parameters": {"checkpoint_interval": 1024},
        "calculation_mode": "causal_events",
        "cache_key": "sha256:" + key,
        "output_path": "cache/chan/m17-" + key,
    }
    print("Computing full declared history", flush=True)
    progress_bucket = -1

    def progress(engine: ChanEngine) -> None:
        nonlocal progress_bucket
        current_bucket = engine.raw_bars[-1].bar_index // 4096
        if current_bucket != progress_bucket:
            progress_bucket = current_bucket
            print(f"Observed through K{engine.raw_bars[-1].bar_index}", flush=True)

    runtime, indices, _ = run_chan(
        payload, guard, threading.Event(), write_checkpoints=False, event_sink=progress
    )
    rows = runtime.result_rows()
    events = [event.row() for event in runtime.emitter.events]
    assert all(event["known_at_bar_index"] <= indices[-1] for event in events)
    result = ChanResult(
        bar_count=len(indices),
        first_bar_index=indices[0],
        last_bar_index=indices[-1],
        merged_bar_count=len(runtime.included),
        events=events,
        **rows,
    )
    cache_ref = write_chan_cache(payload, guard, result)
    cutoffs = sorted({min(2876, indices[-1]), min(5000, indices[-1])})
    for cutoff in cutoffs:
        prefix, _, _ = run_chan(
            payload, guard, threading.Event(), last_bar_index=cutoff, write_checkpoints=False
        )
        prefix_events = [event.row() for event in prefix.emitter.events]
        assert prefix_events == [event for event in events if event["known_at_bar_index"] <= cutoff]
        restored = ChanEngine.from_state(json.loads(json.dumps(prefix.export_state())))
        for bar in runtime.raw_bars:
            if cutoff < bar.bar_index <= min(cutoff + 1000, indices[-1]):
                prefix.update(bar)
                restored.update(bar)
        assert prefix.export_state() == restored.export_state()
        assert [event.row() for event in prefix.emitter.events[len(prefix_events) :]] == [
            event.row() for event in restored.emitter.events
        ]
    print("Prefix and checkpoint parity passed", flush=True)
    spec = trend_divergence_reversal_definition()
    run_id = "m17-recursive-" + uuid.uuid4().hex
    shared = {
        (dataset["dataset_id"], dataset["data_revision"], indices[-1]): (runtime, indices, {})
    }
    run_payload = {
        "dataset": dataset,
        "algorithm": {key: spec[key] for key in algorithm},
        "parameters": {"checkpoint_interval": 1024},
        "_shared_chan_cache": shared,
        "run_id": run_id,
        "run_signature": "sha256:" + key,
        "trace_id": run_id,
        "range": {"warmup_from_bar_index": 0, "from_bar_index": 0, "to_bar_index": indices[-1]},
        "execution": {
            "semantic_version": "1.0.0",
            "signal_timing": "bar_close",
            "fill_timing": "next_bar_open",
            "commission": {"mode": "fixed_per_contract", "amount_i64": 0, "money_scale": 100},
            "slippage": {"mode": "ticks", "value": 0},
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
        "capital": {"initial_cash_i64": 100000000, "currency": "CNY", "money_scale": 100},
        "random_seed": 17,
        "output_path": "runs/" + run_id,
    }
    signature_keys = (
        "dataset",
        "algorithm",
        "parameters",
        "range",
        "execution",
        "capital",
        "random_seed",
    )
    run_payload["run_signature"] = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(
                {key: run_payload[key] for key in signature_keys},
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
    )
    run_ref = run_backtest(run_payload, guard, threading.Event())
    replay_ref = generate_replay(
        {**run_payload, "cache_key": "sha256:" + key, "output_path": "cache/replay/" + run_id},
        guard,
        threading.Event(),
    )
    formal = pq.read_table(guard.resolve(run_ref) / "chart_events.parquet").to_pylist()
    replay = pq.read_table(guard.resolve(replay_ref) / "events.parquet").to_pylist()
    formal_signals = [event for event in formal if event["object_type"] == "trade_signal"]
    replay_signals = [event for event in replay if event["object_type"] == "trade_signal"]
    assert [{k: v for k, v in event.items() if k != "event_seq"} for event in formal_signals] == [
        {k: v for k, v in event.items() if k != "event_seq"} for event in replay_signals
    ]
    same_spec = same_level_decomposition_program_definition()
    same_id = run_id + "-same-level"
    same_payload = {
        **run_payload,
        "algorithm": {key: same_spec[key] for key in algorithm},
        "parameters": {
            "checkpoint_interval": 1024,
            "odd_direction_is_down": True,
            "allow_long": True,
            "allow_short": True,
            "operation_quantity": 1,
        },
        "run_id": same_id,
        "trace_id": same_id,
        "output_path": "runs/" + same_id,
    }
    same_payload["run_signature"] = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(
                {key: same_payload[key] for key in signature_keys},
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
    )
    same_ref = run_backtest(same_payload, guard, threading.Event())
    same_replay_ref = generate_replay(
        {**same_payload, "cache_key": "sha256:" + key, "output_path": "cache/replay/" + same_id},
        guard,
        threading.Event(),
    )
    same_events = pq.read_table(guard.resolve(same_ref) / "chart_events.parquet").to_pylist()
    same_replay_events = pq.read_table(
        guard.resolve(same_replay_ref) / "events.parquet"
    ).to_pylist()
    same_signals = [event for event in same_events if event["object_type"] == "trade_signal"]
    # Backtest inserts execution/risk events into its global stream, so the
    # stream sequence number is not a strategy signal identity. All signal
    # facts, ordering, known time, revisions and operations must still agree.
    assert [{k: v for k, v in event.items() if k != "event_seq"} for event in same_signals] == [
        {k: v for k, v in event.items() if k != "event_seq"}
        for event in same_replay_events
        if event["object_type"] == "trade_signal"
    ]
    report = {
        "dataset": dataset,
        "algorithm": algorithm,
        "bars": len(indices),
        "cache_ref": cache_ref,
        "counts": {name: len(value) for name, value in rows.items()},
        "movements_by_level_status": dict(
            Counter(f"L{row['level']}:{row['status']}" for row in rows["recursive_movements"])
        ),
        "centers_by_level": dict(Counter(f"L{row['level']}" for row in rows["recursive_centers"])),
        "prefix_cutoffs": cutoffs,
        "prefix_event_parity": True,
        "checkpoint_parity": True,
        "run_ref": run_ref,
        "replay_ref": replay_ref,
        "trade_signal_parity": True,
        "trade_signals": len(formal_signals),
        "same_level_run_ref": same_ref,
        "same_level_replay_ref": same_replay_ref,
        "same_level_trade_signals": len(same_signals),
        "same_level_signal_parity": True,
    }
    report_path = guard.resolve("acceptance/" + run_id + ".json")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = report_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, report_path)
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
