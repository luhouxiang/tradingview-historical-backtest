from __future__ import annotations

import json
import os
import shutil
import threading
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tvbt.backtest import run_backtest
from tvbt.storage.path_guard import PathGuard

Progress = Callable[[float, dict[str, Any]], None]


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )


def run_comparison(
    payload: dict[str, Any],
    guard: PathGuard,
    cancelled: threading.Event,
    progress: Progress | None = None,
) -> str:
    strategies = payload.get("strategies")
    if not isinstance(strategies, list) or not strategies:
        raise ValueError("comparison strategies are required")
    output = guard.resolve(str(payload.get("output_path", "")))
    if output.exists():
        raise ValueError("formal comparison directory already exists")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.parent / f".{output.name}.tmp-{uuid.uuid4().hex}"
    temporary.mkdir()
    shared_chan_cache: dict[tuple[Any, ...], tuple[Any, Any, Any]] = {}
    results: list[dict[str, Any]] = []
    failed_count = 0
    try:
        total = len(strategies)
        for index, item in enumerate(strategies):
            if cancelled.is_set():
                raise InterruptedError("strategy comparison cancelled")
            algorithm = item["strategy"]
            algorithm_id = str(algorithm["algorithm_id"])
            if progress is not None:
                progress(
                    index / total,
                    {
                        "total_count": total,
                        "completed_count": index,
                        "failed_count": failed_count,
                        "current_algorithm_id": algorithm_id,
                    },
                )
            run_id = str(item["run_id"])
            child = {
                "contract_version": payload["contract_version"],
                "request_id": payload["request_id"],
                "trace_id": payload["trace_id"],
                "job_id": run_id,
                "run_id": run_id,
                "run_signature": item["run_signature"],
                "dataset": payload["dataset"],
                "algorithm": algorithm,
                "parameters": item["parameters"],
                "range": payload["range"],
                "execution": payload["execution"],
                "capital": payload["capital"],
                "random_seed": payload["random_seed"],
                "output_path": f"runs/{run_id}",
                "_shared_chan_cache": shared_chan_cache,
            }
            if payload.get("risk_overlay") is not None:
                child["risk_overlay"] = payload["risk_overlay"]
            try:
                result_ref = run_backtest(child, guard, cancelled)
                summary = json.loads(
                    guard.resolve(f"{result_ref}/summary.json").read_text(encoding="utf-8")
                )
                summary.update(request_id=payload["request_id"], run_id=run_id)
                results.append(
                    {
                        "algorithm_id": algorithm_id,
                        "name": item["name"],
                        "strategy_family": item["strategy_family"],
                        "parameters": item["parameters"],
                        "status": "completed",
                        "run_id": run_id,
                        "run_signature": item["run_signature"],
                        "summary": summary,
                    }
                )
            except InterruptedError:
                raise
            except Exception as exc:
                failed_count += 1
                results.append(
                    {
                        "algorithm_id": algorithm_id,
                        "name": item["name"],
                        "strategy_family": item["strategy_family"],
                        "parameters": item["parameters"],
                        "status": "failed",
                        "error": {
                            "code": "STRATEGY_BACKTEST_FAILED",
                            "message": str(exc),
                        },
                    }
                )
        manifest = {
            "schema_version": 1,
            "comparison_id": payload["comparison_id"],
            "trace_id": payload["trace_id"],
            "dataset": {
                "dataset_id": payload["dataset"]["dataset_id"],
                "data_revision": payload["dataset"]["data_revision"],
            },
            "range": payload["range"],
            "execution": payload["execution"],
            "capital": payload["capital"],
            "random_seed": payload["random_seed"],
            "minimum_trade_count": payload["minimum_trade_count"],
            "strategy_count": len(strategies),
            "completed_count": len(strategies) - failed_count,
            "failed_count": failed_count,
            "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }
        if payload.get("risk_overlay") is not None:
            manifest["risk_overlay"] = payload["risk_overlay"]
        _write_json(temporary / "comparison.json", manifest)
        _write_json(temporary / "results.json", results)
        (temporary / "_SUCCESS").write_bytes(b"")
        os.replace(temporary, output)
        if progress is not None:
            progress(
                1.0,
                {
                    "total_count": len(strategies),
                    "completed_count": len(strategies),
                    "failed_count": failed_count,
                    "current_algorithm_id": None,
                },
            )
        return str(payload["output_path"])
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
