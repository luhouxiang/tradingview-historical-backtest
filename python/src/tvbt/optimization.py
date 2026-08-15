from __future__ import annotations

import hashlib
import itertools
import json
import math
import os
import random
import shutil
import threading
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from tvbt import CONTRACT_VERSION, ENGINE_VERSION
from tvbt.backtest import run_backtest
from tvbt.logging_config.logger import LOG_METADATA_FIELDS, format_fixed_text_entry
from tvbt.storage.path_guard import PathGuard

SUPPORTED_METRICS = {
    "total_return",
    "sharpe",
    "max_drawdown",
    "win_rate",
    "trade_count",
    "profit_factor",
    "expectancy_i64",
}


def expand_search_space(search_space: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the finite Cartesian product in stable declaration order."""
    names: list[str] = []
    dimensions: list[list[Any]] = []
    for item in search_space:
        name = str(item.get("name", ""))
        if not name or name in names:
            raise ValueError("search parameter names must be non-empty and unique")
        names.append(name)
        dimensions.append(_values(item))
    if not dimensions:
        raise ValueError("search_space must not be empty")
    return [dict(zip(names, values, strict=True)) for values in itertools.product(*dimensions)]


def select_candidates(
    combinations: list[dict[str, Any]], method: str, budget: int, seed: int
) -> list[dict[str, Any]]:
    if budget < 1 or budget > 100:
        raise ValueError("search budget must be between 1 and 100")
    if method == "grid":
        return combinations[:budget]
    if method == "random":
        indices = list(range(len(combinations)))
        random.Random(seed).shuffle(indices)
        return [combinations[index] for index in indices[:budget]]
    raise ValueError("search method must be grid or random")


def _values(item: dict[str, Any]) -> list[Any]:
    value_type = item.get("type")
    if value_type not in {"integer", "number", "boolean", "string"}:
        raise ValueError("unsupported search parameter type")
    candidates = item.get("candidates")
    if candidates is not None:
        if not isinstance(candidates, list) or not candidates:
            raise ValueError("candidates must be a non-empty array")
        values = [_coerce(value, value_type) for value in candidates]
    else:
        if value_type not in {"integer", "number"}:
            raise ValueError("non-numeric ranges must use candidates")
        try:
            minimum = float(item["minimum"])
            maximum = float(item["maximum"])
            step = float(item["step"])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("numeric ranges require minimum, maximum and step") from error
        if not all(math.isfinite(value) for value in (minimum, maximum, step)):
            raise ValueError("search range values must be finite")
        if step <= 0 or maximum < minimum:
            raise ValueError("invalid search range")
        count = math.floor((maximum - minimum) / step + 1e-12) + 1
        if count > 10_000:
            raise ValueError("a search dimension cannot exceed 10000 values")
        values = [_coerce(minimum + index * step, value_type) for index in range(count)]
    if len({json.dumps(value, sort_keys=True) for value in values}) != len(values):
        raise ValueError("search parameter candidates must be unique")
    return values


def _coerce(value: Any, value_type: str) -> Any:
    if value_type == "integer":
        if isinstance(value, bool) or not isinstance(value, (int, float)) or int(value) != value:
            raise ValueError("integer candidate is invalid")
        return int(value)
    if value_type == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("number candidate is invalid")
        result = float(value)
        if not math.isfinite(result):
            raise ValueError("number candidate must be finite")
        return result
    if value_type == "boolean":
        if not isinstance(value, bool):
            raise ValueError("boolean candidate is invalid")
        return value
    if not isinstance(value, str):
        raise ValueError("string candidate is invalid")
    return value


def _signature(facts: dict[str, Any]) -> str:
    encoded = json.dumps(facts, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _metric(summary: dict[str, Any], name: str) -> float | None:
    value = summary.get(name)
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def _constraints_satisfied(summary: dict[str, Any], constraints: list[dict[str, Any]]) -> bool:
    for constraint in constraints:
        value = _metric(summary, str(constraint["metric"]))
        if value is None:
            return False
        target = float(constraint["value"])
        if constraint["operator"] == ">=" and value < target:
            return False
        if constraint["operator"] == "<=" and value > target:
            return False
    return True


def _objective_key(
    evaluation: dict[str, Any], objectives: list[dict[str, Any]], split: str
) -> tuple[Any, ...]:
    values: list[Any] = []
    if split == "train":
        values.append(0 if evaluation["constraints_satisfied"] else 1)
    metrics = evaluation[f"{split}_metrics"]
    for objective in objectives:
        value = _metric(metrics, str(objective["metric"]))
        if value is None:
            values.append(float("inf"))
        elif objective["direction"] == "maximize":
            values.append(-value)
        else:
            values.append(value)
    values.append(evaluation["evaluation_index"])
    return tuple(values)


def _assign_ranks(
    evaluations: list[dict[str, Any]], objectives: list[dict[str, Any]], split: str
) -> None:
    ordered = sorted(evaluations, key=lambda value: _objective_key(value, objectives, split))
    for rank, evaluation in enumerate(ordered, 1):
        evaluation[f"{split}_rank"] = rank


def _validate_payload(payload: dict[str, Any]) -> None:
    for name in (
        "dataset",
        "algorithm",
        "base_parameters",
        "search",
        "ranges",
        "execution",
        "capital",
    ):
        if not isinstance(payload.get(name), dict):
            raise ValueError(f"{name} is required")
    for name in ("search_space", "objectives", "constraints"):
        if not isinstance(payload.get(name), list):
            raise ValueError(f"{name} must be an array")
    objectives = payload["objectives"]
    if not objectives:
        raise ValueError("at least one objective is required")
    for objective in objectives:
        if objective.get("metric") not in SUPPORTED_METRICS:
            raise ValueError("unsupported objective metric")
        if objective.get("direction") not in {"maximize", "minimize"}:
            raise ValueError("invalid objective direction")
    for constraint in payload["constraints"]:
        if constraint.get("metric") not in SUPPORTED_METRICS:
            raise ValueError("unsupported constraint metric")
        if constraint.get("operator") not in {">=", "<="}:
            raise ValueError("invalid constraint operator")
        if not isinstance(constraint.get("value"), (int, float)):
            raise ValueError("constraint value must be numeric")
    ranges = payload["ranges"]
    train, validation = ranges.get("train"), ranges.get("validation")
    if not isinstance(train, dict) or not isinstance(validation, dict):
        raise ValueError("train and validation ranges are required")
    for value in (train, validation):
        warmup = int(value["warmup_from_bar_index"])
        start = int(value["from_bar_index"])
        end = int(value["to_bar_index"])
        if warmup < 0 or start < warmup or end < start:
            raise ValueError("invalid study range")
    if int(train["to_bar_index"]) >= int(validation["from_bar_index"]):
        raise ValueError("training range must precede validation range")


def run_study(
    payload: dict[str, Any],
    guard: PathGuard,
    cancelled: threading.Event,
    progress: Callable[[float], None] | None = None,
) -> str:
    _validate_payload(payload)
    combinations = expand_search_space(payload["search_space"])
    search = payload["search"]
    candidates = select_candidates(
        combinations,
        str(search["method"]),
        int(search["budget"]),
        int(search["random_seed"]),
    )
    if not candidates:
        raise ValueError("search space produced no candidates")
    output = guard.resolve(str(payload["output_path"]))
    if output.exists():
        raise ValueError("formal study directory already exists")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.parent / f".{output.name}.tmp-{uuid.uuid4().hex}"
    temporary.mkdir()
    evaluations: list[dict[str, Any]] = []
    completed_runs = 0
    total_runs = len(candidates) * 2
    try:
        for index, candidate in enumerate(candidates):
            if cancelled.is_set():
                raise InterruptedError("optimization study cancelled")
            parameters = {**payload["base_parameters"], **candidate}
            summaries: dict[str, dict[str, Any]] = {}
            run_ids: dict[str, str] = {}
            run_signatures: dict[str, str] = {}
            for split in ("train", "validation"):
                run_id = f"{payload['study_id']}-e{index:03d}-{split}"
                run_ids[split] = run_id
                facts = {
                    "dataset": payload["dataset"],
                    "algorithm": payload["algorithm"],
                    "parameters": parameters,
                    "range": payload["ranges"][split],
                    "execution": payload["execution"],
                    "capital": payload["capital"],
                    "random_seed": int(search["random_seed"]),
                    "engine_version": ENGINE_VERSION,
                }
                run_signature = _signature(facts)
                run_signatures[split] = run_signature
                run_payload = {
                    "contract_version": payload["contract_version"],
                    "request_id": payload["request_id"],
                    "trace_id": payload["trace_id"],
                    "job_id": run_id,
                    "run_id": run_id,
                    "run_signature": run_signature,
                    "dataset": payload["dataset"],
                    "algorithm": payload["algorithm"],
                    "parameters": parameters,
                    "calculation_mode": "causal_events",
                    "cache_key": _signature({"study_id": payload["study_id"], **facts}),
                    "range": payload["ranges"][split],
                    "execution": payload["execution"],
                    "capital": payload["capital"],
                    "random_seed": int(search["random_seed"]),
                    "output_path": f"runs/{run_id}",
                }
                result_ref = run_backtest(run_payload, guard, cancelled)
                summary_path = guard.resolve(f"{result_ref}/summary.json")
                summaries[split] = json.loads(summary_path.read_text(encoding="utf-8"))
                completed_runs += 1
                if progress is not None:
                    progress(0.05 + completed_runs / total_runs * 0.9)
            evaluations.append(
                {
                    "evaluation_index": index,
                    "parameters": parameters,
                    "constraints_satisfied": _constraints_satisfied(
                        summaries["train"], payload["constraints"]
                    ),
                    "status": "completed",
                    "train_run_id": run_ids["train"],
                    "train_run_signature": run_signatures["train"],
                    "validation_run_id": run_ids["validation"],
                    "validation_run_signature": run_signatures["validation"],
                    "train_metrics": summaries["train"],
                    "validation_metrics": summaries["validation"],
                }
            )
        _assign_ranks(evaluations, payload["objectives"], "train")
        _assign_ranks(evaluations, payload["objectives"], "validation")
        selected = min(evaluations, key=lambda value: value["train_rank"])
        primary = payload["objectives"][0]
        train_primary = _metric(selected["train_metrics"], primary["metric"])
        validation_primary = _metric(selected["validation_metrics"], primary["metric"])
        rank_correlation = _rank_correlation(evaluations)
        top_train = sorted(evaluations, key=lambda value: value["train_rank"])[
            : min(3, len(evaluations))
        ]
        warnings: list[str] = []
        if not selected["constraints_satisfied"]:
            warnings.append("NO_TRAIN_CANDIDATE_SATISFIED_HARD_CONSTRAINTS")
        if rank_correlation is not None and rank_correlation < 0:
            warnings.append("TRAIN_VALIDATION_RANK_INVERSION")
        stability = {
            "selected_evaluation_index": selected["evaluation_index"],
            "selected_train_rank": selected["train_rank"],
            "selected_validation_rank": selected["validation_rank"],
            "primary_metric": primary["metric"],
            "train_primary_value": train_primary,
            "validation_primary_value": validation_primary,
            "primary_absolute_gap": (
                None
                if train_primary is None or validation_primary is None
                else abs(train_primary - validation_primary)
            ),
            "constraint_feasible_count": sum(
                evaluation["constraints_satisfied"] for evaluation in evaluations
            ),
            "top_train_evaluation_indices": [
                evaluation["evaluation_index"] for evaluation in top_train
            ],
            "top_train_validation_rank_mean": sum(
                evaluation["validation_rank"] for evaluation in top_train
            )
            / len(top_train),
            "train_validation_rank_correlation": rank_correlation,
            "stable_selection": selected["validation_rank"]
            <= max(1, math.ceil(len(evaluations) / 4)),
            "warnings": warnings,
        }
        manifest = {
            "schema_version": 1,
            "study_id": payload["study_id"],
            "trace_id": payload["trace_id"],
            "dataset": {
                "dataset_id": payload["dataset"]["dataset_id"],
                "data_revision": payload["dataset"]["data_revision"],
            },
            "strategy": {
                "strategy_id": payload["algorithm"]["algorithm_id"],
                "version": payload["algorithm"]["algorithm_version"],
                "source_hash": payload["algorithm"]["source_hash"],
            },
            "base_parameters": payload["base_parameters"],
            "search_space": payload["search_space"],
            "objectives": payload["objectives"],
            "constraints": payload["constraints"],
            "search": payload["search"],
            "ranges": payload["ranges"],
            "execution": payload["execution"],
            "capital": payload["capital"],
            "evaluation_count": len(evaluations),
            "selected_evaluation_index": selected["evaluation_index"],
            "engine": {
                "engine_version": ENGINE_VERSION,
                "python_version": "3.14",
                "contract_version": CONTRACT_VERSION,
            },
            "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }
        (temporary / "study.json").write_text(
            json.dumps(manifest, separators=(",", ":")), encoding="utf-8"
        )
        (temporary / "evaluations.json").write_text(
            json.dumps(evaluations, separators=(",", ":")), encoding="utf-8"
        )
        (temporary / "stability.json").write_text(
            json.dumps(stability, separators=(",", ":")), encoding="utf-8"
        )
        log_event = {
            "timestamp": datetime.now().astimezone(),
            "level": "INFO",
            "event": "optimization.study.completed",
            "message": "optimization study completed",
            "source_file": "tvbt/optimization.py",
            "source_line": run_study.__code__.co_firstlineno,
            "source_function": "run_study",
            "study_id": payload["study_id"],
            "trace_id": payload["trace_id"],
            "evaluation_count": len(evaluations),
            "selected_evaluation_index": selected["evaluation_index"],
        }
        (temporary / "log.ndjson").write_text(
            _format_study_log_event(log_event) + "\n", encoding="utf-8"
        )
        (temporary / "_SUCCESS").write_bytes(b"")
        os.replace(temporary, output)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return guard.relative(output)


def _format_study_log_event(event: dict[str, Any]) -> str:
    fields = {key: value for key, value in event.items() if key not in LOG_METADATA_FIELDS}
    timestamp = event["timestamp"]
    assert isinstance(timestamp, datetime)
    return format_fixed_text_entry(
        timestamp=timestamp,
        level=str(event["level"]),
        source_file=str(event["source_file"]),
        source_line=int(event["source_line"]),
        event=str(event["event"]),
        message=str(event["message"]),
        fields=fields,
    )


def _rank_correlation(evaluations: list[dict[str, Any]]) -> float | None:
    if len(evaluations) < 2:
        return None
    train = [float(value["train_rank"]) for value in evaluations]
    validation = [float(value["validation_rank"]) for value in evaluations]
    train_mean = sum(train) / len(train)
    validation_mean = sum(validation) / len(validation)
    numerator = sum(
        (left - train_mean) * (right - validation_mean)
        for left, right in zip(train, validation, strict=True)
    )
    train_variance = sum((value - train_mean) ** 2 for value in train)
    validation_variance = sum((value - validation_mean) ** 2 for value in validation)
    denominator = math.sqrt(train_variance * validation_variance)
    return None if denominator == 0 else numerator / denominator
