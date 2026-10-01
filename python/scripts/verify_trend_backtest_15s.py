"""Verify that full-history trend candidates do not become executable standard B1/S1.

This creates one clearly labeled, immutable audit run under the supplied data root.
It never overwrites a completed run; an existing matching run is only rechecked.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import threading
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from tvbt.backtest import run_backtest
from tvbt.storage.path_guard import PathGuard
from tvbt.strategy import trend_divergence_reversal_definition


def _sha256(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--revision-dir", required=True)
    args = parser.parse_args()

    guard = PathGuard(args.data_root.resolve(strict=True))
    dataset_prefix = Path("normalized") / args.dataset_id / args.revision_dir
    meta_ref = (dataset_prefix / "meta.json").as_posix()
    bars_ref = (dataset_prefix / "bars.parquet").as_posix()
    meta = json.loads(guard.resolve(meta_ref).read_text(encoding="utf-8"))
    if meta["dataset_id"] != args.dataset_id or not str(meta["data_revision"])[7:].startswith(
        args.revision_dir
    ):
        raise ValueError("dataset identity differs from normalized metadata")
    bar_count = int(meta["coverage"]["bar_count"])
    if pq.ParquetFile(guard.resolve(bars_ref)).metadata.num_rows != bar_count:
        raise ValueError("normalized bar count differs from metadata")

    published = trend_divergence_reversal_definition()
    algorithm = {
        key: str(published[key])
        for key in ("kind", "algorithm_id", "algorithm_version", "source_hash")
    }
    facts: dict[str, Any] = {
        "dataset": {
            "dataset_id": args.dataset_id,
            "data_revision": meta["data_revision"],
            "bars_path": bars_ref,
            "meta_path": meta_ref,
        },
        "algorithm": algorithm,
        "parameters": {"checkpoint_interval": 1024},
        "range": {
            "warmup_from_bar_index": 0,
            "from_bar_index": 0,
            "to_bar_index": bar_count - 1,
        },
        "execution": {
            "semantic_version": "1.0.0",
            "signal_timing": "bar_close",
            "fill_timing": "next_bar_open",
            "commission": {
                "mode": "fixed_per_contract",
                "amount_i64": 300,
                "money_scale": 100,
            },
            "slippage": {"mode": "ticks", "value": 1},
            "contract_multiplier": 20,
            "contract_multiplier_source": "instrument_config",
            "margin_ratio": 0.12,
            "intrabar_conflict_rule": "worst_case",
            "stress_scenario_id": "baseline",
            "cost_multiplier": 1.0,
            "additional_slippage_ticks": 0.0,
            "additional_delay_bars": 0,
            "fill_mode": "unlimited",
        },
        "capital": {
            "initial_cash_i64": 100_000_000,
            "currency": "CNY",
            "money_scale": 100,
        },
        "random_seed": 15,
    }
    signature = _sha256(facts)
    run_id = f"audit-15s-trend-{signature[7:23]}"
    output_ref = f"runs/{run_id}"
    payload = {
        **facts,
        "run_id": run_id,
        "run_signature": signature,
        "trace_id": run_id,
        "output_path": output_ref,
    }
    output = guard.resolve(output_ref)
    created = not output.exists()
    if created:
        returned = run_backtest(payload, guard, threading.Event())
        if returned != output_ref:
            raise AssertionError("formal run returned a different path")
    if not (output / "_SUCCESS").is_file():
        raise AssertionError("audit run is incomplete")
    manifest = json.loads((output / "run.json").read_text(encoding="utf-8"))
    if (
        manifest["run_signature"] != signature
        or manifest["dataset"]["data_revision"] != meta["data_revision"]
        or manifest["strategy"]["source_hash"] != algorithm["source_hash"]
        or manifest["range"] != facts["range"]
    ):
        raise AssertionError("audit run identity differs")
    counts = {
        name: pq.ParquetFile(output / f"{name}.parquet").metadata.num_rows
        for name in ("trade_signals", "orders", "fills", "trades", "equity")
    }
    if any(counts[name] != 0 for name in ("trade_signals", "orders", "fills", "trades")):
        raise AssertionError("unproved segment trend candidate became an executable trade")
    if counts["equity"] != bar_count:
        raise AssertionError("formal run does not cover the full history")
    print(
        json.dumps(
            {
                "created": created,
                "run_id": run_id,
                "run_signature": signature,
                "data_revision": meta["data_revision"],
                "strategy": algorithm,
                "counts": counts,
                "status": "verified",
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
