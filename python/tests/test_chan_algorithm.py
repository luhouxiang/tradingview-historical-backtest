from __future__ import annotations

import json
import threading
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from tvbt.chan.algorithm import _source_hash, calculate_chan, definition, run_chan
from tvbt.storage.path_guard import PathGuard
from tvbt.testing.logging_proxy import logger


def _write_chan_dataset(root: Path, *, count: int = 25) -> dict[str, str]:
    """Create the minimum normalized dataset that algorithm.py expects.

    algorithm.py does not scan history files.  Callers must pass explicit
    data_root-relative `bars_path` and `meta_path` fields, so the test builds
    those files exactly as Go would.
    """
    dataset_dir = root / "normalized" / "TEST.CHAN.1m" / "revision"
    dataset_dir.mkdir(parents=True)
    levels = [0, 1, 2, 3, 4, 3, 2, 1]
    values = [levels[index % len(levels)] for index in range(count)]
    pq.write_table(
        pa.table(
            {
                "bar_index": pa.array(range(count), type=pa.int64()),
                "timestamp_utc": pa.array(
                    [1_700_000_000_000 + index * 60_000 for index in range(count)],
                    type=pa.int64(),
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
    return {
        "bars_path": "normalized/TEST.CHAN.1m/revision/bars.parquet",
        "meta_path": "normalized/TEST.CHAN.1m/revision/meta.json",
    }


def _payload(root: Path, *, output_path: str = "cache/chan/key") -> dict[str, object]:
    paths = _write_chan_dataset(root)
    spec = definition()
    return {
        "cache_key": "sha256:" + "4" * 64,
        "dataset": {
            "dataset_id": "TEST.CHAN.1m",
            "data_revision": "sha256:" + "1" * 64,
            **paths,
        },
        "algorithm": {
            key: spec[key] for key in ("kind", "algorithm_id", "algorithm_version", "source_hash")
        },
        "parameters": {"checkpoint_interval": 4},
        "calculation_mode": "causal_events",
        "output_path": output_path,
    }


def test_source_hash_is_stable_sha256_and_is_published_by_definition() -> None:
    """Use `_source_hash()` only as the implementation identity for cache keys.

    Expected: it is deterministic in one process, has the public `sha256:`
    format, and `definition()` publishes exactly the same value for Go callers.
    """
    first = _source_hash()
    second = _source_hash()
    logger.info(first)
    logger.info(second)
    assert first == second
    assert first.startswith("sha256:")
    assert len(first) == len("sha256:") + 64
    assert definition()["source_hash"] == first


def test_definition_documents_how_go_and_vue_should_call_chan() -> None:
    """Use `definition()` to discover the only valid algorithm identity.

    Expected: callers copy the identity fields into calculation payloads, use
    `causal_events`, and only configure the documented `checkpoint_interval`.
    """
    spec = definition()

    assert spec["kind"] == "chan"
    assert spec["algorithm_id"] == "chan_engineering"
    assert spec["input_schema"] == "bars.v1"
    assert spec["causal"] is True
    assert spec["parameter_schema"]["additionalProperties"] is False
    assert spec["parameter_schema"]["properties"]["checkpoint_interval"]["default"] == 1024
    assert {output["object_type"] for output in spec["outputs"]} == {
        "fractal",
        "bi",
        "segment",
        "zhongshu",
        "segment_zhongshu",
        "movement_state",
        "center_monitor",
        "divergence",
        "trade_point",
    }
    logger.info(spec["outputs"])


def test_run_chan_reads_declared_parquet_and_can_stop_at_a_prefix(tmp_path: Path) -> None:
    """Use `run_chan()` when a caller needs the in-memory engine state.

    Expected: it reads only the declared Parquet columns, starts from the first
    bar, stops after `last_bar_index`, and does not write cache files itself.
    """
    payload = _payload(tmp_path)
    guard = PathGuard(tmp_path)

    runtime, indices, checkpoints = run_chan(
        payload, guard, threading.Event(), last_bar_index=10, write_checkpoints=True
    )

    assert indices == list(range(11))
    assert [bar.bar_index for bar in runtime.raw_bars] == indices
    assert checkpoints.keys() == {3, 7}
    assert not (tmp_path / "cache").exists()


def test_run_chan_rejects_payloads_not_matching_the_current_definition(tmp_path: Path) -> None:
    """Use the exact identity from `definition()`; stale algorithm metadata is invalid.

    Expected: changing version/hash/kind/id fails before any result is trusted,
    preventing old Chan caches from being reused with new semantics.
    """
    payload = _payload(tmp_path)
    algorithm = dict(payload["algorithm"])  # type: ignore[arg-type]
    algorithm["source_hash"] = "sha256:" + "0" * 64
    payload["algorithm"] = algorithm

    with pytest.raises(ValueError, match="algorithm source_hash"):
        run_chan(payload, PathGuard(tmp_path), threading.Event())


def test_run_chan_honors_cancellation_before_finishing(tmp_path: Path) -> None:
    """Use the `cancelled` event to abort long calculations cooperatively.

    Expected: a set event raises `InterruptedError` and returns no partial
    runtime to callers.
    """
    payload = _payload(tmp_path, output_path="cache/chan/cancelled")
    cancelled = threading.Event()
    cancelled.set()

    with pytest.raises(InterruptedError, match="calculation cancelled"):
        run_chan(payload, PathGuard(tmp_path), cancelled)


def test_calculate_chan_writes_the_causal_cache_contract(tmp_path: Path) -> None:
    """Use `calculate_chan()` for the normal Go calculation task path.

    Expected: it returns a data_root-relative cache directory, writes `_SUCCESS`
    last, emits all contract Parquet files, and records manifest coverage and
    checkpoint metadata.
    """
    payload = _payload(tmp_path, output_path="cache/chan/full")
    result_ref = calculate_chan(payload, PathGuard(tmp_path), threading.Event())
    output = tmp_path / result_ref

    assert result_ref == "cache/chan/full"
    assert (output / "_SUCCESS").is_file()
    for name in (
        "fractals",
        "bi",
        "segments",
        "zhongshu",
        "segment_zhongshu",
        "movement_states",
        "center_monitors",
        "divergences",
        "trade_points",
        "events",
    ):
        assert (output / f"{name}.parquet").is_file()
    assert pq.read_table(output / "fractals.parquet").num_rows > 0
    assert pq.read_table(output / "events.parquet").num_rows > 0
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["algorithm"] == payload["algorithm"]
    assert manifest["coverage"] == {"bar_count": 25, "first_bar_index": 0, "last_bar_index": 24}
    assert manifest["checkpoint"]["interval_bars"] == 4
    assert manifest["checkpoint"]["count"] == 6


def test_calculate_chan_writes_structured_runtime_logs(tmp_path: Path) -> None:
    """Use the process runtime logger to observe Chan algorithm milestones.

    Expected: callers use the process runtime logger configured by
    `logging_proxy`, and Chan calculation writes fixed-text events to screen.
    """
    payload = _payload(tmp_path, output_path="cache/chan/logged")
    payload["job_id"] = "job-chan-log"
    payload["trace_id"] = "trace-chan-log"
    calculate_chan(payload, PathGuard(tmp_path), threading.Event())
    logger.info("Chan runtime log smoke completed")

