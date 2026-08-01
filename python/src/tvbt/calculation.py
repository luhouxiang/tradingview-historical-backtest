from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from tvbt import ENGINE_VERSION
from tvbt.chan.algorithm import calculate_chan
from tvbt.indicators import resolve
from tvbt.storage.path_guard import PathGuard


class CalculationError(ValueError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def calculate(payload: dict[str, Any], guard: PathGuard, cancelled: threading.Event) -> str:
    dataset = payload.get("dataset")
    algorithm = payload.get("algorithm")
    parameters = payload.get("parameters")
    if (
        not isinstance(dataset, dict)
        or not isinstance(algorithm, dict)
        or not isinstance(parameters, dict)
    ):
        raise CalculationError("dataset, algorithm and parameters are required")
    if algorithm.get("kind") == "chan":
        try:
            return calculate_chan(payload, guard, cancelled)
        except ValueError as exc:
            raise CalculationError(str(exc)) from exc
    resolved = resolve(str(algorithm.get("algorithm_id", "")))
    if resolved is None:
        raise CalculationError("unknown indicator algorithm")
    definition, compute = resolved
    for key in ("kind", "algorithm_id", "algorithm_version", "source_hash"):
        if algorithm.get(key) != definition[key]:
            raise CalculationError(f"algorithm {key} does not match engine definition")
    bars_path = guard.resolve(str(dataset.get("bars_path", "")))
    meta_path = guard.resolve(str(dataset.get("meta_path", "")))
    output_path = guard.resolve(str(payload.get("output_path", "")))
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    scale = int(meta["price"]["price_scale"])
    table = pq.read_table(
        bars_path,
        columns=["bar_index", "open_i64", "high_i64", "low_i64", "close_i64"],
    )
    if cancelled.is_set():
        raise InterruptedError("calculation cancelled")
    columns: dict[str, list[float]] = {}
    for source in ("open", "high", "low", "close"):
        columns[source] = [value / scale for value in table[f"{source}_i64"].to_pylist()]
    values = compute(columns, parameters)
    if cancelled.is_set():
        raise InterruptedError("calculation cancelled")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp = output_path.parent / f".{output_path.name}.tmp-{uuid.uuid4().hex}"
    temp.mkdir()
    try:
        result_columns: dict[str, Any] = {
            "bar_index": pa.array(table["bar_index"].to_pylist(), type=pa.int64())
        }
        for name, series in values.items():
            result_columns[name] = pa.array(series, type=pa.float64())
        values_path = temp / "values.parquet"
        pq.write_table(pa.table(result_columns), values_path, compression="zstd")
        indices = table["bar_index"].to_pylist()
        manifest = {
            "schema_version": 1,
            "cache_key": payload["cache_key"],
            "dataset_id": dataset["dataset_id"],
            "data_revision": dataset["data_revision"],
            "algorithm": algorithm,
            "parameters": parameters,
            "calculation_mode": payload["calculation_mode"],
            "engine_version": ENGINE_VERSION,
            "coverage": {
                "bar_count": len(indices),
                "first_bar_index": indices[0] if indices else 0,
                "last_bar_index": indices[-1] if indices else 0,
            },
            "outputs": list(values),
            "values_sha256": _sha256(values_path),
            "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }
        (temp / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
        )
        (temp / "_SUCCESS").write_bytes(b"")
        if output_path.exists():
            if (output_path / "_SUCCESS").is_file():
                return guard.relative(output_path)
            raise CalculationError("incomplete cache output already exists")
        os.replace(temp, output_path)
    finally:
        if temp.exists():
            for child in temp.iterdir():
                child.unlink()
            temp.rmdir()
    return guard.relative(output_path)
