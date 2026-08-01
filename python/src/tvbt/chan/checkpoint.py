from __future__ import annotations

import json
from typing import Any

CHECKPOINT_FORMAT = "tvbt-chan-checkpoint-v1"


class CheckpointVersionError(ValueError):
    pass


def dump_checkpoint(algorithm_version: str, bar_index: int, state: dict[str, Any]) -> bytes:
    if not algorithm_version or bar_index < 0:
        raise ValueError("checkpoint algorithm version and bar index are required")
    envelope = {
        "format": CHECKPOINT_FORMAT,
        "algorithm_version": algorithm_version,
        "bar_index": bar_index,
        "state": state,
    }
    return json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def load_checkpoint(data: bytes, expected_algorithm_version: str) -> tuple[int, dict[str, Any]]:
    try:
        envelope = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("checkpoint is not valid canonical JSON") from exc
    if not isinstance(envelope, dict) or envelope.get("format") != CHECKPOINT_FORMAT:
        raise ValueError("checkpoint format is invalid")
    actual_version = envelope.get("algorithm_version")
    if actual_version != expected_algorithm_version:
        reason = (
            f"checkpoint algorithm version {actual_version!r} "
            f"does not match {expected_algorithm_version!r}"
        )
        raise CheckpointVersionError(reason)
    bar_index = envelope.get("bar_index")
    state = envelope.get("state")
    if not isinstance(bar_index, int) or bar_index < 0 or not isinstance(state, dict):
        raise ValueError("checkpoint payload is invalid")
    return bar_index, state
