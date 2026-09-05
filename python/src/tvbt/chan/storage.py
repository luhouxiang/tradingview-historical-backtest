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

# 以下 schema 是 Go 范围查询和 Vue 批量绘图的持久化契约。字段保持 snake_case，
# 价格使用定点整数，时间使用 UTC 毫秒；新增/删除字段必须先改 contracts 和测试。

# 顺序、方向感知的包含处理结果。`source_bar_indices` 保存完整原始成员，最新对象
# 在包含合并或封存时通过相同 object_id 增加 revision。
PROCESSED_BAR_SCHEMA = pa.schema(
    [
        ("object_id", pa.string()),
        ("normalized_index", pa.int64()),
        ("start_bar_index", pa.int64()),
        ("start_time", pa.int64()),
        ("end_bar_index", pa.int64()),
        ("end_time", pa.int64()),
        ("open_i64", pa.int64()),
        ("high_i64", pa.int64()),
        ("low_i64", pa.int64()),
        ("close_i64", pa.int64()),
        ("high_source_bar_index", pa.int64()),
        ("low_source_bar_index", pa.int64()),
        ("direction", pa.string()),
        ("source_bar_indices", pa.list_(pa.int64())),
        ("status", pa.string()),
        ("sealed_at_bar_index", pa.int64()),
        ("catalog_event", pa.string()),
        ("known_at_bar_index", pa.int64()),
        ("object_revision", pa.int64()),
    ]
)
# 三独立 K 线确认的顶/底分型。
FRACTAL_SCHEMA = pa.schema(
    [
        ("object_id", pa.string()),
        ("bar_index", pa.int64()),
        ("time", pa.int64()),
        ("price_i64", pa.int64()),
        ("zone_low_i64", pa.int64()),
        ("zone_high_i64", pa.int64()),
        ("extreme_source_bar_index", pa.int64()),
        ("fractal_type", pa.string()),
        ("status", pa.string()),
        ("invalidation_reason", pa.string()),
        ("aux_strength", pa.string()),
        ("strength_reason", pa.string()),
        ("body_i64", pa.int64()),
        ("upper_shadow_i64", pa.int64()),
        ("lower_shadow_i64", pa.int64()),
        ("range_i64", pa.int64()),
        ("close_position_milli", pa.int64()),
        ("feature_profile", pa.string()),
        ("catalog_algorithm_id", pa.string()),
        ("strength_semantic_namespace", pa.string()),
        ("standard_signal", pa.bool_()),
        ("execution_allowed", pa.bool_()),
        ("confirmed", pa.bool_()),
        ("confirmed_at_bar_index", pa.int64()),
        ("known_at_bar_index", pa.int64()),
        ("object_revision", pa.int64()),
    ]
)
# 线性对象 schema，当前复用于“笔”和“线段”。结构端点供绘图；独立 range 字段
# 是上层结构计算的权威价格区间，不保存屏幕像素或当前数组下标。
LINE_SCHEMA = pa.schema(
    [
        ("object_id", pa.string()),
        ("start_bar_index", pa.int64()),
        ("start_time", pa.int64()),
        ("start_price_i64", pa.int64()),
        ("start_extreme_source_bar_index", pa.int64()),
        ("end_bar_index", pa.int64()),
        ("end_time", pa.int64()),
        ("end_price_i64", pa.int64()),
        ("end_extreme_source_bar_index", pa.int64()),
        ("range_low_i64", pa.int64()),
        ("range_high_i64", pa.int64()),
        ("range_low_source_bar_index", pa.int64()),
        ("range_high_source_bar_index", pa.int64()),
        ("range_profile", pa.string()),
        ("direction", pa.string()),
        ("status", pa.string()),
        ("invalidation_reason", pa.string()),
        ("catalog_algorithm_id", pa.string()),
        ("confirmed", pa.bool_()),
        ("confirmed_at_bar_index", pa.int64()),
        ("known_at_bar_index", pa.int64()),
        ("object_revision", pa.int64()),
    ]
)
# 笔在线四状态（加初始等待态）的当前快照；完整转换历史保存在 events.parquet。
BI_STATE_SCHEMA = pa.schema(
    [
        ("object_id", pa.string()),
        ("bar_index", pa.int64()),
        ("time", pa.int64()),
        ("price_i64", pa.int64()),
        ("state", pa.string()),
        ("direction", pa.string()),
        ("anchor_fractal_id", pa.string()),
        ("candidate_object_id", pa.string()),
        ("trigger", pa.string()),
        ("catalog_algorithm_id", pa.string()),
        ("known_at_bar_index", pa.int64()),
        ("object_revision", pa.int64()),
    ]
)
# 中枢 schema，复用于笔中枢和标准线段中枢。`ZD/ZG` 是冻结核心，
# `DD/GG` 是参与组件完整振荡包络，`Z` 是核心中轴。
ZHONGSHU_SCHEMA = pa.schema(
    [
        ("object_id", pa.string()),
        ("start_bar_index", pa.int64()),
        ("start_time", pa.int64()),
        ("end_bar_index", pa.int64()),
        ("end_time", pa.int64()),
        ("zg_i64", pa.int64()),
        ("zd_i64", pa.int64()),
        ("gg_i64", pa.int64()),
        ("dd_i64", pa.int64()),
        ("z_i64", pa.int64()),
        ("analysis_level", pa.string()),
        ("component_kind", pa.string()),
        ("component_count", pa.int64()),
        ("confirmed", pa.bool_()),
        ("confirmed_at_bar_index", pa.int64()),
        ("status", pa.string()),
        ("leave_direction", pa.string()),
        ("known_at_bar_index", pa.int64()),
        ("object_revision", pa.int64()),
    ]
)
# 走势状态对象，用于表达盘整、中枢震荡和中枢迁移，不直接等同于交易信号。
MOVEMENT_STATE_SCHEMA = pa.schema(
    [
        ("object_id", pa.string()),
        ("start_bar_index", pa.int64()),
        ("start_time", pa.int64()),
        ("end_bar_index", pa.int64()),
        ("end_time", pa.int64()),
        ("price_i64", pa.int64()),
        ("state_type", pa.string()),
        ("direction", pa.string()),
        ("analysis_level", pa.string()),
        ("reference_object_id", pa.string()),
        ("confirmed", pa.bool_()),
        ("confirmed_at_bar_index", pa.int64()),
        ("known_at_bar_index", pa.int64()),
        ("object_revision", pa.int64()),
    ]
)
LEVEL_CENTER_SCHEMA = pa.schema(
    [
        ("object_id", pa.string()),
        ("level_id", pa.string()),
        ("parent_level_id", pa.string()),
        ("start_bar_index", pa.int64()),
        ("start_time", pa.int64()),
        ("end_bar_index", pa.int64()),
        ("end_time", pa.int64()),
        ("zd_i64", pa.int64()),
        ("zg_i64", pa.int64()),
        ("dd_i64", pa.int64()),
        ("gg_i64", pa.int64()),
        ("component_kind", pa.string()),
        ("component_object_ids", pa.list_(pa.string())),
        ("source_center_ids", pa.list_(pa.string())),
        ("status", pa.string()),
        ("promotion_reason", pa.string()),
        ("promoted_from_center_id", pa.string()),
        ("catalog_event", pa.string()),
        ("catalog_algorithm_id", pa.string()),
        ("confirmed", pa.bool_()),
        ("confirmed_at_bar_index", pa.int64()),
        ("known_at_bar_index", pa.int64()),
        ("object_revision", pa.int64()),
    ]
)
LEVEL_MOVEMENT_SCHEMA = pa.schema(
    [
        ("object_id", pa.string()),
        ("level_id", pa.string()),
        ("start_bar_index", pa.int64()),
        ("start_time", pa.int64()),
        ("end_bar_index", pa.int64()),
        ("end_time", pa.int64()),
        ("low_i64", pa.int64()),
        ("high_i64", pa.int64()),
        ("component_center_ids", pa.list_(pa.string())),
        ("classification", pa.string()),
        ("direction", pa.string()),
        ("status", pa.string()),
        ("previous_classification", pa.string()),
        ("reclassification_reason", pa.string()),
        ("parent_center_candidate_id", pa.string()),
        ("catalog_event", pa.string()),
        ("catalog_algorithm_id", pa.string()),
        ("confirmed", pa.bool_()),
        ("confirmed_at_bar_index", pa.int64()),
        ("known_at_bar_index", pa.int64()),
        ("object_revision", pa.int64()),
    ]
)
# Z/Zn 监视对象：逐组件记录相对中枢中轴的位置、强弱和越界/楔形预警。
CENTER_MONITOR_SCHEMA = pa.schema(
    [
        ("object_id", pa.string()),
        ("bar_index", pa.int64()),
        ("time", pa.int64()),
        ("z_i64", pa.int64()),
        ("zn_i64", pa.int64()),
        ("z_twice_i64", pa.int64()),
        ("zn_twice_i64", pa.int64()),
        ("core_low_i64", pa.int64()),
        ("core_high_i64", pa.int64()),
        ("range_high_i64", pa.int64()),
        ("range_low_i64", pa.int64()),
        ("component_ordinal", pa.int64()),
        ("component_direction", pa.string()),
        ("relative_position", pa.string()),
        ("oscillation_bias", pa.string()),
        ("breakout_warning", pa.string()),
        ("catalog_algorithm_id", pa.string()),
        ("semantic_namespace", pa.string()),
        ("evidence_level", pa.string()),
        ("level_mapping_profile", pa.string()),
        ("standard_signal", pa.bool_()),
        ("execution_allowed", pa.bool_()),
        ("confirms_third_point", pa.bool_()),
        ("analysis_level", pa.string()),
        ("reference_object_id", pa.string()),
        ("confirmed", pa.bool_()),
        ("confirmed_at_bar_index", pa.int64()),
        ("known_at_bar_index", pa.int64()),
        ("object_revision", pa.int64()),
    ]
)
# 背驰和买卖点共用信号 schema；买卖点的 MACD 面积字段为空，背驰会填充。
SIGNAL_SCHEMA = pa.schema(
    [
        ("object_id", pa.string()),
        ("bar_index", pa.int64()),
        ("time", pa.int64()),
        ("price_i64", pa.int64()),
        ("signal_type", pa.string()),
        ("divergence_kind", pa.string()),
        ("signal_class", pa.string()),
        ("strength", pa.string()),
        ("reference_object_id", pa.string()),
        ("macd_area_reference", pa.float64()),
        ("macd_area_current", pa.float64()),
        ("status", pa.string()),
        ("invalidation_reason", pa.string()),
        ("level_id", pa.string()),
        ("lower_level_turn_object_id", pa.string()),
        ("catalog_event", pa.string()),
        ("catalog_algorithm_id", pa.string()),
        ("evidence_profile", pa.string()),
        ("comparison_reference_object_id", pa.string()),
        ("comparison_current_object_id", pa.string()),
        ("comparison_rule", pa.string()),
        ("new_extreme_satisfied", pa.bool_()),
        ("departure_object_id", pa.string()),
        ("return_object_id", pa.string()),
        ("return_ordinal", pa.int64()),
        ("boundary_profile", pa.string()),
        ("boundary_relation", pa.string()),
        ("return_depth_to_core_i64", pa.int64()),
        ("return_depth_to_outer_i64", pa.int64()),
        ("follow_through_object_id", pa.string()),
        ("follow_through_status", pa.string()),
        ("confirmation_latency_bars", pa.int64()),
        ("reference_center_ordinal", pa.int64()),
        ("older_center_count", pa.int64()),
        ("center_chain_profile", pa.string()),
        ("confirmed", pa.bool_()),
        ("confirmed_at_bar_index", pa.int64()),
        ("known_at_bar_index", pa.int64()),
        ("object_revision", pa.int64()),
    ]
)
# 因果事件流 schema。回放只按 `known_at_bar_index` 推进，不读取终态对象倒灌。
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
    """一次缠论计算的完整内存结果。

    计数字段用于 manifest 审计；列表字段分别写入同名 Parquet 文件；
    `checkpoints` 的 key 是已处理到的 `bar_index`，value 是压缩前的检查点字节。
    """

    # 输入覆盖范围。
    bar_count: int
    first_bar_index: int
    last_bar_index: int
    # 包含关系处理后的独立 K 线数量。
    merged_bar_count: int
    # 语义对象终态快照。
    processed_bars: list[dict[str, Any]] = field(default_factory=list)
    fractals: list[dict[str, Any]] = field(default_factory=list)
    bi: list[dict[str, Any]] = field(default_factory=list)
    bi_states: list[dict[str, Any]] = field(default_factory=list)
    segments: list[dict[str, Any]] = field(default_factory=list)
    zhongshu: list[dict[str, Any]] = field(default_factory=list)
    segment_zhongshu: list[dict[str, Any]] = field(default_factory=list)
    level_centers: list[dict[str, Any]] = field(default_factory=list)
    level_movements: list[dict[str, Any]] = field(default_factory=list)
    movement_states: list[dict[str, Any]] = field(default_factory=list)
    center_monitors: list[dict[str, Any]] = field(default_factory=list)
    divergences: list[dict[str, Any]] = field(default_factory=list)
    trade_points: list[dict[str, Any]] = field(default_factory=list)
    # 按发生顺序记录 upsert/delete，供回放和审计使用。
    events: list[dict[str, Any]] = field(default_factory=list)
    # 可选检查点，长任务恢复和一致性测试使用。
    checkpoints: dict[int, bytes] = field(default_factory=dict)


