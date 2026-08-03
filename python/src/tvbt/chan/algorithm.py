from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

from tvbt.chan.checkpoint import dump_checkpoint
from tvbt.chan.engine import ChanEngine, ChanParameters, RawBar
from tvbt.chan.storage import ChanResult, write_chan_cache
from tvbt.storage.path_guard import PathGuard


def _source_hash() -> str:
    digest = hashlib.sha256()
    directory = Path(__file__).parent
    for name in (
        "algorithm.py",
        "checkpoint.py",
        "engine.py",
        "events.py",
        "reference.py",
        "storage.py",
    ):
        digest.update(name.encode())
        digest.update((directory / name).read_bytes())
    return "sha256:" + digest.hexdigest()


def definition() -> dict[str, Any]:
    integer = {"type": "integer", "minimum": 1, "maximum": 100_000}
    return {
        "kind": "chan",
        "algorithm_id": "chan_engineering",
        "algorithm_version": ChanEngine.algorithm_version,
        "source_hash": _source_hash(),
        "name": "Algo-ui Reference Chan",
        "input_schema": "bars.v1",
        "parameter_schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "checkpoint_interval": {**integer, "default": 1024},
            },
            "required": [
                "checkpoint_interval",
            ],
        },
        "outputs": [
            {
                "name": object_type,
                "display_name": display_name,
                "pane": "main",
                "series_type": "semantic_objects",
                "object_type": object_type,
            }
            for object_type, display_name in (
                ("fractal", "分型"),
                ("bi", "笔"),
                ("segment", "段"),
                ("zhongshu", "中枢"),
            )
        ],
        "warmup": {"kind": "formula", "expression": "three independent bars; bi requires five"},
        "causal": True,
    }


def calculate_chan(payload: dict[str, Any], guard: PathGuard, cancelled: threading.Event) -> str:
    runtime, indices, checkpoints = run_chan(payload, guard, cancelled, write_checkpoints=True)
    rows = runtime.result_rows()
    result = ChanResult(
        bar_count=len(indices),
        first_bar_index=indices[0] if indices else 0,
        last_bar_index=indices[-1] if indices else 0,
        merged_bar_count=len(runtime.included),
        fractals=rows["fractals"],
        bi=rows["bi"],
        segments=rows["segments"],
        zhongshu=rows["zhongshu"],
        events=[event.row() for event in runtime.emitter.events],
        checkpoints=checkpoints,
    )
    return write_chan_cache(payload, guard, result)


def run_chan(
    payload: dict[str, Any],
    guard: PathGuard,
    cancelled: threading.Event,
    *,
    last_bar_index: int | None = None,
    write_checkpoints: bool = False,
) -> tuple[ChanEngine, list[int], dict[int, bytes]]:
    dataset = payload.get("dataset")
    algorithm = payload.get("algorithm")
    parameters = payload.get("parameters")
    if (
        not isinstance(dataset, dict)
        or not isinstance(algorithm, dict)
        or not isinstance(parameters, dict)
    ):
        raise ValueError("dataset, algorithm and parameters are required")
    expected = definition()
    for key in ("kind", "algorithm_id", "algorithm_version", "source_hash"):
        if algorithm.get(key) != expected[key]:
            raise ValueError(f"algorithm {key} does not match engine definition")
    bars_path = guard.resolve(str(dataset.get("bars_path", "")))
    meta_path = guard.resolve(str(dataset.get("meta_path", "")))
    json.loads(meta_path.read_text(encoding="utf-8"))
    table = pq.read_table(
        bars_path,
        columns=["bar_index", "timestamp_utc", "high_i64", "low_i64", "close_i64"],
    ).to_pydict()
    runtime = ChanEngine(
        ChanParameters(
            checkpoint_interval=int(parameters["checkpoint_interval"]),
        )
    )
    checkpoints: dict[int, bytes] = {}
    indices: list[int] = []
    interval = runtime.parameters.checkpoint_interval
    for position, bar_index in enumerate(table["bar_index"]):
        raw_index = int(bar_index)
        if last_bar_index is not None and raw_index > last_bar_index:
            break
        if position % 256 == 0 and cancelled.is_set():
            raise InterruptedError("calculation cancelled")
        runtime.update(
            RawBar(
                bar_index=raw_index,
                time=int(table["timestamp_utc"][position]),
                high_i64=int(table["high_i64"][position]),
                low_i64=int(table["low_i64"][position]),
                close_i64=int(table["close_i64"][position]),
            )
        )
        indices.append(raw_index)
        if write_checkpoints and (position + 1) % interval == 0:
            checkpoints[raw_index] = dump_checkpoint(
                runtime.algorithm_version, raw_index, runtime.export_state()
            )
    if cancelled.is_set():
        raise InterruptedError("calculation cancelled")
    return runtime, indices, checkpoints
