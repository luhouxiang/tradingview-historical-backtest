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

# 三独立 K 线确认的顶/底分型。
FRACTAL_SCHEMA = pa.schema(
    [
        ("object_id", pa.string()),
        ("bar_index", pa.int64()),
        ("time", pa.int64()),
        ("price_i64", pa.int64()),
        ("extreme_source_bar_index", pa.int64()),
        ("fractal_type", pa.string()),
        ("confirmed", pa.bool_()),
        ("confirmed_at_bar_index", pa.int64()),
        ("known_at_bar_index", pa.int64()),
        ("object_revision", pa.int64()),
    ]
)
# 线性对象 schema，当前复用于“笔”和“线段”。二者都只保存时间/价格锚点，
# 不保存屏幕像素或数组下标。
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
        ("direction", pa.string()),
        ("confirmed", pa.bool_()),
        ("confirmed_at_bar_index", pa.int64()),
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
# Z/Zn 监视对象：逐组件记录相对中枢中轴的位置、强弱和迁移预警。
CENTER_MONITOR_SCHEMA = pa.schema(
    [
        ("object_id", pa.string()),
        ("bar_index", pa.int64()),
        ("time", pa.int64()),
        ("z_i64", pa.int64()),
        ("zn_i64", pa.int64()),
        ("range_high_i64", pa.int64()),
        ("range_low_i64", pa.int64()),
        ("component_direction", pa.string()),
        ("relative_position", pa.string()),
        ("strength", pa.string()),
        ("migration_warning", pa.string()),
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
    fractals: list[dict[str, Any]] = field(default_factory=list)
    bi: list[dict[str, Any]] = field(default_factory=list)
    segments: list[dict[str, Any]] = field(default_factory=list)
    zhongshu: list[dict[str, Any]] = field(default_factory=list)
    segment_zhongshu: list[dict[str, Any]] = field(default_factory=list)
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
            "fractals": (result.fractals, FRACTAL_SCHEMA),
            "bi": (result.bi, LINE_SCHEMA),
            "segments": (result.segments, LINE_SCHEMA),
            "zhongshu": (result.zhongshu, ZHONGSHU_SCHEMA),
            "segment_zhongshu": (result.segment_zhongshu, ZHONGSHU_SCHEMA),
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
                "segment_zhongshu": len(result.segment_zhongshu),
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
