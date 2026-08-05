from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

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
    event_seq: int
    known_at_bar_index: int
    object_type: str
    object_id: str
    operation: str
    object_revision: int
    payload_json: str

    def row(self) -> dict[str, int | str]:
        return asdict(self)


class EventEmitter:
    """Produces a deterministic upsert/delete stream for semantic objects."""

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
        if object_type not in OBJECT_TYPES:
            raise ValueError(f"unsupported Chan object type: {object_type}")
        if not object_id:
            raise ValueError("object_id is required")
        return f"{object_type}:{object_id}"

    @staticmethod
    def _canonical(payload: dict[str, Any]) -> str:
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def upsert(
        self,
        known_at_bar_index: int,
        object_type: str,
        object_id: str,
        payload: dict[str, Any],
    ) -> ChanEvent | None:
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
        return {
            "next_event_seq": self._next_event_seq,
            "objects": self._objects,
            "revisions": self._revisions,
        }

    def current(self, object_type: str) -> list[dict[str, Any]]:
        if object_type not in OBJECT_TYPES:
            raise ValueError(f"unsupported Chan object type: {object_type}")
        prefix = f"{object_type}:"
        return [dict(value) for key, value in self._objects.items() if key.startswith(prefix)]

    @classmethod
    def from_state(cls, state: dict[str, Any]) -> EventEmitter:
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
