from __future__ import annotations

import json
from pathlib import Path

import pyarrow.parquet as pq

from tvbt.chan import dump_checkpoint
from tvbt.chan.storage import ChanResult, write_chan_cache
from tvbt.storage.path_guard import PathGuard


def payload() -> dict[str, object]:
    """构造写缓存测试使用的最小缠论任务载荷。"""
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
        "parameters": {
            "min_fractal_gap": 5,
            "checkpoint_interval": 4,
            "center_boundary_profile": "local_center_boundary_v1",
        },
        "calculation_mode": "causal_events",
        "output_path": "cache/chan/example",
    }


def test_chan_cache_writes_typed_tables_checkpoints_and_success_last(tmp_path: Path) -> None:
    """测试缠论缓存是否写出类型化表、检查点和完成标记。

    预期:缓存目录包含 `_SUCCESS`、检查点文件、全部 Parquet 表和 manifest;
    `bi` 与 `segments` 使用相同线性对象 Schema,manifest 记录文件路径、数量、
    哈希和最新检查点位置。
    """
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
                "zone_low_i64": 100,
                "zone_high_i64": 110,
                "extreme_source_bar_index": 2,
                "fractal_type": "top",
                "status": "confirmed",
                "invalidation_reason": None,
                "aux_strength": "unclassified",
                "strength_reason": "lesson82_strong_reversal_condition_not_met",
                "body_i64": 5,
                "upper_shadow_i64": 0,
                "lower_shadow_i64": 5,
                "range_i64": 10,
                "close_position_milli": 500,
                "feature_profile": "processed_bar_ohlc_v1",
                "catalog_algorithm_id": "ALG-GEO-002",
                "strength_semantic_namespace": "auxiliary",
                "standard_signal": False,
                "execution_allowed": False,
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
    fractals = pq.read_table(directory / "fractals.parquet").to_pylist()
    assert len(fractals) == 1
    assert (fractals[0]["zone_low_i64"], fractals[0]["zone_high_i64"]) == (100, 110)
    assert pq.read_table(directory / "bi.parquet").schema.names == [
        "object_id",
        "start_bar_index",
        "start_time",
        "start_price_i64",
        "start_extreme_source_bar_index",
        "end_bar_index",
        "end_time",
        "end_price_i64",
        "end_extreme_source_bar_index",
        "range_low_i64",
        "range_high_i64",
        "range_low_source_bar_index",
        "range_high_source_bar_index",
        "range_profile",
        "direction",
        "status",
        "invalidation_reason",
        "catalog_algorithm_id",
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
    assert manifest["schema_version"] == 12
    assert manifest["counts"]["events"] == 1
    assert manifest["files"]["processed_bars"]["path"] == "processed_bars.parquet"
    assert manifest["files"]["bi_states"]["path"] == "bi_states.parquet"
    assert manifest["counts"]["segments"] == 0
    assert manifest["files"]["segments"]["path"] == "segments.parquet"
    assert manifest["counts"]["local_centers"] == 0
    assert manifest["files"]["center_connections"]["path"] == "center_connections.parquet"
    assert manifest["files"]["center_audit_events"]["path"] == "center_audit_events.parquet"
    assert manifest["files"]["movement_states"]["path"] == "movement_states.parquet"
    assert manifest["files"]["center_monitors"]["path"] == "center_monitors.parquet"
    assert manifest["files"]["divergences"]["path"] == "divergences.parquet"
    assert manifest["files"]["trade_points"]["path"] == "trade_points.parquet"
    assert "status" in pq.read_table(directory / "trade_points.parquet").schema.names
    assert (
        "lower_level_turn_object_id"
        in pq.read_table(directory / "trade_points.parquet").schema.names
    )
    signal_columns = pq.read_table(directory / "trade_points.parquet").schema.names
    assert "comparison_reference_object_id" in signal_columns
    assert "return_depth_to_core_i64" in signal_columns
    assert "confirmation_latency_bars" in signal_columns
    assert "macd_extreme_relation" in signal_columns
    assert manifest["checkpoint"]["last_bar_index"] == 4
    assert all(value["sha256"].startswith("sha256:") for value in manifest["files"].values())


def test_completed_chan_cache_is_reused_without_overwrite(tmp_path: Path) -> None:
    """测试已完成缠论缓存是否复用且不覆盖。

    预期:同一输出路径已有 `_SUCCESS` 时再次写入直接复用原目录,manifest
    内容保持不变,防止完成缓存被重复任务覆盖。
    """
    guard = PathGuard(tmp_path)
    result = ChanResult(0, 0, 0, 0)
    first = write_chan_cache(payload(), guard, result)
    marker = tmp_path / first / "manifest.json"
    before = marker.read_bytes()
    second = write_chan_cache(payload(), guard, result)
    assert second == first
    assert marker.read_bytes() == before


def test_divergence_structure_and_macd_evidence_survive_cache_roundtrip(
    tmp_path: Path,
) -> None:
    """The signal evidence used by the object tree must not vanish in Parquet."""
    guard = PathGuard(tmp_path)
    result = ChanResult(
        bar_count=6,
        first_bar_index=0,
        last_bar_index=5,
        merged_bar_count=6,
        divergences=[
            {
                "object_id": "divergence-1",
                "bar_index": 4,
                "time": 1_200_000,
                "price_i64": 123,
                "signal_type": "top_divergence",
                "divergence_kind": "trend",
                "divergence_profile": "segment_trend_candidate",
                "formation_dir": "DOWN",
                "relative_dir": "UP",
                "a_object_id": "segment-a",
                "b_object_id": "segment-b",
                "a_center_id": "center-a",
                "b_center_id": "center-b",
                "macd_area_reference": 20.0,
                "macd_area_current": 10.0,
                "macd_area_ratio": 0.5,
                "macd_diff_reference_extreme": 5.0,
                "macd_diff_current_extreme": 3.0,
                "macd_dea_reference_extreme": 4.0,
                "macd_dea_current_extreme": 2.0,
                "macd_parameter_profile": "macd_12_26_9_histogram_x2",
                "c_contains_type3": False,
                "c_meets_sublevel": False,
                "status": "candidate",
                "known_at_bar_index": 5,
                "object_revision": 1,
            }
        ],
    )
    directory = tmp_path / write_chan_cache(payload(), guard, result)
    stored = pq.read_table(directory / "divergences.parquet").to_pylist()
    assert len(stored) == 1
    for field in (
        "divergence_profile",
        "formation_dir",
        "relative_dir",
        "a_object_id",
        "b_object_id",
        "a_center_id",
        "b_center_id",
        "macd_area_ratio",
        "macd_diff_reference_extreme",
        "macd_diff_current_extreme",
        "macd_dea_reference_extreme",
        "macd_dea_current_extreme",
        "macd_parameter_profile",
        "c_contains_type3",
        "c_meets_sublevel",
    ):
        assert stored[0][field] == result.divergences[0][field]
