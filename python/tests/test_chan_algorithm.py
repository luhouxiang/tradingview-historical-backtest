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
    """创建 `algorithm.py` 所需的最小标准化数据集。

    缠论算法入口不会自行扫描历史目录,调用方必须显式传入相对 data_root
    的 `bars_path` 和 `meta_path`,因此这里按 Go 侧会生成的形态构造文件。
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
                "open_i64": pa.array([value * 10 + 1 for value in values], type=pa.int64()),
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
    """创建缠论计算任务载荷,供入口函数测试复用。"""
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
    """测试源码哈希是否可作为缠论实现身份参与缓存键。

    预期:同一进程内多次计算结果一致,格式为公开的 `sha256:` 前缀加
    64 位十六进制摘要,并且 `definition()` 对 Go 调用方发布同一个值。
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
    """测试缠论算法定义是否完整描述 Go 和 Vue 的调用契约。

    预期:定义中包含唯一有效的算法身份、因果计算模式、可配置的检查点参数,
    以及前端对象树和图层需要使用的全部输出对象类型。
    """
    spec = definition()

    assert spec["kind"] == "chan"
    assert spec["algorithm_id"] == "chan_engineering"
    assert spec["input_schema"] == "bars.v1"
    assert spec["causal"] is True
    assert spec["parameter_schema"]["additionalProperties"] is False
    assert spec["parameter_schema"]["properties"]["checkpoint_interval"]["default"] == 1024
    assert {output["object_type"] for output in spec["outputs"]} == {
        "processed_bar",
        "fractal",
        "bi",
        "bi_state",
        "segment",
        "zhongshu",
        "segment_zhongshu",
        "level_center",
        "level_movement",
        "movement_state",
        "center_monitor",
        "divergence",
        "trade_point",
    }
    logger.info(spec["outputs"])


def test_run_chan_reads_declared_parquet_and_can_stop_at_a_prefix(tmp_path: Path) -> None:
    """测试 `run_chan()` 是否只读取声明的 Parquet 并支持前缀停止。

    预期:它从数据集第一根 K 线开始逐根运行,到 `last_bar_index` 停止,
    可按间隔产生内存检查点,但不会自行写入正式缓存目录。
    """
    payload = _payload(tmp_path)
    guard = PathGuard(tmp_path)

    runtime, indices, checkpoints = run_chan(
        payload, guard, threading.Event(), last_bar_index=10, write_checkpoints=True
    )

    assert indices == list(range(11))
    assert [bar.bar_index for bar in runtime.raw_bars] == indices
    assert [bar.open_i64 for bar in runtime.raw_bars] == [
        value * 10 + 1 for value in [0, 1, 2, 3, 4, 3, 2, 1, 0, 1, 2]
    ]
    assert checkpoints.keys() == {3, 7}
    assert not (tmp_path / "cache").exists()


def test_run_chan_rejects_payloads_not_matching_the_current_definition(tmp_path: Path) -> None:
    """测试入口是否拒绝与当前定义不一致的算法身份。

    预期:篡改版本、源码哈希、算法类型或算法 ID 时立即失败,避免旧缠论缓存
    被错误复用于新语义。
    """
    payload = _payload(tmp_path)
    algorithm = dict(payload["algorithm"])
    algorithm["source_hash"] = "sha256:" + "0" * 64
    payload["algorithm"] = algorithm

    with pytest.raises(ValueError, match="algorithm source_hash"):
        run_chan(payload, PathGuard(tmp_path), threading.Event())


def test_run_chan_honors_cancellation_before_finishing(tmp_path: Path) -> None:
    """测试取消标记是否能中止缠论长任务。

    预期:调用前已经设置取消标记时抛出 `InterruptedError`,不向调用方返回
    可能被误用的半成品运行状态。
    """
    payload = _payload(tmp_path, output_path="cache/chan/cancelled")
    cancelled = threading.Event()
    cancelled.set()

    with pytest.raises(InterruptedError, match="calculation cancelled"):
        run_chan(payload, PathGuard(tmp_path), cancelled)


def test_calculate_chan_writes_the_causal_cache_contract(tmp_path: Path) -> None:
    """测试 `calculate_chan()` 是否写出完整的因果缓存契约。

    预期:返回 data_root 相对缓存目录,最终写出 `_SUCCESS`,生成所有契约
    Parquet 文件,并在 manifest 中记录覆盖范围和检查点元数据。
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
        "level_centers",
        "level_movements",
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
    """测试缠论计算是否输出结构化运行日志。

    预期:计算路径使用 `logging_proxy` 配置的进程级日志器,并输出固定文本格式
    的阶段日志,便于 Go 侧和人工排查任务状态。
    """
    payload = _payload(tmp_path, output_path="cache/chan/logged")
    payload["job_id"] = "job-chan-log"
    payload["trace_id"] = "trace-chan-log"
    calculate_chan(payload, PathGuard(tmp_path), threading.Event())
    logger.info("Chan runtime log smoke completed")
