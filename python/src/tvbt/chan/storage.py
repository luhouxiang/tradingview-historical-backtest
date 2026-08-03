from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from tvbt import ENGINE_VERSION
from tvbt.storage.path_guard import PathGuard

FRACTAL_SCHEMA = pa.schema(
    [
        ("object_id", pa.string()),
        ("bar_index", pa.int64()),
        ("time", pa.int64()),
        ("price_i64", pa.int64()),
        ("fractal_type", pa.string()),
        ("confirmed", pa.bool_()),
        ("confirmed_at_bar_index", pa.int64()),
        ("known_at_bar_index", pa.int64()),
        ("object_revision", pa.int64()),
    ]
)
LINE_SCHEMA = pa.schema(
    [
        ("object_id", pa.string()),
        ("start_bar_index", pa.int64()),
        ("start_time", pa.int64()),
        ("start_price_i64", pa.int64()),
        ("end_bar_index", pa.int64()),
        ("end_time", pa.int64()),
        ("end_price_i64", pa.int64()),
        ("direction", pa.string()),
        ("confirmed", pa.bool_()),
        ("confirmed_at_bar_index", pa.int64()),
        ("known_at_bar_index", pa.int64()),
        ("object_revision", pa.int64()),
    ]
)
ZHONGSHU_SCHEMA = pa.schema(
    [
        ("object_id", pa.string()),
        ("start_bar_index", pa.int64()),
        ("start_time", pa.int64()),
        ("end_bar_index", pa.int64()),
        ("end_time", pa.int64()),
        ("zg_i64", pa.int64()),
        ("zd_i64", pa.int64()),
        ("confirmed", pa.bool_()),
        ("confirmed_at_bar_index", pa.int64()),
        ("status", pa.string()),
        ("leave_direction", pa.string()),
        ("known_at_bar_index", pa.int64()),
        ("object_revision", pa.int64()),
    ]
)
EVENT_SCHEMA = pa.schema(
    [
        ("event_seq", pa.int64()),
        ("known_at_bar_index", pa.int64()),
        ("object_type", pa.string()),
        ("object_id", pa.string()),
        ("operation", pa.string()),
        ("object_revision", pa.int64()),
        ("payload_json", pa.string()),
    ]
)


@dataclass
class ChanResult:
    bar_count: int
    first_bar_index: int
    last_bar_index: int
    merged_bar_count: int
    fractals: list[dict[str, Any]] = field(default_factory=list)
    bi: list[dict[str, Any]] = field(default_factory=list)
    segments: list[dict[str, Any]] = field(default_factory=list)
    zhongshu: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    checkpoints: dict[int, bytes] = field(default_factory=dict)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def write_chan_cache(payload: dict[str, Any], guard: PathGuard, result: ChanResult) -> str:
    dataset = payload["dataset"]
    algorithm = payload["algorithm"]
    parameters = payload["parameters"]
    if algorithm.get("kind") != "chan" or payload.get("calculation_mode") != "causal_events":
        raise ValueError("Chan cache requires a causal_events Chan calculation")
    output_path = guard.resolve(str(payload["output_path"]))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.parent / f".{output_path.name}.tmp-{uuid.uuid4().hex}"
    temporary.mkdir()
    try:
        tables = {
            "fractals": (result.fractals, FRACTAL_SCHEMA),
            "bi": (result.bi, LINE_SCHEMA),
            "segments": (result.segments, LINE_SCHEMA),
            "zhongshu": (result.zhongshu, ZHONGSHU_SCHEMA),
            "events": (result.events, EVENT_SCHEMA),
        }
        files: dict[str, dict[str, int | str]] = {}
        for name, (rows, schema) in tables.items():
            path = temporary / f"{name}.parquet"
            pq.write_table(pa.Table.from_pylist(rows, schema=schema), path, compression="zstd")
            files[name] = {"path": path.name, "row_count": len(rows), "sha256": _sha256(path)}

        checkpoint_directory = temporary / "checkpoints"
        checkpoint_directory.mkdir()
        for bar_index, data in sorted(result.checkpoints.items()):
            (checkpoint_directory / f"{bar_index}.bin").write_bytes(data)
        checkpoint_indices = sorted(result.checkpoints)
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
                "bar_count": result.bar_count,
                "first_bar_index": result.first_bar_index,
                "last_bar_index": result.last_bar_index,
            },
            "counts": {
                "merged_bars": result.merged_bar_count,
                "fractals": len(result.fractals),
                "bi": len(result.bi),
                "segments": len(result.segments),
                "zhongshu": len(result.zhongshu),
                "events": len(result.events),
            },
            "files": files,
            "checkpoint": {
                "format": "tvbt-chan-checkpoint-v1",
                "algorithm_version": algorithm["algorithm_version"],
                "interval_bars": int(parameters["checkpoint_interval"]),
                "count": len(checkpoint_indices),
                "last_bar_index": checkpoint_indices[-1] if checkpoint_indices else None,
            },
            "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }
        (temporary / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
        )
        (temporary / "_SUCCESS").write_bytes(b"")
        if output_path.exists():
            if (output_path / "_SUCCESS").is_file():
                return guard.relative(output_path)
            raise ValueError("incomplete Chan cache output already exists")
        os.replace(temporary, output_path)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return guard.relative(output_path)
