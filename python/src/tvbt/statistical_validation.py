from __future__ import annotations

import hashlib
import json
import math
import random
import statistics
import threading
from collections import defaultdict
from collections.abc import Callable
from typing import Any, cast

import pyarrow.parquet as pq

from tvbt.storage.path_guard import PathGuard
from tvbt.walk_forward import _return_metrics, _run

CERTIFICATION_RULES_VERSION = "1.0.0"


def _combination_id(parameters: dict[str, Any]) -> str:
    encoded = json.dumps(parameters, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def attempted_parameter_combinations(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    combinations: dict[str, dict[str, Any]] = {}
    for result in results:
        for fold in result.get("folds", []):
            for ranking in fold.get("training_ranking", []):
                parameters = ranking.get("parameters")
                if not isinstance(parameters, dict):
                    continue
                identifier = _combination_id(parameters)
                entry = combinations.setdefault(
                    identifier,
                    {
                        "combination_id": identifier,
                        "parameters": parameters,
                        "attempt_count": 0,
                        "completed_count": 0,
                    },
                )
                entry["attempt_count"] += 1
                entry["completed_count"] += int("train_metrics" in ranking)
    return sorted(combinations.values(), key=lambda item: str(item["combination_id"]))


def _metric_values(values: list[float]) -> tuple[float, float, float]:
    metrics = _return_metrics(values)
    return (
        float(metrics["total_return"]),
        statistics.fmean(values),
        float(metrics["max_drawdown"]),
    )


def _percentile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def moving_block_bootstrap(
    values: list[float],
    *,
    block_size: int,
    iterations: int,
    confidence_level: float,
    random_seed: int,
) -> dict[str, Any]:
    if len(values) < block_size:
        return {
            "method": "moving_block_bootstrap",
            "sample_count": len(values),
            "block_size_trading_days": block_size,
            "iterations": iterations,
            "confidence_level": confidence_level,
            "random_seed": random_seed,
            "metrics": {
                name: {
                    "point_estimate": None,
                    "lower": None,
                    "upper": None,
                    "reason": "insufficient_oos_daily_returns_for_block_size",
                }
                for name in ("total_return", "mean_daily_return", "max_drawdown")
            },
        }
    generator = random.Random(random_seed)
    distributions: list[list[float]] = [[], [], []]
    starts = len(values) - block_size + 1
    for _ in range(iterations):
        sample: list[float] = []
        while len(sample) < len(values):
            start = generator.randrange(starts)
            sample.extend(values[start : start + block_size])
        for target, metric in zip(
            distributions, _metric_values(sample[: len(values)]), strict=True
        ):
            target.append(metric)
    alpha = (1 - confidence_level) / 2
    points = _metric_values(values)
    return {
        "method": "moving_block_bootstrap",
        "sample_count": len(values),
        "block_size_trading_days": block_size,
        "iterations": iterations,
        "confidence_level": confidence_level,
        "random_seed": random_seed,
        "metrics": {
            name: {
                "point_estimate": point,
                "lower": _percentile(distribution, alpha),
                "upper": _percentile(distribution, 1 - alpha),
                "reason": None,
            }
            for name, point, distribution in zip(
                ("total_return", "mean_daily_return", "max_drawdown"),
                points,
                distributions,
                strict=True,
            )
        },
    }


def holm_adjust(comparisons: list[dict[str, Any]], alpha: float) -> list[dict[str, Any]]:
    eligible = [item for item in comparisons if item.get("raw_p_value") is not None]
    ordered = sorted(eligible, key=lambda item: float(item["raw_p_value"]))
    running = 0.0
    total = len(ordered)
    for index, item in enumerate(ordered):
        adjusted = min(1.0, float(item["raw_p_value"]) * (total - index))
        running = max(running, adjusted)
        item["holm_adjusted_p_value"] = running
        item["reject_equal_mean"] = running <= alpha
    for item in comparisons:
        if item.get("raw_p_value") is None:
            item["holm_adjusted_p_value"] = None
            item["reject_equal_mean"] = False
    return comparisons


def _two_sided_normal_p(left: list[float], right: list[float]) -> float | None:
    if len(left) < 2 or len(right) < 2:
        return None
    variance = statistics.variance(left) / len(left) + statistics.variance(right) / len(right)
    difference = statistics.fmean(left) - statistics.fmean(right)
    if variance == 0:
        return 1.0 if difference == 0 else 0.0
    z_score = abs(difference) / math.sqrt(variance)
    return 2 * (1 - statistics.NormalDist().cdf(z_score))


def candidate_multiple_comparisons(
    candidate_returns: dict[str, list[float]], alpha: float
) -> dict[str, Any]:
    candidates = sorted(candidate_returns)
    comparisons: list[dict[str, Any]] = []
    for left_index, left in enumerate(candidates):
        for right in candidates[left_index + 1 :]:
            raw = _two_sided_normal_p(candidate_returns[left], candidate_returns[right])
            comparisons.append(
                {
                    "left_combination_id": left,
                    "right_combination_id": right,
                    "left_sample_count": len(candidate_returns[left]),
                    "right_sample_count": len(candidate_returns[right]),
                    "raw_p_value": raw,
                    "reason": None
                    if raw is not None
                    else "insufficient_candidate_oos_daily_returns",
                }
            )
    return {
        "method": "holm_bonferroni_after_two_sided_normal_mean_test",
        "scope": "selected_parameter_combinations_with_out_of_sample_daily_returns",
        "alpha": alpha,
        "candidate_count": len(candidates),
        "comparison_count": len(comparisons),
        "multiple_comparison_warning": len(candidates) > 1,
        "warning": (
            "Multiple candidate comparisons were attempted; use Holm-adjusted p-values."
            if len(candidates) > 1
            else None
        ),
        "comparisons": holm_adjust(comparisons, alpha),
    }


def _numeric_neighbors(
    search_space: list[dict[str, Any]], selected: dict[str, Any]
) -> list[dict[str, Any]]:
    neighbors: list[dict[str, Any]] = []
    for parameter in search_space:
        if parameter.get("type") not in {"integer", "number"}:
            continue
        name = str(parameter["name"])
        selected_value = selected.get(name)
        if isinstance(selected_value, bool) or not isinstance(selected_value, (int, float)):
            continue
        candidates = sorted(
            {
                float(value)
                for value in parameter.get("candidates", [])
                if isinstance(value, (int, float)) and not isinstance(value, bool)
            }
        )
        numeric = float(selected_value)
        lower = [value for value in candidates if value < numeric]
        upper = [value for value in candidates if value > numeric]
        for direction, values in (("lower", lower), ("upper", upper)):
            if not values:
                continue
            value = max(values) if direction == "lower" else min(values)
            if parameter.get("type") == "integer":
                value = int(value)
            neighbors.append({"parameter_name": name, "direction": direction, "value": value})
    return neighbors


def _safe_name(value: str) -> str:
    return "".join(character if character.isalnum() else "_" for character in value)[:32]


def _neighbor_passed(summary: dict[str, Any]) -> bool:
    expectancy = summary.get("expectancy_i64")
    return (
        float(summary.get("total_return") or 0) > 0
        and expectancy is not None
        and float(expectancy) > 0
        and float(summary.get("max_drawdown") or 0) <= 0.2
    )


def run_parameter_neighborhood(
    payload: dict[str, Any],
    results: list[dict[str, Any]],
    guard: PathGuard,
    cancelled: threading.Event,
    progress: Callable[[float, dict[str, Any]], None] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    datasets = {str(item["dataset_id"]): item for item in payload["datasets"]}
    search_space = cast(list[dict[str, Any]], payload["walk_forward"]["search_space"])
    details: list[dict[str, Any]] = []
    child_runs: list[dict[str, Any]] = []
    group_dataset_passes: dict[str, dict[str, list[bool]]] = defaultdict(lambda: defaultdict(list))
    total_neighbors = sum(
        len(_numeric_neighbors(search_space, dict(fold["selected_parameters"])))
        for result in results
        for fold in result.get("folds", [])
        if fold.get("status") == "completed"
    )
    for dataset_index, result in enumerate(results):
        dataset_id = str(result["dataset_id"])
        dataset = datasets[dataset_id]
        group = str(dataset["independence_group"])
        for fold in result.get("folds", []):
            if fold.get("status") != "completed":
                continue
            selected = dict(fold["selected_parameters"])
            for neighbor in _numeric_neighbors(search_space, selected):
                if cancelled.is_set():
                    raise InterruptedError("parameter neighborhood validation cancelled")
                parameters = {**selected, neighbor["parameter_name"]: neighbor["value"]}
                fold_index = int(fold["fold_index"])
                run_id = (
                    f"{payload['research_study_id']}-d{dataset_index:02d}-f{fold_index:03d}"
                    f"-neighbor-{_safe_name(neighbor['parameter_name'])}-{neighbor['direction']}"
                )
                try:
                    summary, _, signature = _run(
                        payload,
                        dataset,
                        parameters,
                        fold["validation_range"],
                        run_id,
                        guard,
                        cancelled,
                    )
                    passed = _neighbor_passed(summary)
                    detail = {
                        "dataset_id": dataset_id,
                        "independence_group": group,
                        "fold_index": fold_index,
                        **neighbor,
                        "parameters": parameters,
                        "status": "completed",
                        "passed_core_gates": passed,
                        "run_id": run_id,
                        "run_signature": signature,
                        "summary": summary,
                    }
                    child_runs.append(
                        {
                            "dataset_id": dataset_id,
                            "run_id": run_id,
                            "run_signature": signature,
                            "role": "neighbor",
                            "fold_index": fold_index,
                            "parameter_name": neighbor["parameter_name"],
                            "neighbor_direction": neighbor["direction"],
                        }
                    )
                    group_dataset_passes[group][dataset_id].append(passed)
                except InterruptedError:
                    raise
                except Exception as exc:
                    detail = {
                        "dataset_id": dataset_id,
                        "independence_group": group,
                        "fold_index": fold_index,
                        **neighbor,
                        "parameters": parameters,
                        "status": "failed",
                        "passed_core_gates": False,
                        "error": {"code": "NEIGHBOR_RUN_FAILED", "message": str(exc)},
                    }
                    group_dataset_passes[group][dataset_id].append(False)
                details.append(detail)
                if progress is not None:
                    progress(
                        len(details) / total_neighbors if total_neighbors else 1.0,
                        {
                            "stage": "parameter_neighborhood",
                            "completed_count": len(details),
                            "total_count": total_neighbors,
                            "current_dataset_id": dataset_id,
                            "current_scenario_id": None,
                            "current_fold_index": fold_index,
                        },
                    )
    if progress is not None and total_neighbors == 0:
        progress(
            1.0,
            {
                "stage": "parameter_neighborhood",
                "completed_count": 0,
                "total_count": 0,
                "current_dataset_id": None,
                "current_scenario_id": None,
                "current_fold_index": None,
            },
        )
    group_rates = {
        group: statistics.fmean(
            statistics.fmean(1.0 if value else 0.0 for value in passes)
            for passes in datasets.values()
            if passes
        )
        for group, datasets in group_dataset_passes.items()
    }
    pass_rate = statistics.fmean(group_rates.values()) if group_rates else None
    return (
        {
            "method": "adjacent_declared_numeric_candidate",
            "core_gates": [
                "total_return_positive",
                "expectancy_positive",
                "max_drawdown_at_most_20_percent",
            ],
            "evaluated_neighbor_count": len(details),
            "completed_neighbor_count": sum(item["status"] == "completed" for item in details),
            "group_pass_rates": group_rates,
            "pass_rate": pass_rate,
            "required_pass_rate": 0.6,
            "passed": pass_rate is not None and pass_rate >= 0.6,
            "reason": None if group_rates else "no_adjacent_numeric_parameter_candidates",
        },
        details,
        child_runs,
    )


def _candidate_oos_returns(
    results: list[dict[str, Any]], guard: PathGuard
) -> dict[str, list[float]]:
    candidate_group_days: dict[str, dict[str, dict[str, list[float]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(list))
    )
    for result in results:
        group = str(result["independence_group"])
        for fold in result.get("folds", []):
            if fold.get("status") != "completed" or not fold.get("validation_run_id"):
                continue
            identifier = _combination_id(dict(fold["selected_parameters"]))
            path = guard.resolve(f"runs/{fold['validation_run_id']}/daily_returns.parquet")
            for row in cast(list[dict[str, Any]], pq.read_table(path).to_pylist()):
                candidate_group_days[identifier][group][str(row["trading_day"])].append(
                    float(row["daily_return"])
                )
    candidate_returns: dict[str, list[float]] = {}
    for identifier, groups in candidate_group_days.items():
        portfolio_days: dict[str, list[float]] = defaultdict(list)
        for days in groups.values():
            for day, values in days.items():
                portfolio_days[day].append(statistics.fmean(values))
        candidate_returns[identifier] = [
            statistics.fmean(portfolio_days[day]) for day in sorted(portfolio_days)
        ]
    return candidate_returns


def certification(aggregate: dict[str, Any], statistical: dict[str, Any]) -> dict[str, Any]:
    stress = {str(item["scenario_id"]): item for item in aggregate.get("stress_scenarios", [])}
    bootstrap_lower = (
        statistical.get("bootstrap", {})
        .get("metrics", {})
        .get("mean_daily_return", {})
        .get("lower")
    )
    neighborhood_rate = statistical.get("parameter_neighborhood", {}).get("pass_rate")
    gates: list[tuple[str, str, Any, int | float, Callable[[Any], bool]]] = [
        (
            "minimum_walk_forward_folds",
            "research_candidate",
            aggregate.get("minimum_completed_folds_per_group", 0),
            4,
            lambda value: value >= 4,
        ),
        (
            "minimum_oos_closed_trades_research",
            "research_candidate",
            aggregate.get("certification_trade_count", 0),
            100,
            lambda value: value >= 100,
        ),
        (
            "positive_oos_net_return",
            "research_candidate",
            aggregate.get("total_return"),
            0,
            lambda value: value is not None and value > 0,
        ),
        (
            "positive_oos_expectancy",
            "research_candidate",
            aggregate.get("out_of_sample_expectancy_i64"),
            0,
            lambda value: value is not None and value > 0,
        ),
        (
            "maximum_drawdown",
            "research_candidate",
            aggregate.get("max_drawdown"),
            0.2,
            lambda value: value is not None and value <= 0.2,
        ),
        (
            "minimum_eligible_independence_groups",
            "reliable_candidate",
            aggregate.get("eligible_independence_group_count", 0),
            3,
            lambda value: value >= 3,
        ),
        (
            "minimum_trading_days_per_group",
            "reliable_candidate",
            aggregate.get("minimum_studied_trading_days_per_group", 0),
            504,
            lambda value: value >= 504,
        ),
        (
            "minimum_oos_closed_trades_reliable",
            "reliable_candidate",
            aggregate.get("certification_trade_count", 0),
            200,
            lambda value: value >= 200,
        ),
        (
            "minimum_profitable_fold_ratio",
            "reliable_candidate",
            aggregate.get("profitable_fold_ratio"),
            0.7,
            lambda value: value is not None and value >= 0.7,
        ),
        (
            "cost_2x_positive",
            "reliable_candidate",
            stress.get("cost_2x", {}).get("total_return"),
            0,
            lambda value: value is not None and value > 0,
        ),
        (
            "delay_1_bar_positive",
            "reliable_candidate",
            stress.get("delay_1_bar", {}).get("total_return"),
            0,
            lambda value: value is not None and value > 0,
        ),
        (
            "bootstrap_mean_daily_return_lower_positive",
            "reliable_candidate",
            bootstrap_lower,
            0,
            lambda value: value is not None and value > 0,
        ),
        (
            "parameter_neighborhood_pass_rate",
            "reliable_candidate",
            neighborhood_rate,
            0.6,
            lambda value: value is not None and value >= 0.6,
        ),
    ]
    matrix = [
        {
            "gate_id": gate_id,
            "required_for": required_for,
            "passed": bool(predicate(actual)),
            "actual": actual,
            "threshold": threshold,
            "reason": "passed" if predicate(actual) else f"{gate_id}_not_met",
        }
        for gate_id, required_for, actual, threshold, predicate in gates
    ]
    research_passed = all(
        item["passed"] for item in matrix if item["required_for"] == "research_candidate"
    )
    reliable_passed = research_passed and all(
        item["passed"] for item in matrix if item["required_for"] == "reliable_candidate"
    )
    return {
        "rules_version": CERTIFICATION_RULES_VERSION,
        "tier": (
            "reliable_candidate"
            if reliable_passed
            else "research_candidate"
            if research_passed
            else "exploratory"
        ),
        "reliable_candidate_is_historical_only": True,
        "research_candidate_passed": research_passed,
        "reliable_candidate_passed": reliable_passed,
        "reasons": [item["reason"] for item in matrix if not item["passed"]],
        "evidence_matrix": matrix,
    }


def run_statistical_validation(
    payload: dict[str, Any],
    results: list[dict[str, Any]],
    aggregate: dict[str, Any],
    oos_daily_rows: list[dict[str, Any]],
    guard: PathGuard,
    cancelled: threading.Event,
    progress: Callable[[float, dict[str, Any]], None] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    config = payload["statistical_validation"]
    portfolio_returns = [
        float(row["daily_return"]) for row in oos_daily_rows if row["series_kind"] == "portfolio"
    ]
    bootstrap = moving_block_bootstrap(
        portfolio_returns,
        block_size=int(config["block_size_trading_days"]),
        iterations=int(config["iterations"]),
        confidence_level=float(config["confidence_level"]),
        random_seed=int(config["random_seed"]),
    )
    if progress is not None:
        progress(
            0.05,
            {
                "stage": "bootstrap",
                "completed_count": int(config["iterations"]),
                "total_count": int(config["iterations"]),
                "current_dataset_id": None,
                "current_scenario_id": None,
                "current_fold_index": None,
            },
        )
    neighborhood, details, child_runs = run_parameter_neighborhood(
        payload,
        results,
        guard,
        cancelled,
        (
            lambda value, detail: (
                progress(0.05 + value * 0.9, detail) if progress is not None else None
            )
        ),
    )
    evidence = {
        "method_version": config["method_version"],
        "bootstrap": bootstrap,
        "multiple_comparisons": candidate_multiple_comparisons(
            _candidate_oos_returns(results, guard), float(config["holm_alpha"])
        ),
        "parameter_neighborhood": neighborhood,
        "parameter_neighborhood_runs": details,
    }
    evidence["certification"] = certification(aggregate, evidence)
    if progress is not None:
        progress(
            1.0,
            {
                "stage": "aggregation",
                "completed_count": 1,
                "total_count": 1,
                "current_dataset_id": None,
                "current_scenario_id": None,
                "current_fold_index": None,
            },
        )
    return evidence, child_runs
