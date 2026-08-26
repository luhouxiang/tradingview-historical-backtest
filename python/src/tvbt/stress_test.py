from __future__ import annotations

import statistics
import threading
from typing import Any

from tvbt.storage.path_guard import PathGuard
from tvbt.walk_forward import _return_metrics, _run


def _scenarios(participation_rate: float) -> list[dict[str, Any]]:
    return [
        {"scenario_id": "baseline", "cost_multiplier": 1.0},
        {"scenario_id": "cost_1_5x", "cost_multiplier": 1.5},
        {"scenario_id": "cost_2x", "cost_multiplier": 2.0},
        {
            "scenario_id": "extra_slippage_1_tick",
            "cost_multiplier": 1.0,
            "additional_slippage_ticks": 1.0,
        },
        {
            "scenario_id": "extra_slippage_2_ticks",
            "cost_multiplier": 1.0,
            "additional_slippage_ticks": 2.0,
        },
        {"scenario_id": "delay_1_bar", "cost_multiplier": 1.0, "additional_delay_bars": 1},
        {
            "scenario_id": "volume_participation_10pct",
            "cost_multiplier": 1.0,
            "max_volume_participation_rate": participation_rate,
            "fill_mode": "volume_cap_ioc",
        },
    ]


def _execution(base: dict[str, Any], scenario: dict[str, Any]) -> dict[str, Any]:
    result = {
        **base,
        "stress_scenario_id": scenario["scenario_id"],
        "cost_multiplier": float(scenario.get("cost_multiplier", 1)),
        "additional_slippage_ticks": float(scenario.get("additional_slippage_ticks", 0)),
        "additional_delay_bars": int(scenario.get("additional_delay_bars", 0)),
        "fill_mode": str(scenario.get("fill_mode", "unlimited")),
    }
    if "max_volume_participation_rate" in scenario:
        result["max_volume_participation_rate"] = float(scenario["max_volume_participation_rate"])
    else:
        result.pop("max_volume_participation_rate", None)
    return result


def _aggregate_scenario(scenario: dict[str, Any], runs: list[dict[str, Any]]) -> dict[str, Any]:
    completed = [run for run in runs if run["status"] == "completed"]
    group_days: dict[str, dict[str, list[float]]] = {}
    for run in completed:
        group = str(run["independence_group"])
        for row in run["daily_returns"]:
            group_days.setdefault(group, {}).setdefault(str(row["trading_day"]), []).append(
                float(row["daily_return"])
            )
    portfolio: dict[str, list[float]] = {}
    for days in group_days.values():
        for day, values in days.items():
            portfolio.setdefault(day, []).append(statistics.fmean(values))
    metrics = _return_metrics([statistics.fmean(portfolio[day]) for day in sorted(portfolio)])
    requested = sum(int(run["summary"].get("requested_quantity") or 0) for run in completed)
    filled = sum(int(run["summary"].get("filled_quantity") or 0) for run in completed)
    total_return = metrics["total_return"] if completed else None
    return {
        "scenario_id": scenario["scenario_id"],
        "status": "completed" if completed else "failed",
        "cost_multiplier": float(scenario.get("cost_multiplier", 1)),
        "additional_slippage_ticks": float(scenario.get("additional_slippage_ticks", 0)),
        "additional_delay_bars": int(scenario.get("additional_delay_bars", 0)),
        "max_volume_participation_rate": scenario.get("max_volume_participation_rate"),
        "fill_mode": str(scenario.get("fill_mode", "unlimited")),
        "completed_run_count": len(completed),
        "failed_run_count": len(runs) - len(completed),
        "daily_return_count": metrics["daily_return_count"],
        "total_return": total_return,
        "max_drawdown": metrics["max_drawdown"] if completed else None,
        "trade_count": sum(int(run["summary"].get("trade_count") or 0) for run in completed),
        "requested_quantity": requested,
        "filled_quantity": filled,
        "fill_rate": filled / requested if requested else None,
        "return_degradation": None,
        "drawdown_degradation": None,
        "fill_rate_degradation": None,
        "failure_reason": (
            "NO_COMPLETED_RUNS"
            if not completed
            else "NON_POSITIVE_TOTAL_RETURN"
            if total_return is not None and total_return <= 0
            else None
        ),
    }


def run_stress_suite(
    payload: dict[str, Any],
    results: list[dict[str, Any]],
    guard: PathGuard,
    cancelled: threading.Event,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    config = payload["stress_test"]
    datasets = {str(item["dataset_id"]): item for item in payload["datasets"]}
    scenario_details: list[dict[str, Any]] = []
    child_runs: list[dict[str, Any]] = []
    aggregates: list[dict[str, Any]] = []
    for scenario_index, scenario in enumerate(
        _scenarios(float(config["volume_participation_rate"]))
    ):
        execution = _execution(payload["execution"], scenario)
        runs: list[dict[str, Any]] = []
        for dataset_index, result in enumerate(results):
            dataset = datasets[str(result["dataset_id"])]
            for fold in result.get("folds", []):
                if fold.get("status") != "completed":
                    continue
                if cancelled.is_set():
                    raise InterruptedError("stress test cancelled")
                fold_index = int(fold["fold_index"])
                run_id = (
                    f"{payload['research_study_id']}-d{dataset_index:02d}-f{fold_index:03d}"
                    f"-stress-{scenario_index:02d}"
                )
                try:
                    summary, daily, signature = _run(
                        payload,
                        dataset,
                        dict(fold["selected_parameters"]),
                        fold["validation_range"],
                        run_id,
                        guard,
                        cancelled,
                        execution,
                    )
                    runs.append(
                        {
                            "dataset_id": dataset["dataset_id"],
                            "independence_group": dataset["independence_group"],
                            "fold_index": fold_index,
                            "status": "completed",
                            "run_id": run_id,
                            "run_signature": signature,
                            "summary": summary,
                            "daily_returns": daily,
                        }
                    )
                    child_runs.append(
                        {
                            "dataset_id": dataset["dataset_id"],
                            "run_id": run_id,
                            "run_signature": signature,
                            "role": "stress",
                            "fold_index": fold_index,
                            "scenario_id": scenario["scenario_id"],
                        }
                    )
                except InterruptedError:
                    raise
                except Exception as exc:
                    runs.append(
                        {
                            "dataset_id": dataset["dataset_id"],
                            "independence_group": dataset["independence_group"],
                            "fold_index": fold_index,
                            "status": "failed",
                            "error": {"code": "STRESS_RUN_FAILED", "message": str(exc)},
                        }
                    )
        aggregate = _aggregate_scenario(scenario, runs)
        aggregates.append(aggregate)
        scenario_details.append(
            {
                "scenario": aggregate,
                "runs": [
                    {key: value for key, value in run.items() if key != "daily_returns"}
                    for run in runs
                ],
            }
        )
    baseline = aggregates[0]
    for aggregate in aggregates:
        aggregate["return_degradation"] = (
            None
            if baseline["total_return"] is None or aggregate["total_return"] is None
            else float(baseline["total_return"]) - float(aggregate["total_return"])
        )
        aggregate["drawdown_degradation"] = (
            None
            if baseline["max_drawdown"] is None or aggregate["max_drawdown"] is None
            else float(aggregate["max_drawdown"]) - float(baseline["max_drawdown"])
        )
        aggregate["fill_rate_degradation"] = (
            None
            if baseline["fill_rate"] is None or aggregate["fill_rate"] is None
            else float(baseline["fill_rate"]) - float(aggregate["fill_rate"])
        )
    return aggregates, scenario_details, child_runs
