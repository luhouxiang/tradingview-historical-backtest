from __future__ import annotations

import json
from pathlib import Path

import pyarrow.parquet as pq

from tvbt.chan import dump_checkpoint
from tvbt.chan.storage import ChanResult, write_chan_cache
from tvbt.storage.path_guard import PathGuard


def payload() -> dict[str, object]:
    digest = "sha256:" + "1" * 64
    return {
        "cache_key": "sha256:" + "3" * 64,
        "dataset": {"dataset_id": "TEST.A1.1m", "data_revision": digest},
        "algorithm": {
            "kind": "chan",
            "algorithm_id": "chan_standard",
            "algorithm_version": "1.0.0",
            "source_hash": "sha256:" + "2" * 64,
        },
        "parameters": {"min_fractal_gap": 5, "checkpoint_interval": 4},
        "calculation_mode": "causal_events",
        "output_path": "cache/chan/example",
    }


def test_chan_cache_writes_typed_tables_checkpoints_and_success_last(tmp_path: Path) -> None:
    guard = PathGuard(tmp_path)
    checkpoint = dump_checkpoint("1.0.0", 4, {"state": "test"})
    result = ChanResult(
        bar_count=6,
        first_bar_index=0,
        last_bar_index=5,
        merged_bar_count=5,
        fractals=[
            {
                "object_id": "fractal-1",
                "bar_index": 2,
                "time": 120_000,
                "price_i64": 110,
                "fractal_type": "top",
                "confirmed": True,
                "confirmed_at_bar_index": 4,
                "known_at_bar_index": 4,
                "object_revision": 1,
            }
        ],
        events=[
            {
                "event_seq": 1,
                "known_at_bar_index": 4,
                "object_type": "fractal",
                "object_id": "fractal-1",
                "operation": "upsert",
                "object_revision": 1,
                "payload_json": "{}",
            }
        ],
        checkpoints={4: checkpoint},
    )
    relative = write_chan_cache(payload(), guard, result)
    directory = tmp_path / relative
    assert (directory / "_SUCCESS").is_file()
    assert (directory / "checkpoints" / "4.bin").read_bytes() == checkpoint
    assert pq.read_table(directory / "fractals.parquet").num_rows == 1
    assert pq.read_table(directory / "bi.parquet").schema.names == [
        "object_id",
        "start_bar_index",
        "start_time",
        "start_price_i64",
        "end_bar_index",
        "end_time",
        "end_price_i64",
        "direction",
        "confirmed",
        "confirmed_at_bar_index",
        "known_at_bar_index",
        "object_revision",
    ]
    assert (
        pq.read_table(directory / "segments.parquet").schema.names
        == pq.read_table(directory / "bi.parquet").schema.names
    )
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["counts"]["events"] == 1
    assert manifest["counts"]["segments"] == 0
    assert manifest["files"]["segments"]["path"] == "segments.parquet"
    assert manifest["counts"]["segment_zhongshu"] == 0
    assert manifest["files"]["divergences"]["path"] == "divergences.parquet"
    assert manifest["files"]["trade_points"]["path"] == "trade_points.parquet"
    assert manifest["checkpoint"]["last_bar_index"] == 4
    assert all(value["sha256"].startswith("sha256:") for value in manifest["files"].values())


def test_completed_chan_cache_is_reused_without_overwrite(tmp_path: Path) -> None:
    guard = PathGuard(tmp_path)
    result = ChanResult(0, 0, 0, 0)
    first = write_chan_cache(payload(), guard, result)
    marker = tmp_path / first / "manifest.json"
    before = marker.read_bytes()
    second = write_chan_cache(payload(), guard, result)
    assert second == first
    assert marker.read_bytes() == before
