from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

# 缠论语义对象的全集。新增对象类型时必须同步 storage schema、Go 范围读取、
# Vue 图层和对象树；否则事件流可能写出 Go/Vue 无法消费的数据。
OBJECT_TYPES = frozenset(
    {
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
)


@dataclass(frozen=True)
class ChanEvent:
    """单条因果事件。

    字段说明：

    - `event_seq`：计算内单调递增序号，保证同一 `known_at_bar_index` 下的稳定排序。
    - `known_at_bar_index`：该 upsert/delete 最早可被回放游标看到的 K 线。
    - `object_type/object_id`：语义对象身份，前端按它们做对象树定位和图层合并。
    - `operation`：`upsert` 表示新增或修订，`delete` 表示当前事实集中移除。
    - `object_revision`：同一对象每次内容变化递增，便于前端区分重算后的修订。
    - `payload_json`：规范化 JSON；删除事件使用 `{}`，快照字段在 upsert 中保存。
    """

    event_seq: int
    known_at_bar_index: int
    object_type: str
    object_id: str
    operation: str
    object_revision: int
    payload_json: str

    def row(self) -> dict[str, int | str]:
        """转换为 Parquet 行，字段名与 `EVENT_SCHEMA` 保持一致。"""
        return asdict(self)


class EventEmitter:
    """维护当前对象快照，并生成确定性的 upsert/delete 事件流。

    引擎会反复重扫笔中枢、段中枢、背驰和买卖点。`EventEmitter` 的职责是把
    “当前应该存在的对象集合”转换成审计友好的增量事件，同时保证：

    - 内容未变化的对象不重复发事件。
    - 同一对象 ID 的每次语义变化都有递增 revision。
    - 检查点恢复后事件序号、对象快照和 revision 能继续衔接。
    """

    def __init__(
        self,
        *,
        next_event_seq: int = 1,
        objects: dict[str, dict[str, Any]] | None = None,
        revisions: dict[str, int] | None = None,
    ) -> None:
        if next_event_seq < 1:
            raise ValueError("next_event_seq must be positive")
        self._next_event_seq = next_event_seq
        self._objects = dict(objects or {})
        self._revisions = dict(revisions or {})
        self.events: list[ChanEvent] = []

    @staticmethod
    def _key(object_type: str, object_id: str) -> str:
        """把对象类型和稳定 ID 组合成内部字典键，并集中校验对象类型。"""
        if object_type not in OBJECT_TYPES:
            raise ValueError(f"unsupported Chan object type: {object_type}")
        if not object_id:
            raise ValueError("object_id is required")
        return f"{object_type}:{object_id}"

    @staticmethod
    def _canonical(payload: dict[str, Any]) -> str:
        """生成稳定 JSON，避免字典顺序导致事件内容哈希或测试结果漂移。"""
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def upsert(
        self,
        known_at_bar_index: int,
        object_type: str,
        object_id: str,
        payload: dict[str, Any],
    ) -> ChanEvent | None:
        """新增或修订对象。

        `payload` 不包含 `object_id/known_at_bar_index/object_revision` 时更清晰；
        本方法会统一补齐这些事件和快照字段。若语义字段没有变化则返回 `None`。
        """
        if known_at_bar_index < 0:
            raise ValueError("known_at_bar_index must not be negative")
        key = self._key(object_type, object_id)
        semantic = dict(payload)
        semantic["object_id"] = object_id
        semantic["known_at_bar_index"] = known_at_bar_index
        previous = self._objects.get(key)
        comparable = {name: value for name, value in semantic.items() if name != "object_revision"}
        previous_comparable = (
            {name: value for name, value in previous.items() if name != "object_revision"}
            if previous is not None
            else None
        )
        if previous_comparable == comparable:
            return None
        revision = self._revisions.get(key, 0) + 1
        semantic["object_revision"] = revision
        self._objects[key] = semantic
        self._revisions[key] = revision
        return self._append(
            known_at_bar_index,
            object_type,
            object_id,
            "upsert",
            revision,
            self._canonical(semantic),
        )

    def delete(self, known_at_bar_index: int, object_type: str, object_id: str) -> ChanEvent | None:
        """删除对象并发出删除事件；对象已经不存在时保持幂等。"""
        key = self._key(object_type, object_id)
        if key not in self._objects:
            return None
        del self._objects[key]
        revision = self._revisions.get(key, 0) + 1
        self._revisions[key] = revision
        return self._append(known_at_bar_index, object_type, object_id, "delete", revision, "{}")

    def _append(
        self,
        known_at_bar_index: int,
        object_type: str,
        object_id: str,
        operation: str,
        revision: int,
        payload_json: str,
    ) -> ChanEvent:
        event = ChanEvent(
            event_seq=self._next_event_seq,
            known_at_bar_index=known_at_bar_index,
            object_type=object_type,
            object_id=object_id,
            operation=operation,
            object_revision=revision,
            payload_json=payload_json,
        )
        self._next_event_seq += 1
        self.events.append(event)
        return event

    def state(self) -> dict[str, Any]:
        """导出检查点状态，不包含历史事件列表，只保存继续运行所需快照。"""
        return {
            "next_event_seq": self._next_event_seq,
            "objects": self._objects,
            "revisions": self._revisions,
        }

    def current(self, object_type: str) -> list[dict[str, Any]]:
        """返回某类对象的当前快照副本，调用方可以安全排序和序列化。"""
        if object_type not in OBJECT_TYPES:
            raise ValueError(f"unsupported Chan object type: {object_type}")
        prefix = f"{object_type}:"
        return [dict(value) for key, value in self._objects.items() if key.startswith(prefix)]

    @classmethod
    def from_state(cls, state: dict[str, Any]) -> EventEmitter:
        """从检查点恢复事件收集器，继续沿用原有对象 revision。"""
        next_event_seq = state.get("next_event_seq")
        objects = state.get("objects")
        revisions = state.get("revisions")
        if (
            not isinstance(next_event_seq, int)
            or not isinstance(objects, dict)
            or not isinstance(revisions, dict)
        ):
            raise ValueError("event emitter checkpoint state is invalid")
        return cls(
            next_event_seq=next_event_seq,
            objects=objects,
            revisions={str(key): int(value) for key, value in revisions.items()},
        )
