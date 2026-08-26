from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import statistics
import threading
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pyarrow as pa
import pyarrow.parquet as pq

from tvbt import CONTRACT_VERSION, ENGINE_VERSION
from tvbt.backtest import run_backtest
from tvbt.optimization import SUPPORTED_METRICS, expand_search_space, select_candidates
from tvbt.statistical_validation import (
    attempted_parameter_combinations,
    run_statistical_validation,
)
from tvbt.storage.path_guard import PathGuard
from tvbt.stress_test import run_stress_suite
from tvbt.walk_forward import run_dataset_walk_forward

Progress = Callable[[float, dict[str, Any]], None]
AGGREGATOR_VERSION = "4.0.0"


def _write_json_atomic(path: Path, value: Any) -> None:
    temporary = path.with_name(f".{path.name}.tmp-{uuid.uuid4().hex}")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    os.replace(temporary, path)


def _signature(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _validate(payload: dict[str, Any]) -> None:
    datasets = payload.get("datasets")
    if not isinstance(datasets, list) or not 2 <= len(datasets) <= 32:
        raise ValueError("research study requires 2 to 32 datasets")
    if not isinstance(payload.get("strategy"), dict):
        raise ValueError("research strategy is required")
    if not isinstance(payload.get("parameters"), dict):
        raise ValueError("research parameters are required")
    seen: set[tuple[str, str]] = set()
    timeframe: str | None = None
    for item in datasets:
        if not isinstance(item, dict):
            raise ValueError("research dataset entry is invalid")
        identity = (str(item.get("dataset_id", "")), str(item.get("data_revision", "")))
        if not all(identity) or identity in seen:
            raise ValueError("research datasets must be unique and versioned")
        seen.add(identity)
        if not isinstance(item.get("range"), dict):
            raise ValueError("research dataset range is required")
        item_timeframe = str(item.get("timeframe", ""))
        if not item_timeframe or (timeframe is not None and item_timeframe != timeframe):
            raise ValueError("research datasets must use one timeframe")
        timeframe = item_timeframe
    walk = payload.get("walk_forward")
    stress = payload.get("stress_test")
    statistical = payload.get("statistical_validation")
    if stress is not None:
        if not isinstance(stress, dict) or walk is None:
            raise ValueError("stress_test requires walk_forward")
        if stress.get("suite_version") != "1.0.0":
            raise ValueError("stress_test suite_version is unsupported")
        participation = stress.get("volume_participation_rate")
        if (
            not isinstance(participation, (int, float))
            or isinstance(participation, bool)
            or float(participation) != 0.1
        ):
            raise ValueError("stress_test volume_participation_rate is invalid")
    if statistical is not None:
        if not isinstance(statistical, dict) or walk is None:
            raise ValueError("statistical_validation requires walk_forward")
        if statistical.get("method_version") != "1.0.0":
            raise ValueError("statistical_validation method_version is unsupported")
        if int(statistical.get("block_size_trading_days", 0)) < 1:
            raise ValueError("statistical_validation block size is invalid")
        if not 100 <= int(statistical.get("iterations", 0)) <= 10_000:
            raise ValueError("statistical_validation iterations are invalid")
        if float(statistical.get("confidence_level", 0)) != 0.95:
            raise ValueError("statistical_validation confidence level is invalid")
        if float(statistical.get("holm_alpha", 0)) != 0.05:
            raise ValueError("statistical_validation Holm alpha is invalid")
    if walk is not None:
        if not isinstance(walk, dict):
            raise ValueError("walk_forward must be an object")
        for name in ("search_space", "objectives", "constraints"):
            if not isinstance(walk.get(name), list):
                raise ValueError(f"walk_forward {name} must be an array")
        if not walk["objectives"]:
            raise ValueError("walk_forward objectives are required")
        train_days = int(walk.get("train_trading_days", 0))
        validation_days = int(walk.get("validation_trading_days", 0))
        step_days = int(walk.get("step_trading_days", 0))
        if train_days < 2 or validation_days < 1 or step_days < validation_days:
            raise ValueError("walk_forward windows are invalid or overlap")
        search = walk.get("search")
        if not isinstance(search, dict):
            raise ValueError("walk_forward search is required")
        for objective in walk["objectives"]:
            if (
                not isinstance(objective, dict)
                or objective.get("metric") not in SUPPORTED_METRICS
                or objective.get("direction") not in {"maximize", "minimize"}
            ):
                raise ValueError("walk_forward objective is invalid")
        for constraint in walk["constraints"]:
            if (
                not isinstance(constraint, dict)
                or constraint.get("metric") not in SUPPORTED_METRICS
                or constraint.get("operator") not in {">=", "<="}
                or not isinstance(constraint.get("value"), (int, float))
            ):
                raise ValueError("walk_forward constraint is invalid")
        combinations = expand_search_space(walk["search_space"]) if walk["search_space"] else [{}]
        select_candidates(
            combinations,
            str(search.get("method", "")),
            int(search.get("budget", 0)),
            int(search.get("random_seed", 0)),
        )


def _load_journal(path: Path, signature: str) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError, OSError:
        return []
    if value.get("study_signature") != signature or not isinstance(value.get("results"), list):
        raise ValueError("research journal does not match the submitted study")
    return list(value["results"])


def _write_journal(
    path: Path, payload: dict[str, Any], signature: str, results: list[dict[str, Any]]
) -> None:
    completed = {str(item.get("dataset_id")) for item in results}
    _write_json_atomic(
        path,
        {
            "schema_version": 1,
            "research_study_id": payload["research_study_id"],
            "study_signature": signature,
            "journal_status": "running",
            "payload": payload,
            "results": results,
            "completed_dataset_ids": sorted(completed),
            "remaining_dataset_ids": [
                item["dataset_id"]
                for item in payload["datasets"]
                if item["dataset_id"] not in completed
            ],
            "updated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        },
    )


def _daily_rows(guard: PathGuard, run_id: str) -> list[dict[str, Any]]:
    path = guard.resolve(f"runs/{run_id}/daily_returns.parquet")
    return cast(list[dict[str, Any]], pq.read_table(path).to_pylist())


def _portfolio_metrics(daily_returns: list[float]) -> dict[str, Any]:
    equity = 1.0
    peak = 1.0
    max_drawdown = 0.0
    for value in daily_returns:
        equity *= 1 + value
        peak = max(peak, equity)
        if peak > 0:
            max_drawdown = max(max_drawdown, (peak - equity) / peak)
    count = len(daily_returns)
    mean = statistics.fmean(daily_returns) if daily_returns else 0.0
    deviation = statistics.stdev(daily_returns) if count > 1 else 0.0
    return {
        "daily_return_count": count,
        "total_return": equity - 1,
        "annualized_return": equity ** (252 / count) - 1 if count > 1 and equity > 0 else None,
        "sharpe": mean / deviation * math.sqrt(252) if deviation > 0 else None,
        "annualized_volatility": deviation * math.sqrt(252) if count > 1 else None,
        "max_drawdown": max_drawdown,
    }


def _aggregate(results: list[dict[str, Any]]) -> dict[str, Any]:
    completed = [item for item in results if item.get("status") == "completed"]
    group_days: dict[str, dict[str, list[float]]] = {}
    for item in completed:
        group = str(item["independence_group"])
        for row in item.get("daily_returns", []):
            group_days.setdefault(group, {}).setdefault(str(row["trading_day"]), []).append(
                float(row["daily_return"])
            )
    portfolio_days: dict[str, list[float]] = {}
    for days in group_days.values():
        for trading_day, values in days.items():
            portfolio_days.setdefault(trading_day, []).append(statistics.fmean(values))
    portfolio_returns = [statistics.fmean(portfolio_days[day]) for day in sorted(portfolio_days)]
    eligible_groups = {group for group, days in group_days.items() if len(days) >= 504}
    returns = [float(item["summary"].get("total_return") or 0.0) for item in completed]
    worst = min(
        completed,
        key=lambda item: float(item["summary"].get("total_return") or 0.0),
        default=None,
    )
    metrics = _portfolio_metrics(portfolio_returns)
    metrics.update(
        {
            "completed_dataset_count": len(completed),
            "failed_dataset_count": len(results) - len(completed),
            "independence_group_count": len(group_days),
            "eligible_independence_group_count": len(eligible_groups),
            "data_status": "certification_ready" if len(eligible_groups) >= 3 else "exploratory",
            "median_dataset_return": statistics.median(returns) if returns else None,
            "worst_dataset_id": None if worst is None else worst["dataset_id"],
            "worst_dataset_return": None
            if worst is None
            else float(worst["summary"].get("total_return") or 0.0),
            "profitable_dataset_ratio": None
            if not completed
            else sum(value > 0 for value in returns) / len(completed),
            "total_trade_count": sum(
                int(item["summary"].get("trade_count") or 0) for item in completed
            ),
            "worst_dataset_max_drawdown": max(
                (float(item["summary"].get("max_drawdown") or 0.0) for item in completed),
                default=0.0,
            ),
        }
    )
    return metrics


def _aggregate_walk_forward(results: list[dict[str, Any]]) -> dict[str, Any]:
    completed = [item for item in results if item.get("status") == "completed"]
    group_days: dict[str, dict[str, list[float]]] = {}
    group_study_days: dict[str, set[str]] = {}
    folds: list[dict[str, Any]] = []
    group_profitable_fold_ratios: dict[str, list[float]] = {}
    group_parameter_stabilities: dict[str, list[float]] = {}
    group_trade_counts: dict[str, list[int]] = {}
    group_net_pnl: dict[str, list[float]] = {}
    group_fold_counts: dict[str, list[int]] = {}
    for item in completed:
        group = str(item["independence_group"])
        group_study_days.setdefault(group, set()).update(
            str(day) for day in item.get("study_trading_days", [])
        )
        for row in item.get("oos_daily_returns", []):
            group_days.setdefault(group, {}).setdefault(str(row["trading_day"]), []).append(
                float(row["daily_return"])
            )
        completed_folds = [
            fold for fold in item.get("folds", []) if fold.get("status") == "completed"
        ]
        folds.extend(item.get("folds", []))
        dataset_trade_count = sum(
            int(fold["validation_metrics"].get("trade_count") or 0) for fold in completed_folds
        )
        dataset_net_pnl = sum(
            float(fold["validation_metrics"].get("expectancy_i64") or 0)
            * int(fold["validation_metrics"].get("trade_count") or 0)
            for fold in completed_folds
        )
        group_trade_counts.setdefault(group, []).append(dataset_trade_count)
        group_net_pnl.setdefault(group, []).append(dataset_net_pnl)
        group_fold_counts.setdefault(group, []).append(len(completed_folds))
        if completed_folds:
            group_profitable_fold_ratios.setdefault(group, []).append(
                sum(
                    float(fold["validation_metrics"].get("total_return") or 0) > 0
                    for fold in completed_folds
                )
                / len(completed_folds)
            )
        stability = item.get("walk_forward_summary", {}).get("parameter_stability")
        if stability is not None:
            group_parameter_stabilities.setdefault(group, []).append(float(stability))
    daily_rows = _group_weighted_daily_rows(group_days)
    portfolio_returns = [float(row["daily_return"]) for row in daily_rows]
    eligible_groups = {group for group, days in group_study_days.items() if len(days) >= 504}
    completed_folds = [fold for fold in folds if fold.get("status") == "completed"]
    dataset_returns = [
        float(item["walk_forward_summary"].get("total_return") or 0) for item in completed
    ]
    worst = min(
        completed,
        key=lambda item: float(item["walk_forward_summary"].get("total_return") or 0),
        default=None,
    )
    metrics = _portfolio_metrics(portfolio_returns)
    trade_count = sum(
        int(fold["validation_metrics"].get("trade_count") or 0) for fold in completed_folds
    )
    certification_trade_count = sum(
        statistics.fmean(values) for values in group_trade_counts.values()
    )
    certification_net_pnl = sum(statistics.fmean(values) for values in group_net_pnl.values())
    metrics.update(
        {
            "completed_dataset_count": len(completed),
            "failed_dataset_count": len(results) - len(completed),
            "independence_group_count": len(group_study_days),
            "eligible_independence_group_count": len(eligible_groups),
            "data_status": "certification_ready" if len(eligible_groups) >= 3 else "exploratory",
            "median_dataset_return": (
                statistics.median(dataset_returns) if dataset_returns else None
            ),
            "worst_dataset_id": None if worst is None else worst["dataset_id"],
            "worst_dataset_return": (
                None
                if worst is None
                else float(worst["walk_forward_summary"].get("total_return") or 0)
            ),
            "profitable_dataset_ratio": (
                None
                if not completed
                else sum(value > 0 for value in dataset_returns) / len(completed)
            ),
            "total_trade_count": trade_count,
            "worst_dataset_max_drawdown": max(
                (
                    float(item["walk_forward_summary"].get("max_drawdown") or 0)
                    for item in completed
                ),
                default=0.0,
            ),
            "walk_forward_fold_count": len(folds),
            "completed_walk_forward_fold_count": len(completed_folds),
            "profitable_fold_ratio": (
                None
                if not group_profitable_fold_ratios
                else statistics.fmean(
                    statistics.fmean(values) for values in group_profitable_fold_ratios.values()
                )
            ),
            "worst_fold_max_drawdown": max(
                (
                    float(fold["validation_metrics"].get("max_drawdown") or 0)
                    for fold in completed_folds
                ),
                default=None,
            ),
            "out_of_sample_trade_count": trade_count,
            "certification_trade_count": certification_trade_count,
            "out_of_sample_expectancy_i64": (
                certification_net_pnl / certification_trade_count
                if certification_trade_count > 0
                else None
            ),
            "minimum_completed_folds_per_group": min(
                (math.floor(statistics.fmean(values)) for values in group_fold_counts.values()),
                default=0,
            ),
            "minimum_studied_trading_days_per_group": min(
                (len(days) for days in group_study_days.values()), default=0
            ),
            "parameter_stability": (
                None
                if not group_parameter_stabilities
                else statistics.fmean(
                    statistics.fmean(values) for values in group_parameter_stabilities.values()
                )
            ),
        }
    )
    return metrics


def _group_weighted_daily_rows(
    group_days: dict[str, dict[str, list[float]]],
) -> list[dict[str, Any]]:
    portfolio_days: dict[str, list[float]] = {}
    for days in group_days.values():
        for trading_day, values in days.items():
            portfolio_days.setdefault(trading_day, []).append(statistics.fmean(values))
    return [
        {
            "trading_day": day,
            "daily_return": statistics.fmean(portfolio_days[day]),
            "contributing_group_count": len(portfolio_days[day]),
        }
        for day in sorted(portfolio_days)
    ]


def _out_of_sample_daily_rows(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    group_days: dict[str, dict[str, list[float]]] = {}
    for item in results:
        if item.get("status") != "completed":
            continue
        group = str(item["independence_group"])
        for row in item.get("oos_daily_returns", []):
            group_days.setdefault(group, {}).setdefault(str(row["trading_day"]), []).append(
                float(row["daily_return"])
            )
    rows = [
        {
            "series_kind": "independence_group",
            "independence_group": group,
            "trading_day": day,
            "daily_return": statistics.fmean(values),
            "constituent_count": len(values),
        }
        for group in sorted(group_days)
        for day, values in sorted(group_days[group].items())
    ]
    rows.extend(
        {
            "series_kind": "portfolio",
            "independence_group": None,
            "trading_day": row["trading_day"],
            "daily_return": row["daily_return"],
            "constituent_count": row["contributing_group_count"],
        }
        for row in _group_weighted_daily_rows(group_days)
    )
    return rows


def _child_runs(results: list[dict[str, Any]], walk_forward: bool) -> list[dict[str, Any]]:
    if not walk_forward:
        return [
            {
                "dataset_id": item["dataset_id"],
                "run_id": item["run_id"],
                "run_signature": item["run_signature"],
                "role": "fixed",
            }
            for item in results
            if item.get("status") == "completed"
        ]
    child_runs: list[dict[str, Any]] = []
    for item in results:
        for fold in item.get("folds", []):
            for ranking in fold.get("training_ranking", []):
                if ranking.get("train_run_id"):
                    child_runs.append(
                        {
                            "dataset_id": item["dataset_id"],
                            "run_id": ranking["train_run_id"],
                            "run_signature": ranking["train_run_signature"],
                            "role": "train_candidate",
                            "fold_index": fold["fold_index"],
                            "candidate_index": ranking["evaluation_index"],
                        }
                    )
            if fold.get("validation_run_id"):
                child_runs.append(
                    {
                        "dataset_id": item["dataset_id"],
                        "run_id": fold["validation_run_id"],
                        "run_signature": fold["validation_run_signature"],
                        "role": "validation",
                        "fold_index": fold["fold_index"],
                    }
                )
    return child_runs


def run_research_study(
    payload: dict[str, Any],
    guard: PathGuard,
    cancelled: threading.Event,
    progress: Progress | None = None,
) -> str:
    _validate(payload)
    output = guard.resolve(str(payload["output_path"]))
    if output.exists():
        raise ValueError("completed research study directory already exists")
    output.parent.mkdir(parents=True, exist_ok=True)
    signature = str(payload.get("study_signature") or _signature(payload))
    journal_path = output.parent / f"{output.name}.journal.json"
    results = _load_journal(journal_path, signature)
    completed_ids = {str(item.get("dataset_id")) for item in results}
    _write_journal(journal_path, payload, signature, results)
    total = len(payload["datasets"])
    walk_forward = isinstance(payload.get("walk_forward"), dict)
    for dataset_index, item in enumerate(payload["datasets"]):
        dataset_id = str(item["dataset_id"])
        if dataset_id in completed_ids:
            continue
        if cancelled.is_set():
            raise InterruptedError("research study cancelled")
        if progress is not None:
            progress(
                len(results) / total,
                {
                    "total_count": total,
                    "completed_count": len(results),
                    "current_dataset_id": dataset_id,
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
            "dataset": {
                key: item[key] for key in ("dataset_id", "data_revision", "bars_path", "meta_path")
            },
            "algorithm": payload["strategy"],
            "parameters": payload["parameters"],
            "range": item["range"],
            "execution": payload["execution"],
            "capital": payload["capital"],
            "random_seed": payload["random_seed"],
            "output_path": f"runs/{run_id}",
        }
        try:
            if walk_forward:
                result, _ = run_dataset_walk_forward(
                    payload,
                    item,
                    dataset_index,
                    guard,
                    cancelled,
                    lambda value, current_dataset_id=dataset_id: (
                        progress(
                            (len(results) + value) / total,
                            {
                                "total_count": total,
                                "completed_count": len(results),
                                "current_dataset_id": current_dataset_id,
                            },
                        )
                        if progress is not None
                        else None
                    ),
                )
                results.append(result)
                _write_journal(journal_path, payload, signature, results)
                continue
            run_path = guard.resolve(f"runs/{run_id}")
            if (run_path / "_SUCCESS").is_file():
                result_ref = f"runs/{run_id}"
            else:
                result_ref = run_backtest(child, guard, cancelled)
            summary = json.loads(
                guard.resolve(f"{result_ref}/summary.json").read_text(encoding="utf-8")
            )
            results.append(
                {
                    "dataset_id": dataset_id,
                    "data_revision": item["data_revision"],
                    "independence_group": item["independence_group"],
                    "trading_day_count": item["trading_day_count"],
                    "status": "completed",
                    "run_id": run_id,
                    "run_signature": item["run_signature"],
                    "summary": summary,
                    "daily_returns": _daily_rows(guard, run_id),
                }
            )
        except InterruptedError:
            raise
        except Exception as exc:
            results.append(
                {
                    "dataset_id": dataset_id,
                    "data_revision": item["data_revision"],
                    "independence_group": item["independence_group"],
                    "trading_day_count": item["trading_day_count"],
                    "status": "failed",
                    "error": {"code": "DATASET_BACKTEST_FAILED", "message": str(exc)},
                }
            )
        _write_journal(journal_path, payload, signature, results)
    aggregate = _aggregate_walk_forward(results) if walk_forward else _aggregate(results)
    if walk_forward:
        aggregate["attempted_parameter_combinations"] = attempted_parameter_combinations(results)
    stress_aggregates: list[dict[str, Any]] = []
    stress_details: list[dict[str, Any]] = []
    stress_child_runs: list[dict[str, Any]] = []
    if isinstance(payload.get("stress_test"), dict):
        stress_aggregates, stress_details, stress_child_runs = run_stress_suite(
            payload, results, guard, cancelled
        )
        aggregate["stress_scenarios"] = stress_aggregates
        aggregate["first_failure_scenario"] = next(
            (
                str(item["scenario_id"])
                for item in stress_aggregates
                if item.get("failure_reason") is not None
            ),
            None,
        )
    oos_daily_rows = _out_of_sample_daily_rows(results) if walk_forward else []
    statistical_evidence: dict[str, Any] | None = None
    statistical_child_runs: list[dict[str, Any]] = []
    if isinstance(payload.get("statistical_validation"), dict):
        statistical_evidence, statistical_child_runs = run_statistical_validation(
            payload, results, aggregate, oos_daily_rows, guard, cancelled
        )
        aggregate["statistical_evidence"] = {
            key: value
            for key, value in statistical_evidence.items()
            if key != "parameter_neighborhood_runs"
        }
        aggregate["certification"] = statistical_evidence["certification"]
    public_results = [
        {
            key: value
            for key, value in item.items()
            if key not in {"daily_returns", "oos_daily_returns", "study_trading_days"}
        }
        for item in results
    ]
    child_runs = _child_runs(results, walk_forward) + stress_child_runs + statistical_child_runs
    manifest = {
        "schema_version": 1,
        "research_study_id": payload["research_study_id"],
        "study_signature": signature,
        "aggregator_version": AGGREGATOR_VERSION,
        "trace_id": payload["trace_id"],
        "study_mode": (
            "walk_forward_certification"
            if statistical_evidence is not None
            else "walk_forward_stress"
            if stress_aggregates
            else "walk_forward"
            if walk_forward
            else "fixed_parameters"
        ),
        "timeframe": payload["datasets"][0]["timeframe"],
        "strategy": payload["strategy"],
        "parameters": payload["parameters"],
        "datasets": [
            {
                **{
                    key: item[key]
                    for key in (
                        "dataset_id",
                        "data_revision",
                        "independence_group",
                        "trading_day_count",
                        "range",
                    )
                },
                **(
                    {}
                    if walk_forward
                    else {"run_id": item["run_id"], "run_signature": item["run_signature"]}
                ),
            }
            for item in payload["datasets"]
        ],
        "child_runs": child_runs,
        "execution": payload["execution"],
        "capital": payload["capital"],
        "random_seed": payload["random_seed"],
        "aggregate": aggregate,
        "engine": {
            "engine_version": ENGINE_VERSION,
            "python_version": "3.14",
            "contract_version": CONTRACT_VERSION,
        },
        "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
    if walk_forward:
        manifest["walk_forward"] = payload["walk_forward"]
        manifest["artifacts"] = {
            "out_of_sample_daily_returns": (
                f"{payload['output_path']}/out_of_sample_daily_returns.parquet"
            )
        }
    if stress_aggregates:
        manifest["stress_test"] = payload["stress_test"]
        manifest["artifacts"]["stress_results"] = f"{payload['output_path']}/stress_results.json"
    if statistical_evidence is not None:
        manifest["statistical_validation"] = payload["statistical_validation"]
        manifest["artifacts"]["statistical_evidence"] = (
            f"{payload['output_path']}/statistical_evidence.json"
        )
    temporary = output.parent / f".{output.name}.tmp-{uuid.uuid4().hex}"
    temporary.mkdir()
    try:
        (temporary / "research-study.json").write_text(
            json.dumps(manifest, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
        )
        (temporary / "results.json").write_text(
            json.dumps(public_results, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        if walk_forward:
            pq.write_table(
                pa.Table.from_pylist(
                    oos_daily_rows,
                    schema=pa.schema(
                        [
                            ("series_kind", pa.string()),
                            ("independence_group", pa.string()),
                            ("trading_day", pa.string()),
                            ("daily_return", pa.float64()),
                            ("constituent_count", pa.int32()),
                        ]
                    ),
                ),
                temporary / "out_of_sample_daily_returns.parquet",
                compression="zstd",
            )
        if stress_aggregates:
            (temporary / "stress_results.json").write_text(
                json.dumps(stress_details, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
        if statistical_evidence is not None:
            (temporary / "statistical_evidence.json").write_text(
                json.dumps(
                    statistical_evidence,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                encoding="utf-8",
            )
        (temporary / "_SUCCESS").write_bytes(b"")
        os.replace(temporary, output)
    finally:
        shutil.rmtree(temporary, ignore_errors=True)
    journal_path.unlink(missing_ok=True)
    if progress is not None:
        progress(1.0, {"total_count": total, "completed_count": total, "current_dataset_id": None})
    return str(payload["output_path"])