def _sha256(path: Path) -> str:
    """计算单个输出文件哈希，写入 manifest 供 Go 校验。"""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def write_chan_cache(payload: dict[str, Any], guard: PathGuard, result: ChanResult) -> str:
    """原子写入缠论缓存目录。

    写入顺序是：临时目录、所有 Parquet、检查点、manifest、`_SUCCESS`、同卷
    `os.replace`。已有完整缓存直接复用；已有但未提交成功的目录视为错误，避免
    静默覆盖不完整结果。
    """
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
            "processed_bars": (result.processed_bars, PROCESSED_BAR_SCHEMA),
            "fractals": (result.fractals, FRACTAL_SCHEMA),
            "bi": (result.bi, LINE_SCHEMA),
            "bi_states": (result.bi_states, BI_STATE_SCHEMA),
            "segments": (result.segments, LINE_SCHEMA),
            "zhongshu": (result.zhongshu, ZHONGSHU_SCHEMA),
            "segment_zhongshu": (result.segment_zhongshu, ZHONGSHU_SCHEMA),
            "level_centers": (result.level_centers, LEVEL_CENTER_SCHEMA),
            "level_movements": (result.level_movements, LEVEL_MOVEMENT_SCHEMA),
            "movement_states": (result.movement_states, MOVEMENT_STATE_SCHEMA),
            "center_monitors": (result.center_monitors, CENTER_MONITOR_SCHEMA),
            "divergences": (result.divergences, SIGNAL_SCHEMA),
            "trade_points": (result.trade_points, SIGNAL_SCHEMA),
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
            "schema_version": 4,
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
                "processed_bars": len(result.processed_bars),
                "fractals": len(result.fractals),
                "bi": len(result.bi),
                "bi_states": len(result.bi_states),
                "segments": len(result.segments),
                "zhongshu": len(result.zhongshu),
                "segment_zhongshu": len(result.segment_zhongshu),
                "level_centers": len(result.level_centers),
                "level_movements": len(result.level_movements),
                "movement_states": len(result.movement_states),
                "center_monitors": len(result.center_monitors),
                "divergences": len(result.divergences),
                "trade_points": len(result.trade_points),
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
