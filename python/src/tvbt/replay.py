from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from tvbt import ENGINE_VERSION
from tvbt.chan.algorithm import run_chan
from tvbt.chan.storage import EVENT_SCHEMA
from tvbt.storage.path_guard import PathGuard
from tvbt.strategy import run_strategy


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def generate_replay(payload: dict[str, Any], guard: PathGuard, cancelled: threading.Event) -> str:
    range_value = payload.get("range")
    strategy = payload.get("algorithm")
    dataset = payload.get("dataset")
    parameters = payload.get("parameters")
    if not all(isinstance(value, dict) for value in (range_value, strategy, dataset, parameters)):
        raise ValueError("dataset, algorithm, parameters and range are required")
    assert isinstance(range_value, dict)
    assert isinstance(strategy, dict)
    assert isinstance(dataset, dict)
    assert isinstance(parameters, dict)
    start = int(range_value["from_bar_index"])
    end = int(range_value["to_bar_index"])
    warmup = int(range_value["warmup_from_bar_index"])
    if warmup < 0 or start < warmup or end < start:
        raise ValueError("replay range must satisfy 0 <= warmup <= from <= to")
    if strategy.get("kind") == "chan":
        runtime, indices, _ = run_chan(payload, guard, cancelled, last_bar_index=end)
        event_rows = [event.row() for event in runtime.emitter.events]
    elif strategy.get("kind") == "strategy":
        result = run_strategy(payload, guard, cancelled, last_bar_index=end)
        indices = [bar.bar_index for bar in result.bars]
        event_rows = result.events
    else:
        raise ValueError("replay requires a causal Chan or strategy algorithm")
    if not indices or end > indices[-1] or start < indices[0]:
        raise ValueError("replay range is outside dataset coverage")
    events = [event for event in event_rows if int(event["known_at_bar_index"]) <= end]
    output = guard.resolve(str(payload.get("output_path", "")))
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.parent / f".{output.name}.tmp-{uuid.uuid4().hex}"
    temporary.mkdir()
    try:
        events_path = temporary / "events.parquet"
        pq.write_table(
            pa.Table.from_pylist(events, schema=EVENT_SCHEMA), events_path, compression="zstd"
        )
        manifest = {
            "schema_version": 1,
            "cache_key": payload["cache_key"],
            "dataset_id": dataset["dataset_id"],
            "data_revision": dataset["data_revision"],
            "strategy": strategy,
            "parameters": parameters,
            "range": {
                "from_bar_index": start,
                "to_bar_index": end,
                "warmup_from_bar_index": warmup,
            },
            "engine_version": ENGINE_VERSION,
            "event_count": len(events),
            "events_sha256": _sha256(events_path),
            "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }
        (temporary / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
        )
        (temporary / "_SUCCESS").write_bytes(b"")
        if output.exists():
            if (output / "_SUCCESS").is_file():
                return guard.relative(output)
            raise ValueError("incomplete replay cache output already exists")
        os.replace(temporary, output)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return guard.relative(output)
