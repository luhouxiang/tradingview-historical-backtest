from __future__ import annotations

import hashlib
import json
import os
import pickle
import shutil
import threading
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

from tvbt.backtest import run_backtest
from tvbt.chan.algorithm import definition as chan_definition
from tvbt.storage.path_guard import PathGuard

Progress = Callable[[float, dict[str, Any]], None]
AGGREGATOR_VERSION = "2.0.0"


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )


def _write_journal(path: Path, payload: dict[str, Any], results: list[dict[str, Any]]) -> None:
    completed_ids = [item["algorithm_id"] for item in results]
    remaining = [
        item["strategy"]["algorithm_id"]
        for item in payload["strategies"]
        if item["strategy"]["algorithm_id"] not in completed_ids
    ]
    journal = {
        "schema_version": 2,
        "comparison_id": payload["comparison_id"],
        "comparison_signature": _comparison_signature(payload),
        "aggregator_version": AGGREGATOR_VERSION,
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
        "strategies": [
            {"strategy": item["strategy"], "parameters": item["parameters"]}
            for item in payload["strategies"]
        ],
        "strategy_count": len(payload["strategies"]),
        "completed_count": sum(item.get("status") == "completed" for item in results),
        "failed_count": sum(item.get("status") == "failed" for item in results),
        "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "journal_status": "running",
        "completed_algorithm_ids": completed_ids,
        "remaining_algorithm_ids": remaining,
    }
    if payload.get("risk_overlay") is not None:
        journal["risk_overlay"] = payload["risk_overlay"]
    temporary = path.with_name(f".{path.name}.tmp-{uuid.uuid4().hex}")
    _write_json(temporary, journal)
    os.replace(temporary, path)


