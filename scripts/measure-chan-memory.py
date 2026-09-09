"""Run a declared real dataset with production spill and record sampled peak memory.

Invoke with the repository's pinned Python 3.14, never a PATH interpreter.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python" / "src"))

from tvbt.chan.algorithm import calculate_chan, definition
from tvbt.storage.memory_guard import memory_usage
from tvbt.storage.path_guard import PathGuard

parser = argparse.ArgumentParser()
parser.add_argument("--data-root", required=True)
parser.add_argument("--meta", required=True)
parser.add_argument("--output", required=True)
args = parser.parse_args()
guard = PathGuard(Path(args.data_root))
if guard.resolve(args.output).exists() or guard.resolve(args.output + "-memory.json").exists():
    parser.error("Use a new --output path for each measurement; existing evidence is immutable")
meta = json.loads(guard.resolve(args.meta).read_text(encoding="utf-8"))
algorithm = definition()
payload = {
    "dataset": {
        "dataset_id": meta["dataset_id"],
        "data_revision": meta["data_revision"],
        "meta_path": args.meta,
        "bars_path": next(f["path"] for f in meta["files"] if f["role"] == "bars"),
    },
    "algorithm": {
        key: algorithm[key] for key in ("kind", "algorithm_id", "algorithm_version", "source_hash")
    },
    "parameters": {"checkpoint_interval": 1024},
    "calculation_mode": "causal_events",
    "cache_key": algorithm["source_hash"],
    "output_path": args.output,
}
stopped = threading.Event()
peak = [0]


def sample() -> None:
    while not stopped.wait(0.25):
        peak[0] = max(peak[0], memory_usage()[0])


monitor = threading.Thread(target=sample, daemon=True)
monitor.start()
started = time.perf_counter()
result = {
    "dataset_id": meta["dataset_id"],
    "bar_count": meta["coverage"]["bar_count"],
    "source_hash": algorithm["source_hash"],
}
try:
    result["result_ref"] = calculate_chan(payload, guard, threading.Event())
    result["status"] = "completed"
except Exception as exc:
    result.update(status="failed", error=str(exc))
finally:
    peak[0] = max(peak[0], memory_usage()[0])
    stopped.set()
    monitor.join()
    result.update(
        peak_private_mib=round(peak[0] / 1024**2, 2),
        elapsed_seconds=round(time.perf_counter() - started, 2),
    )
    report = guard.resolve(args.output + "-memory.json")
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False), flush=True)
if result["status"] != "completed":
    sys.exit(1)
