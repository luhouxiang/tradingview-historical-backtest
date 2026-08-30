from __future__ import annotations

import json
import math
import statistics
import threading
from pathlib import Path
from typing import Any, cast

import pyarrow.parquet as pq

from tvbt import ENGINE_VERSION
from tvbt.backtest import run_backtest
from tvbt.optimization import (
    _assign_ranks,
    _constraints_satisfied,
    _signature,
    expand_search_space,
    select_candidates,
)
from tvbt.storage.path_guard import PathGuard


def trading_day_folds(
    bars_path: Path,
    overall_range: dict[str, Any],
    train_days: int,
    validation_days: int,
    step_days: int,
) -> list[dict[str, Any]]:
    if train_days < 2 or validation_days < 1 or step_days < validation_days:
        raise ValueError("walk-forward windows are invalid or overlap")
    table = pq.read_table(bars_path, columns=["bar_index", "trading_day"])
    rows = cast(list[dict[str, Any]], table.to_pylist())
    first = int(overall_range["from_bar_index"])
    last = int(overall_range["to_bar_index"])
    warmup = int(overall_range["warmup_from_bar_index"])
    if rows and warmup != min(int(row["bar_index"]) for row in rows):
        raise ValueError("walk-forward warmup must start at the dataset beginning")
    bounds: dict[str, list[int]] = {}
    order: list[str] = []
    for row in rows:
        bar_index = int(row["bar_index"])
        if bar_index < first or bar_index > last:
            continue
        day = str(row["trading_day"])
        if day not in bounds:
            bounds[day] = [bar_index, bar_index]
            order.append(day)
        else:
            bounds[day][1] = bar_index
    folds: list[dict[str, Any]] = []
    for train_start in range(0, len(order) - train_days - validation_days + 1, step_days):
        validation_start = train_start + train_days
        validation_end = validation_start + validation_days - 1
        train_first, train_last = order[train_start], order[validation_start - 1]
        validation_first, validation_last = order[validation_start], order[validation_end]
        folds.append(
            {
                "fold_index": len(folds),
                "train_trading_day_from": train_first,
                "train_trading_day_to": train_last,
                "validation_trading_day_from": validation_first,
                "validation_trading_day_to": validation_last,
                "train_range": {
                    "warmup_from_bar_index": warmup,
                    "from_bar_index": bounds[train_first][0],
                    "to_bar_index": bounds[train_last][1],
                },
                "validation_range": {
                    "warmup_from_bar_index": warmup,
                    "from_bar_index": bounds[validation_first][0],
                    "to_bar_index": bounds[validation_last][1],
                },
            }
        )
    return folds


def trading_days_in_range(bars_path: Path, overall_range: dict[str, Any]) -> list[str]:
    table = pq.read_table(bars_path, columns=["bar_index", "trading_day"])
    first = int(overall_range["from_bar_index"])
    last = int(overall_range["to_bar_index"])
    return list(
        dict.fromkeys(
            str(day)
            for bar_index, day in zip(
                table.column("bar_index").to_pylist(),
                table.column("trading_day").to_pylist(),
                strict=True,
            )
            if first <= int(bar_index) <= last
        )
    )


