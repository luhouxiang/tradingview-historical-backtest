"""Create and audit an immutable segment-end simulation on a specified dataset.

Use only standardized Go-provided paths beneath data_root; do not scan history.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import threading
import uuid
from itertools import pairwise
from pathlib import Path

import pyarrow.parquet as pq

from tvbt.backtest import run_backtest
from tvbt.divergence_simulation import run_simulation
from tvbt.storage.path_guard import PathGuard
from tvbt.strategy import divergence_simulation_definition
from tvbt.training import TrainingStore


def run_training(payload: dict, guard: PathGuard) -> str:
    """Audit real incremental commands, including idempotent retry and lookback."""
    store = TrainingStore(guard)
    cancelled = threading.Event()
    operation = 0

    def command(action: str, **fields: object) -> tuple[str, dict]:
        nonlocal operation
        operation += 1
        job_id = f"training-command-{operation}"
        request = {
            **payload,
            "job_id": job_id,
            "output_path": f"runs/training/{payload['run_id']}/commands/{job_id}",
            "training": {
                "session_id": payload["run_id"],
                "operation_id": job_id,
                "action": action,
                **fields,
            },
        }
        reference = store.command(request, cancelled)
        snapshot = json.loads(
            (guard.resolve(reference) / "snapshot.json").read_text(encoding="utf-8")
        )
        assert store.command(request, cancelled) == reference
        assert all(e["known_at_bar_index"] <= snapshot["cursor"] for e in snapshot["events"])
        return reference, snapshot

    _, snapshot = command("create")
    assert snapshot["cursor"] == payload["range"]["from_bar_index"]
    assert snapshot["processed_until_bar_index"] == snapshot["cursor"]
    print(f"training prepared only through K{snapshot['cursor']}", flush=True)
    events = list(snapshot["events"])
    while snapshot["state"] != "completed":
        _, snapshot = command("advance", expected_cursor=snapshot["cursor"], count=100)
        events.extend(snapshot["events"])
    finished = snapshot
    if finished["cursor"] > payload["range"]["from_bar_index"]:
        _, earlier = command(
            "seek",
            expected_cursor=finished["cursor"],
            target_bar_index=payload["range"]["from_bar_index"],
        )
        assert earlier["reset"] and earlier["state"] == "lookback"
        assert earlier["processed_until_bar_index"] == finished["cursor"]
        assert earlier["account"]["closed_trade_count"] == 0
    output = guard.resolve(finished["completed_run_ref"])
    persisted = pq.read_table(output / "chart_events.parquet").to_pylist()
    assert events == [
        {
            **{key: value for key, value in row.items() if key != "payload_json"},
            "payload": json.loads(row["payload_json"]),
        }
        for row in persisted
    ]
    print(
        f"training command audit passed: {operation} commands, {len(events)} causal events",
        flush=True,
    )
    return str(finished["completed_run_ref"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument(
        "--meta", required=True, help="standardized meta path relative to data_root"
    )
    parser.add_argument("--to", type=int)
    parser.add_argument("--verify-prefix", type=int)
    parser.add_argument(
        "--compare-run", help="compare complete facts with another immutable same-signature run"
    )
    parser.add_argument(
        "--training",
        action="store_true",
        help="execute and audit bounded training commands instead of a batch",
    )
    parser.add_argument(
        "--existing-run", help="verify an existing immutable run instead of creating one"
    )
    args = parser.parse_args()
    if args.training and args.existing_run:
        parser.error("--training cannot be combined with --existing-run")
    guard = PathGuard(args.data_root)
    meta_path = guard.resolve(args.meta)
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    bars_path = meta_path.parent / "bars.parquet"
    last = int(meta["coverage"]["last_bar_index"]) if args.to is None else args.to
    definition = divergence_simulation_definition()
    run_id = "audit-16a-" + uuid.uuid4().hex[:16]
    payload = {
        "dataset": {
            "dataset_id": meta["dataset_id"],
            "data_revision": meta["data_revision"],
            "bars_path": bars_path.relative_to(args.data_root.resolve()).as_posix(),
            "meta_path": args.meta,
        },
        "algorithm": {
            key: definition[key]
            for key in ("kind", "algorithm_id", "algorithm_version", "source_hash")
        },
        "parameters": {
            "checkpoint_interval": 1024,
            "enable_trend": True,
            "enable_consolidation": True,
            "enable_oscillation": True,
            "operation_quantity": 1,
            "allow_short": True,
        },
        "range": {"warmup_from_bar_index": 0, "from_bar_index": 0, "to_bar_index": last},
        "execution": {
            "semantic_version": "1.0.0",
            "signal_timing": "bar_close",
            "fill_timing": "next_bar_open",
            "commission": {"mode": "fixed_per_contract", "amount_i64": 300, "money_scale": 100},
            "slippage": {"mode": "ticks", "value": 1},
            "contract_multiplier": 20,
            "contract_multiplier_source": "instrument_config",
            "margin_ratio": 0.12,
            "intrabar_conflict_rule": "worst_case",
            "stress_scenario_id": "baseline",
            "cost_multiplier": 1,
            "additional_slippage_ticks": 0,
            "additional_delay_bars": 0,
            "fill_mode": "unlimited",
        },
        "capital": {"initial_cash_i64": 100000000, "currency": "CNY", "money_scale": 100},
        "random_seed": 16,
        "trace_id": run_id,
        "run_id": run_id,
        "output_path": f"runs/{run_id}",
    }
    payload["run_signature"] = (
        "sha256:" + hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    )
    operation = "verifying" if args.existing_run else "preparing"
    print(f"{operation} {args.existing_run or run_id} through K{last}", flush=True)
    output = guard.resolve(
        f"runs/{args.existing_run}"
        if args.existing_run
        else run_training(payload, guard)
        if args.training
        else run_backtest(payload, guard, threading.Event())
    )
    manifest = json.loads((output / "run.json").read_text(encoding="utf-8"))
    assert manifest["dataset"] == {
        key: payload["dataset"][key] for key in ("dataset_id", "data_revision")
    }
    assert manifest["strategy"]["source_hash"] == definition["source_hash"]
    assert manifest["range"] == payload["range"]
    log = (output / "log.ndjson").read_text(encoding="utf-8").splitlines()[-1]
    recorded = json.loads(log[log.index("{") :])["fact_hashes"]
    for name, checksum in recorded.items():
        assert (
            checksum
            == "sha256:" + hashlib.sha256((output / f"{name}.parquet").read_bytes()).hexdigest()
        )
    events = pq.read_table(output / "chart_events.parquet").to_pylist()
    assert all(a["known_at_bar_index"] <= b["known_at_bar_index"] for a, b in pairwise(events))
    decisions = [
        json.loads(e["payload_json"]) for e in events if e["object_type"] == "stage_signal"
    ]
    signals = pq.read_table(output / "trade_signals.parquet").to_pylist()
    fills = pq.read_table(output / "fills.parquet").to_pylist()
    orders = {o["order_id"]: o for o in pq.read_table(output / "orders.parquet").to_pylist()}
    signal_by_id = {s["signal_id"]: s for s in signals}
    for fill in fills:
        signal = signal_by_id[orders[fill["order_id"]]["signal_id"]]
        assert fill["bar_index"] == signal["known_at_bar_index"] + 1
    assert all(
        d["segment_end_bar_index"] <= d["segment_confirmed_at_bar_index"] == d["known_at_bar_index"]
        for d in decisions
    )
    fill_events = [
        json.loads(e["payload_json"])
        for e in events
        if json.loads(e["payload_json"]).get("event_type") == "simulation_fill"
    ]
    assert len(fill_events) == len(fills)
    if args.verify_prefix is not None:
        prefix = run_simulation(
            payload, guard, threading.Event(), last_bar_index=args.verify_prefix
        )
        full_prefix = [
            e
            for e in events
            if e["object_type"] in {"stage_signal", "trade_signal"}
            and e["known_at_bar_index"] <= args.verify_prefix
        ]
        assert [json.loads(e["payload_json"]) for e in full_prefix] == [
            json.loads(e["payload_json"])
            for e in prefix.events
            if e["object_type"] in {"stage_signal", "trade_signal"}
        ]
    assert (output / "_SUCCESS").is_file()
    if args.compare_run:
        comparison = guard.resolve(f"runs/{args.compare_run}")
        other = json.loads((comparison / "run.json").read_text(encoding="utf-8"))
        assert (comparison / "_SUCCESS").is_file()
        assert manifest["run_signature"] == other["run_signature"]
        for name in recorded:
            assert (
                pq.read_table(output / f"{name}.parquet").to_pylist()
                == pq.read_table(comparison / f"{name}.parquet").to_pylist()
            ), f"training and batch {name} facts differ"
        print(f"all {len(recorded)} persisted fact tables match {args.compare_run}", flush=True)
    print(
        json.dumps(
            {
                "run_id": manifest["run_id"],
                "source_hash": definition["source_hash"],
                "decisions": len(decisions),
                "kinds": {
                    kind: sum(d["divergence_kind"] == kind for d in decisions)
                    for kind in ("trend", "consolidation", "center_oscillation")
                },
                "fills": len(fills),
                "events": len(events),
                "engine": manifest["engine"],
                "K27417": [d for d in decisions if d["segment_end_bar_index"] == 27417],
            },
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
