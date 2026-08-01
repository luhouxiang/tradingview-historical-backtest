from __future__ import annotations

import json
import threading
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from tvbt.chan.algorithm import definition
from tvbt.replay import generate_replay
from tvbt.storage.path_guard import PathGuard


def test_replay_writes_only_causal_events_through_requested_end(tmp_path: Path) -> None:
    dataset = tmp_path / "normalized" / "TEST.A1.1m" / "revision"
    dataset.mkdir(parents=True)
    levels = [0, 1, 2, 3, 4, 3, 2, 1]
    values = [levels[index % len(levels)] for index in range(30)]
    pq.write_table(
        pa.table(
            {
                "bar_index": pa.array(range(30), type=pa.int64()),
                "timestamp_utc": pa.array(
                    [1_700_000_000_000 + index * 60_000 for index in range(30)],
                    type=pa.int64(),
                ),
                "high_i64": pa.array([value * 10 + 5 for value in values], type=pa.int64()),
                "low_i64": pa.array([value * 10 for value in values], type=pa.int64()),
                "close_i64": pa.array([value * 10 + 2 for value in values], type=pa.int64()),
            }
        ),
        dataset / "bars.parquet",
    )
    (dataset / "meta.json").write_text(json.dumps({"price": {"price_scale": 1}}), encoding="utf-8")
    algorithm = definition()
    parameters = {
        name: rule["default"] for name, rule in algorithm["parameter_schema"]["properties"].items()
    }
    parameters["min_stroke_atr"] = 0
    payload = {
        "cache_key": "sha256:" + "4" * 64,
        "dataset": {
            "dataset_id": "TEST.A1.1m",
            "data_revision": "sha256:" + "1" * 64,
            "bars_path": "normalized/TEST.A1.1m/revision/bars.parquet",
            "meta_path": "normalized/TEST.A1.1m/revision/meta.json",
        },
        "algorithm": {
            key: algorithm[key]
            for key in ("kind", "algorithm_id", "algorithm_version", "source_hash")
        },
        "parameters": parameters,
        "range": {"from_bar_index": 8, "to_bar_index": 24, "warmup_from_bar_index": 0},
        "output_path": "cache/replay/key",
    }
    ref = generate_replay(payload, PathGuard(tmp_path), threading.Event())
    output = tmp_path / ref
    events = pq.read_table(output / "events.parquet").to_pylist()
    assert events
    assert all(event["known_at_bar_index"] <= 24 for event in events)
    assert [event["event_seq"] for event in events] == list(range(1, len(events) + 1))
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["range"] == {
        "from_bar_index": 8,
        "to_bar_index": 24,
        "warmup_from_bar_index": 0,
    }
    assert manifest["event_count"] == len(events)
    assert (output / "_SUCCESS").is_file()
