from __future__ import annotations

import json
import threading
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from tvbt.calculation import calculate
from tvbt.chan.algorithm import definition as chan_definition
from tvbt.indicators import resolve
from tvbt.storage.path_guard import PathGuard


def test_calculation_writes_atomic_parquet_cache(tmp_path: Path) -> None:
    guard = PathGuard(tmp_path)
    dataset_dir = tmp_path / "normalized" / "TEST.A1.1m" / "revision"
    dataset_dir.mkdir(parents=True)
    pq.write_table(
        pa.table(
            {
                "bar_index": pa.array([0, 1, 2, 3], type=pa.int64()),
                "open_i64": pa.array([100, 200, 300, 400], type=pa.int64()),
                "high_i64": pa.array([110, 210, 310, 410], type=pa.int64()),
                "low_i64": pa.array([90, 190, 290, 390], type=pa.int64()),
                "close_i64": pa.array([100, 200, 300, 400], type=pa.int64()),
            }
        ),
        dataset_dir / "bars.parquet",
    )
    (dataset_dir / "meta.json").write_text(
        json.dumps({"price": {"price_scale": 100}}), encoding="utf-8"
    )
    definition = resolve("ma")
    assert definition is not None
    payload = {
        "cache_key": "sha256:" + "3" * 64,
        "dataset": {
            "dataset_id": "TEST.A1.1m",
            "data_revision": "sha256:" + "1" * 64,
            "bars_path": "normalized/TEST.A1.1m/revision/bars.parquet",
            "meta_path": "normalized/TEST.A1.1m/revision/meta.json",
        },
        "algorithm": {
            key: definition[0][key]
            for key in ("kind", "algorithm_id", "algorithm_version", "source_hash")
        },
        "parameters": {"period": 2, "source": "close"},
        "calculation_mode": "full_history",
        "output_path": "cache/indicators/key",
    }
    result_ref = calculate(payload, guard, threading.Event())
    assert result_ref == "cache/indicators/key"
    output = tmp_path / result_ref
    assert (output / "_SUCCESS").is_file()
    values = pq.read_table(output / "values.parquet").to_pydict()
    assert values == {"bar_index": [0, 1, 2, 3], "ma": [None, 1.5, 2.5, 3.5]}
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["parameters"] == {"period": 2, "source": "close"}
    assert manifest["values_sha256"].startswith("sha256:")


def test_chan_calculation_writes_causal_structure_cache(tmp_path: Path) -> None:
    guard = PathGuard(tmp_path)
    dataset_dir = tmp_path / "normalized" / "TEST.A1.1m" / "revision"
    dataset_dir.mkdir(parents=True)
    levels = [0, 1, 2, 3, 4, 3, 2, 1]
    count = 25
    values = [levels[index % len(levels)] for index in range(count)]
    pq.write_table(
        pa.table(
            {
                "bar_index": pa.array(range(count), type=pa.int64()),
                "timestamp_utc": pa.array(
                    [1_700_000_000_000 + index * 60_000 for index in range(count)], type=pa.int64()
                ),
                "high_i64": pa.array([value * 10 + 5 for value in values], type=pa.int64()),
                "low_i64": pa.array([value * 10 for value in values], type=pa.int64()),
                "close_i64": pa.array([value * 10 + 2 for value in values], type=pa.int64()),
            }
        ),
        dataset_dir / "bars.parquet",
    )
    (dataset_dir / "meta.json").write_text(
        json.dumps({"price": {"price_scale": 1}}), encoding="utf-8"
    )
    definition = chan_definition()
    parameters = {
        name: rule["default"] for name, rule in definition["parameter_schema"]["properties"].items()
    }
    parameters["checkpoint_interval"] = 4
    payload = {
        "cache_key": "sha256:" + "4" * 64,
        "dataset": {
            "dataset_id": "TEST.A1.1m",
            "data_revision": "sha256:" + "1" * 64,
            "bars_path": "normalized/TEST.A1.1m/revision/bars.parquet",
            "meta_path": "normalized/TEST.A1.1m/revision/meta.json",
        },
        "algorithm": {
            key: definition[key]
            for key in ("kind", "algorithm_id", "algorithm_version", "source_hash")
        },
        "parameters": parameters,
        "calculation_mode": "causal_events",
        "output_path": "cache/chan/key",
    }
    result_ref = calculate(payload, guard, threading.Event())
    output = tmp_path / result_ref
    assert result_ref == "cache/chan/key"
    assert (output / "_SUCCESS").is_file()
    assert pq.read_table(output / "fractals.parquet").num_rows > 0
    assert pq.read_table(output / "bi.parquet").num_rows > 0
    assert (
        pq.read_table(output / "events.parquet").num_rows
        > pq.read_table(output / "bi.parquet").num_rows
    )
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["algorithm"]["kind"] == "chan"
    assert manifest["checkpoint"]["count"] == 6
    assert len(list((output / "checkpoints").glob("*.bin"))) == 6