def _run(
    payload: dict[str, Any],
    dataset: dict[str, Any],
    parameters: dict[str, Any],
    range_value: dict[str, Any],
    run_id: str,
    guard: PathGuard,
    cancelled: threading.Event,
    execution: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    execution_facts = dataset["execution"] if execution is None else execution
    facts = {
        "dataset": {
            "dataset_id": dataset["dataset_id"],
            "data_revision": dataset["data_revision"],
        },
        "algorithm": payload["strategy"],
        "parameters": parameters,
        "range": range_value,
        "execution": execution_facts,
        "capital": payload["capital"],
        "random_seed": payload["random_seed"],
        "engine_version": ENGINE_VERSION,
    }
    signature = _signature(facts)
    run_ref = f"runs/{run_id}"
    run_path = guard.resolve(run_ref)
    if (run_path / "_SUCCESS").is_file():
        manifest = json.loads((run_path / "run.json").read_text(encoding="utf-8"))
        if manifest.get("run_signature") != signature:
            raise ValueError("existing walk-forward run signature does not match")
    else:
        child = {
            "contract_version": payload["contract_version"],
            "request_id": payload["request_id"],
            "trace_id": payload["trace_id"],
            "job_id": run_id,
            "run_id": run_id,
            "run_signature": signature,
            "dataset": {
                key: dataset[key]
                for key in ("dataset_id", "data_revision", "bars_path", "meta_path")
            },
            "algorithm": payload["strategy"],
            "parameters": parameters,
            "range": range_value,
            "execution": execution_facts,
            "capital": payload["capital"],
            "random_seed": payload["random_seed"],
            "output_path": run_ref,
        }
        run_backtest(child, guard, cancelled)
    summary = json.loads((run_path / "summary.json").read_text(encoding="utf-8"))
    daily = cast(
        list[dict[str, Any]], pq.read_table(run_path / "daily_returns.parquet").to_pylist()
    )
    return summary, daily, signature


def run_dataset_walk_forward(
    payload: dict[str, Any],
    dataset: dict[str, Any],
    dataset_index: int,
    guard: PathGuard,
    cancelled: threading.Event,
    progress: Any | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    config = payload["walk_forward"]
    combinations = expand_search_space(config["search_space"]) if config["search_space"] else [{}]
    candidates = select_candidates(
        combinations,
        str(config["search"]["method"]),
        int(config["search"]["budget"]),
        int(config["search"]["random_seed"]),
    )
    folds = trading_day_folds(
        guard.resolve(str(dataset["bars_path"])),
        dataset["range"],
        int(config["train_trading_days"]),
        int(config["validation_trading_days"]),
        int(config["step_trading_days"]),
    )
    study_trading_days = trading_days_in_range(
        guard.resolve(str(dataset["bars_path"])), dataset["range"]
    )
    if not folds:
        return (
            {
                "dataset_id": dataset["dataset_id"],
                "data_revision": dataset["data_revision"],
                "independence_group": dataset["independence_group"],
                "trading_day_count": dataset["trading_day_count"],
                "status": "failed",
                "folds": [],
                "study_trading_days": study_trading_days,
                "error": {
                    "code": "INSUFFICIENT_TRADING_DAYS",
                    "message": "dataset cannot form one complete walk-forward fold",
                },
            },
            [],
        )
    fold_results: list[dict[str, Any]] = []
    child_runs: list[dict[str, Any]] = []
    previous_parameters: dict[str, Any] | None = None
    oos_daily: list[dict[str, Any]] = []
    for fold in folds:
        if cancelled.is_set():
            raise InterruptedError("walk-forward study cancelled")
        fold_index = int(fold["fold_index"])
        rankings: list[dict[str, Any]] = []
        for candidate_index, candidate in enumerate(candidates):
            parameters = {**payload["parameters"], **candidate}
            run_id = (
                f"{payload['research_study_id']}-d{dataset_index:02d}"
                f"-f{fold_index:03d}-c{candidate_index:03d}-train"
            )
            try:
                summary, _, signature = _run(
                    payload,
                    dataset,
                    parameters,
                    fold["train_range"],
                    run_id,
                    guard,
                    cancelled,
                )
                rankings.append(
                    {
                        "evaluation_index": candidate_index,
                        "parameters": parameters,
                        "constraints_satisfied": _constraints_satisfied(
                            summary, config["constraints"]
                        ),
                        "train_metrics": summary,
                        "train_run_id": run_id,
                        "train_run_signature": signature,
                    }
                )
                child_runs.append(
                    {
                        "dataset_id": dataset["dataset_id"],
                        "run_id": run_id,
                        "run_signature": signature,
                        "role": "train_candidate",
                        "fold_index": fold_index,
                        "candidate_index": candidate_index,
                    }
                )
            except InterruptedError:
                raise
            except Exception as exc:
                rankings.append(
                    {
                        "evaluation_index": candidate_index,
                        "parameters": parameters,
                        "constraints_satisfied": False,
                        "status": "failed",
                        "error": {"code": "TRAIN_RUN_FAILED", "message": str(exc)},
                    }
                )
        completed = [item for item in rankings if "train_metrics" in item]
        _assign_ranks(completed, config["objectives"], "train")
        feasible = [item for item in completed if item["constraints_satisfied"]]
        if not feasible:
            fold_results.append(
                {
                    **fold,
                    "dataset_id": dataset["dataset_id"],
                    "independence_group": dataset["independence_group"],
                    "status": "failed",
                    "training_ranking": rankings,
                    "error": {
                        "code": "NO_FEASIBLE_TRAIN_CANDIDATE",
                        "message": "no completed training candidate satisfied constraints",
                    },
                }
            )
            continue
        selected = min(feasible, key=lambda item: int(item["train_rank"]))
        parameters = dict(selected["parameters"])
        changed = []
        if previous_parameters is not None:
            changed = sorted(
                name
                for name in set(previous_parameters) | set(parameters)
                if previous_parameters.get(name) != parameters.get(name)
            )
        validation_id = (
            f"{payload['research_study_id']}-d{dataset_index:02d}-f{fold_index:03d}-validation"
        )
        try:
            validation, daily, validation_signature = _run(
                payload,
                dataset,
                parameters,
                fold["validation_range"],
                validation_id,
                guard,
                cancelled,
            )
            oos_daily.extend(daily)
            child_runs.append(
                {
                    "dataset_id": dataset["dataset_id"],
                    "run_id": validation_id,
                    "run_signature": validation_signature,
                    "role": "validation",
                    "fold_index": fold_index,
                }
            )
            fold_results.append(
                {
                    **fold,
                    "dataset_id": dataset["dataset_id"],
                    "independence_group": dataset["independence_group"],
                    "status": "completed",
                    "selected_parameters": parameters,
                    "training_ranking": sorted(
                        rankings, key=lambda item: int(item.get("train_rank", 10**9))
                    ),
                    "selected_train_metrics": selected["train_metrics"],
                    "validation_metrics": validation,
                    "selected_train_run_id": selected["train_run_id"],
                    "selected_train_run_signature": selected["train_run_signature"],
                    "validation_run_id": validation_id,
                    "validation_run_signature": validation_signature,
                    "parameter_changed": previous_parameters is not None and bool(changed),
                    "changed_parameter_names": changed,
                }
            )
            previous_parameters = parameters
        except InterruptedError:
            raise
        except Exception as exc:
            fold_results.append(
                {
                    **fold,
                    "dataset_id": dataset["dataset_id"],
                    "independence_group": dataset["independence_group"],
                    "status": "failed",
                    "selected_parameters": parameters,
                    "training_ranking": rankings,
                    "error": {"code": "VALIDATION_RUN_FAILED", "message": str(exc)},
                }
            )
        if progress is not None:
            progress((fold_index + 1) / len(folds))
    completed_folds = [item for item in fold_results if item["status"] == "completed"]
    returns = [float(row["daily_return"]) for row in oos_daily]
    summary = _return_metrics(returns)
    transitions = max(0, len(completed_folds) - 1)
    summary.update(
        {
            "studied_trading_day_count": len(study_trading_days),
            "fold_count": len(fold_results),
            "completed_fold_count": len(completed_folds),
            "failed_fold_count": len(fold_results) - len(completed_folds),
            "profitable_fold_ratio": (
                None
                if not completed_folds
                else sum(
                    float(item["validation_metrics"].get("total_return") or 0) > 0
                    for item in completed_folds
                )
                / len(completed_folds)
            ),
            "worst_fold_max_drawdown": max(
                (
                    float(item["validation_metrics"].get("max_drawdown") or 0)
                    for item in completed_folds
                ),
                default=None,
            ),
            "out_of_sample_trade_count": sum(
                int(item["validation_metrics"].get("trade_count") or 0) for item in completed_folds
            ),
            "parameter_stability": (
                None
                if transitions == 0
                else 1
                - sum(bool(item["parameter_changed"]) for item in completed_folds[1:]) / transitions
            ),
        }
    )
    return (
        {
            "dataset_id": dataset["dataset_id"],
            "data_revision": dataset["data_revision"],
            "independence_group": dataset["independence_group"],
            "trading_day_count": dataset["trading_day_count"],
            "status": "completed" if completed_folds else "failed",
            "folds": fold_results,
            "walk_forward_summary": summary,
            "oos_daily_returns": oos_daily,
            "study_trading_days": study_trading_days,
            **(
                {}
                if completed_folds
                else {
                    "error": {
                        "code": "NO_COMPLETED_WALK_FORWARD_FOLDS",
                        "message": "all walk-forward folds failed",
                    }
                }
            ),
        },
        child_runs,
    )


def _return_metrics(values: list[float]) -> dict[str, Any]:
    equity = 1.0
    peak = 1.0
    drawdown = 0.0
    for value in values:
        equity *= 1 + value
        peak = max(peak, equity)
        drawdown = max(drawdown, 0 if peak <= 0 else (peak - equity) / peak)
    deviation = statistics.stdev(values) if len(values) > 1 else 0.0
    mean = statistics.fmean(values) if values else 0.0
    return {
        "daily_return_count": len(values),
        "total_return": equity - 1,
        "annualized_return": (
            equity ** (252 / len(values)) - 1 if len(values) > 1 and equity > 0 else None
        ),
        "sharpe": mean / deviation * math.sqrt(252) if deviation > 0 else None,
        "annualized_volatility": deviation * math.sqrt(252) if len(values) > 1 else None,
        "max_drawdown": drawdown,
    }