def _dependency_identity(payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    definition = chan_definition()
    facts = {
        "schema_version": 1,
        "dataset_id": payload["dataset"]["dataset_id"],
        "data_revision": payload["dataset"]["data_revision"],
        "to_bar_index": payload["range"]["to_bar_index"],
        "dependency_kind": "chan_causal_runtime",
        "algorithm": {
            key: definition[key]
            for key in ("kind", "algorithm_id", "algorithm_version", "source_hash")
        },
        "parameters": {"write_checkpoints": False},
    }
    canonical = json.dumps(facts, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest(), facts


def _load_shared_dependency(
    guard: PathGuard, payload: dict[str, Any]
) -> tuple[dict[tuple[Any, ...], tuple[Any, Any, Any]], str]:
    identity, expected = _dependency_identity(payload)
    ref = f"cache/comparison_dependencies/{identity}"
    directory = guard.resolve(ref)
    cache: dict[tuple[Any, ...], tuple[Any, Any, Any]] = {}
    try:
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        data = (directory / "runtime.pkl").read_bytes()
        if not (directory / "_SUCCESS").is_file() or manifest["identity"] != expected:
            return cache, ref
        if manifest["content_hash"] != "sha256:" + hashlib.sha256(data).hexdigest():
            return cache, ref
        loaded = pickle.loads(data)  # trusted, content-addressed file below data_root
        if isinstance(loaded, dict):
            cache.update(loaded)
    except OSError, EOFError, KeyError, ValueError, pickle.PickleError:
        pass
    return cache, ref


def _commit_shared_dependency(
    guard: PathGuard, payload: dict[str, Any], cache: dict[Any, Any], ref: str
) -> dict[str, Any] | None:
    if not cache:
        return None
    output = guard.resolve(ref)
    identity_hash, identity = _dependency_identity(payload)
    if (output / "_SUCCESS").is_file():
        existing = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        if not isinstance(existing, dict):
            raise ValueError("shared dependency manifest is invalid")
        return existing
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.parent / f".{output.name}.tmp-{uuid.uuid4().hex}"
    temporary.mkdir()
    try:
        data = pickle.dumps(cache, protocol=pickle.HIGHEST_PROTOCOL)
        content_hash = "sha256:" + hashlib.sha256(data).hexdigest()
        (temporary / "runtime.pkl").write_bytes(data)
        manifest = {
            "schema_version": 1,
            "dependency_id": identity_hash,
            "dependency_ref": ref,
            "identity": identity,
            "content_hash": content_hash,
        }
        _write_json(temporary / "manifest.json", manifest)
        (temporary / "_SUCCESS").write_bytes(b"")
        try:
            os.replace(temporary, output)
        except FileExistsError:
            shutil.rmtree(temporary, ignore_errors=True)
        return manifest
    finally:
        if temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)


def _tier_results(results: list[dict[str, Any]], minimum: int) -> None:
    eligible: list[dict[str, Any]] = []
    for item in results:
        summary = item.get("summary")
        if item.get("status") != "completed" or not isinstance(summary, dict):
            item["tier"] = "failed"
            item["pareto"] = False
            continue
        trades = int(summary.get("trade_count") or 0)
        total_return = float(summary.get("total_return") or 0.0)
        if trades == 0:
            tier = "no_trades"
        elif total_return < 0:
            tier = "loss_making"
        elif trades < minimum:
            tier = "profitable_low_sample"
        else:
            tier = "profitable_candidate"
            eligible.append(item)
        item["tier"] = tier
        item["pareto"] = False
    for candidate in eligible:
        cs = candidate["summary"]
        dominated = any(
            other is not candidate
            and float(other["summary"].get("total_return") or 0)
            >= float(cs.get("total_return") or 0)
            and float(other["summary"].get("max_drawdown") or 0)
            <= float(cs.get("max_drawdown") or 0)
            and (
                float(other["summary"].get("total_return") or 0)
                > float(cs.get("total_return") or 0)
                or float(other["summary"].get("max_drawdown") or 0)
                < float(cs.get("max_drawdown") or 0)
            )
            for other in eligible
        )
        if not dominated:
            candidate["pareto"] = True
            candidate["tier"] = "pareto_candidate"


def _comparison_signature(payload: dict[str, Any]) -> str:
    supplied = payload.get("comparison_signature")
    if isinstance(supplied, str) and supplied.startswith("sha256:"):
        return supplied
    facts = {
        key: payload.get(key)
        for key in (
            "dataset",
            "strategies",
            "range",
            "execution",
            "capital",
            "risk_overlay",
            "random_seed",
            "minimum_trade_count",
        )
    }
    data = json.dumps(facts, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _trade_attribution(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    table = pq.read_table(path)
    required = {"market_l0", "center_phase", "price_vs_center", "trigger_category", "net_pnl_i64"}
    if not required.issubset(table.column_names):
        return {"attribution_supported": False, "dimensions": [], "realized_pnl_i64": 0}
    rows = table.to_pylist()
    dimensions: list[dict[str, Any]] = []
    for dimension in ("market_l0", "center_phase", "price_vs_center", "trigger_category"):
        groups: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            groups.setdefault(str(row.get(dimension) or "unknown"), []).append(row)
        for value, group in sorted(groups.items()):
            pnls = [int(row["net_pnl_i64"]) for row in group]
            wins = [pnl for pnl in pnls if pnl > 0]
            losses = [pnl for pnl in pnls if pnl < 0]
            gross_profit, gross_loss = sum(wins), abs(sum(losses))
            dimensions.append(
                {
                    "dimension": dimension,
                    "value": value,
                    "trade_count": len(group),
                    "win_rate": sum(pnl > 0 for pnl in pnls) / len(group) if group else None,
                    "realized_net_pnl_i64": sum(pnls),
                    "expectancy_i64": sum(pnls) / len(group) if group else None,
                    "profit_factor": gross_profit / gross_loss if gross_loss else None,
                    "average_holding_bars": sum(
                        int(row["exit_bar_index"]) - int(row["entry_bar_index"]) for row in group
                    )
                    / len(group),
                    "commission_i64": sum(int(row["commission_i64"]) for row in group),
                    "slippage_i64": sum(int(row["slippage_i64"]) for row in group),
                }
            )
    return {
        "attribution_supported": True,
        "dimensions": dimensions,
        "realized_pnl_i64": sum(int(row["net_pnl_i64"]) for row in rows),
    }


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
    shared_chan_cache, dependency_ref = _load_shared_dependency(guard, payload)
    dependency_manifest: dict[str, Any] | None = None
    results: list[dict[str, Any]] = []
    failed_count = 0
    journal_path = output.parent / f"{output.name}.journal.json"
    try:
        _write_journal(journal_path, payload, results)
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
                "_shared_dependency_ref": dependency_ref,
            }
            if payload.get("risk_overlay") is not None:
                child["risk_overlay"] = payload["risk_overlay"]
            try:
                result_ref = run_backtest(child, guard, cancelled)
                if dependency_manifest is None:
                    dependency_manifest = _commit_shared_dependency(
                        guard, payload, shared_chan_cache, dependency_ref
                    )
                summary = json.loads(
                    guard.resolve(f"{result_ref}/summary.json").read_text(encoding="utf-8")
                )
                summary.update(request_id=payload["request_id"], run_id=run_id)
                attribution = _trade_attribution(guard.resolve(f"{result_ref}/trades.parquet"))
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
                        "attribution": attribution,
                    }
                )
                _write_journal(journal_path, payload, results)
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
                _write_journal(journal_path, payload, results)
        _tier_results(results, int(payload["minimum_trade_count"]))
        manifest = {
            "schema_version": 2,
            "comparison_id": payload["comparison_id"],
            "comparison_signature": _comparison_signature(payload),
            "aggregator_version": AGGREGATOR_VERSION,
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
            "strategies": [
                {"strategy": item["strategy"], "parameters": item["parameters"]}
                for item in strategies
            ],
            "strategy_count": len(strategies),
            "completed_count": len(strategies) - failed_count,
            "failed_count": failed_count,
            "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }
        if payload.get("risk_overlay") is not None:
            manifest["risk_overlay"] = payload["risk_overlay"]
        if dependency_manifest is not None:
            manifest["shared_dependency"] = dependency_manifest
        _write_json(temporary / "comparison.json", manifest)
        _write_json(temporary / "results.json", results)
        (temporary / "_SUCCESS").write_bytes(b"")
        os.replace(temporary, output)
        journal_path.unlink(missing_ok=True)
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
