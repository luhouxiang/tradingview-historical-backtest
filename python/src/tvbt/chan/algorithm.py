from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

from tvbt.chan.checkpoint import dump_checkpoint
from tvbt.chan.engine import ChanEngine, ChanParameters, RawBar
from tvbt.chan.storage import ChanResult, write_chan_cache
from tvbt.logging_proxy import logger
from tvbt.storage.path_guard import PathGuard

"""缠论计算入口。

本文件只负责把 Go 传入的任务载荷转换为 Python 引擎调用：

- 校验算法定义，保证缓存键中的版本和源码哈希与当前实现一致。
- 从 Go 指定的标准化 Parquet 读取 K 线，不自行扫描磁盘目录。
- 逐根 K 线推进 `ChanEngine`，生成因果事件、对象快照和可选检查点。
- 将结果交给 `storage.py` 原子写入 `cache/chan/<cache_key>/`。

真正的分型、笔、段、中枢、背驰和买卖点算法在 `engine.py`、
`reference.py` 和 `signals.py` 中实现。
"""


def _source_hash() -> str:
    """计算缠论实现源码哈希，参与算法定义与缓存键。

    这里显式列出会影响缠论语义或输出格式的 Python 文件。只要这些文件
    内容变化，Go 侧传入的旧 `source_hash` 就无法通过校验，从而避免复用
    语义不一致的旧缓存。
    """
    digest = hashlib.sha256()
    directory = Path(__file__).parent
    for name in (
        "algorithm.py",
        "checkpoint.py",
        "engine.py",
        "events.py",
        "reference.py",
        "signals.py",
        "storage.py",
    ):
        digest.update(name.encode())
        digest.update((directory / name).read_bytes())
    return "sha256:" + digest.hexdigest()


def definition() -> dict[str, Any]:
    """发布给 Go/Vue 的缠论算法定义。

    字段语义：

    - `kind/algorithm_id/algorithm_version/source_hash` 共同确定不可变算法身份。
    - `parameter_schema` 当前只开放检查点间隔，算法规则本身不暴露可调阈值。
    - `outputs` 定义前端对象树和图层可见性用到的语义对象类别。
    - `causal=True` 表示输出必须通过 `known_at_bar_index` 约束回放可见时间。
    """
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
                ("zhongshu", "笔中枢"),
                ("segment_zhongshu", "标准线段中枢"),
                ("movement_state", "走势状态"),
                ("center_monitor", "Z/Zn 中枢监视"),
                ("divergence", "背驰"),
                ("trade_point", "买卖点"),
            )
        ],
        "warmup": {"kind": "formula", "expression": "three independent bars; bi requires five"},
        "causal": True,
    }


def calculate_chan(payload: dict[str, Any], guard: PathGuard, cancelled: threading.Event) -> str:
    """执行完整缠论计算并写入正式缓存目录。

    `payload` 由 Go 侧构造，所有路径都必须通过 `PathGuard` 限制在
    `data_root` 内。函数返回的是 Go 可保存的 data_root 相对路径。
    """
    started = time.perf_counter()
    context = _log_context(payload)
    logger.info(
        "calculation.started",
        "Chan calculation started",
        {**context, "calculation_mode": payload.get("calculation_mode")},
    )
    try:
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
            segment_zhongshu=rows["segment_zhongshu"],
            movement_states=rows["movement_states"],
            center_monitors=rows["center_monitors"],
            divergences=rows["divergences"],
            trade_points=rows["trade_points"],
            events=[event.row() for event in runtime.emitter.events],
            checkpoints=checkpoints,
        )
        result_ref = write_chan_cache(payload, guard, result)
        logger.info(
            "calculation.completed",
            "Chan calculation completed",
            {
                **context,
                "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                "output_path": result_ref,
                "bar_count": result.bar_count,
                "merged_bar_count": result.merged_bar_count,
                "fractals": len(result.fractals),
                "bi": len(result.bi),
                "segments": len(result.segments),
                "zhongshu": len(result.zhongshu),
                "segment_zhongshu": len(result.segment_zhongshu),
                "divergences": len(result.divergences),
                "trade_points": len(result.trade_points),
                "events": len(result.events),
                "checkpoint_count": len(result.checkpoints),
            },
        )
        return result_ref
    except InterruptedError:
        logger.warning(
            "calculation.cancelled",
            "Chan calculation cancelled",
            {
                **context,
                "duration_ms": round((time.perf_counter() - started) * 1000, 3),
            },
        )
        raise
    except Exception as exc:
        logger.error(
            "calculation.failed",
            "Chan calculation failed",
            {
                **context,
                "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        )
        raise


def run_chan(
    payload: dict[str, Any],
    guard: PathGuard,
    cancelled: threading.Event,
    *,
    last_bar_index: int | None = None,
    write_checkpoints: bool = False,
) -> tuple[ChanEngine, list[int], dict[int, bytes]]:
    """运行缠论引擎并返回内存态结果。

    `last_bar_index` 用于回放/测试中的前缀运行；它不会改变算法起点，仍然
    从数据集第一根 K 线开始逐根累计，避免固定预热窗口破坏缠论前缀不变性。
    """
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
            logger.warning(
                "algorithm.rejected",
                "Chan algorithm identity rejected",
                {**_log_context(payload), "mismatch_field": key},
            )
            raise ValueError(f"algorithm {key} does not match engine definition")
    bars_path = guard.resolve(str(dataset.get("bars_path", "")))
    meta_path = guard.resolve(str(dataset.get("meta_path", "")))
    json.loads(meta_path.read_text(encoding="utf-8"))
    logger.debug(
        "algorithm.loaded",
        "Chan algorithm payload accepted",
        {
            **_log_context(payload),
            "checkpoint_interval": int(parameters["checkpoint_interval"]),
            "last_bar_index": last_bar_index,
        },
    )
    table = pq.read_table(
        bars_path,
        columns=["bar_index", "timestamp_utc", "high_i64", "low_i64", "close_i64"],
    ).to_pydict()
    logger.debug(
        "data.batch.transferred",
        "Chan input Parquet loaded",
        {
            **_log_context(payload),
            "bar_count": len(table["bar_index"]),
            "input_columns": ["bar_index", "timestamp_utc", "high_i64", "low_i64", "close_i64"],
        },
    )
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
            logger.debug(
                "checkpoint.saved",
                "Chan checkpoint saved in memory",
                {**_log_context(payload), "bar_index": raw_index, "sequence": len(checkpoints)},
            )
    if cancelled.is_set():
        raise InterruptedError("calculation cancelled")
    logger.debug(
        "algorithm.completed",
        "Chan engine run completed",
        {
            **_log_context(payload),
            "bar_count": len(indices),
            "first_bar_index": indices[0] if indices else 0,
            "last_bar_index": indices[-1] if indices else 0,
            "checkpoint_count": len(checkpoints),
        },
    )
    return runtime, indices, checkpoints


def _log_context(payload: dict[str, Any]) -> dict[str, Any]:
    """Extract safe, small logging context fields from a calculation payload."""
    dataset = payload.get("dataset")
    algorithm = payload.get("algorithm")
    context: dict[str, Any] = {}
    for key in ("request_id", "trace_id", "job_id", "cache_key"):
        value = payload.get(key)
        if isinstance(value, str):
            context[key] = value
    if isinstance(dataset, dict):
        for key in ("dataset_id", "data_revision"):
            value = dataset.get(key)
            if isinstance(value, str):
                context[key] = value
    if isinstance(algorithm, dict):
        for key in ("algorithm_id", "algorithm_version"):
            value = algorithm.get(key)
            if isinstance(value, str):
                context[key] = value
    return context
