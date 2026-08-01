from __future__ import annotations

import json

import pytest

from tvbt.chan import CheckpointVersionError, EventEmitter, dump_checkpoint, load_checkpoint


def test_event_emitter_is_deterministic_and_suppresses_unchanged_upserts() -> None:
    emitter = EventEmitter()
    first = emitter.upsert(12, "fractal", "fractal-10", {"bar_index": 10, "confirmed": False})
    assert first is not None
    assert first.event_seq == 1
    assert first.object_revision == 1
    assert (
        emitter.upsert(12, "fractal", "fractal-10", {"bar_index": 10, "confirmed": False}) is None
    )
    changed = emitter.upsert(14, "fractal", "fractal-10", {"bar_index": 10, "confirmed": True})
    assert changed is not None
    assert changed.event_seq == 2
    assert changed.object_revision == 2
    assert json.loads(changed.payload_json)["known_at_bar_index"] == 14
    deleted = emitter.delete(15, "fractal", "fractal-10")
    assert deleted is not None
    assert deleted.event_seq == 3
    assert deleted.object_revision == 3
    assert emitter.delete(16, "fractal", "fractal-10") is None


def test_checkpoint_round_trip_restores_event_sequence_and_revision() -> None:
    emitter = EventEmitter()
    emitter.upsert(5, "bi", "bi-1", {"start_bar_index": 1, "end_bar_index": 5})
    encoded = dump_checkpoint("1.0.0", 5, {"emitter": emitter.state()})
    assert encoded == dump_checkpoint("1.0.0", 5, {"emitter": emitter.state()})
    bar_index, state = load_checkpoint(encoded, "1.0.0")
    restored = EventEmitter.from_state(state["emitter"])
    event = restored.upsert(8, "bi", "bi-1", {"start_bar_index": 1, "end_bar_index": 8})
    assert bar_index == 5
    assert event is not None
    assert event.event_seq == 2
    assert event.object_revision == 2


def test_checkpoint_rejects_algorithm_version_mismatch() -> None:
    encoded = dump_checkpoint("1.0.0", 5, {"emitter": EventEmitter().state()})
    with pytest.raises(CheckpointVersionError):
        load_checkpoint(encoded, "2.0.0")
