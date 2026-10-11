from __future__ import annotations

import json
import threading
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pyarrow.parquet as pq
import pytest
from test_chan_storage import payload
from test_recursive_decomposition import PRICES, calculate, lines_for

from tvbt.chan.algorithm import definition, run_chan
from tvbt.chan.engine import ChanEngine, ChanParameters
from tvbt.chan.storage import ChanResult, write_chan_cache
from tvbt.storage.path_guard import PathGuard


def test_production_emitter_references_match_current_revisions_and_discovery() -> None:
    engine = ChanEngine(ChanParameters())
    roots = lines_for(PRICES)
    engine._raw_position_by_bar_index = {i: i for i in range(212)}
    for count in range(1, len(roots) + 1):
        cursor = roots[count - 1].known_at_bar_index
        engine._segment_lines = [
            SimpleNamespace(
                object_id=line.object_id,
                start=line.start,
                end=line.end,
                direction=line.direction,
                range_low_i64=line.fact.low_i64,
                range_high_i64=line.fact.high_i64,
                range_low_source_bar_index=line.fact.low_source_bar_index,
                range_high_source_bar_index=line.fact.high_source_bar_index,
            )
            for line in roots[:count]
        ]
        newest = roots[count - 1]
        engine.emitter.upsert(
            cursor,
            "segment",
            newest.object_id,
            {"start_bar_index": newest.start.bar_index, "end_bar_index": newest.end.bar_index},
        )
        engine._update_recursive_objects(cursor)
        by_id = {
            row["object_id"]: row
            for kind in ("segment", "recursive_movement", "recursive_center")
            for row in engine.emitter.current(kind)
        }
        for row in engine.emitter.current("recursive_membership"):
            ref = row["child_ref"]
            assert ref["object_revision"] == by_id[ref["object_id"]]["object_revision"]
            assert row["known_at_bar_index"] >= by_id[ref["object_id"]]["known_at_bar_index"]
            assert row["known_at_bar_index"] <= cursor
    rows = engine.result_rows()
    assert any(row["level"] == 1 for row in rows["recursive_centers"])


def test_nonempty_recursive_cache_preserves_all_proof_payloads(tmp_path: Path) -> None:
    _, snapshot = calculate(PRICES)
    result = ChanResult(
        bar_count=212,
        first_bar_index=0,
        last_bar_index=211,
        merged_bar_count=212,
        recursive_movements=snapshot["movements"],
        recursive_centers=snapshot["centers"],
        recursive_memberships=snapshot["memberships"],
    )
    ref = write_chan_cache(payload(), PathGuard(tmp_path), result)
    directory = tmp_path / ref
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 13
    for name in ("movements", "centers", "memberships"):
        parquet_rows = pq.read_table(directory / f"recursive_{name}.parquet").to_pylist()
        assert [json.loads(row["payload_json"]) for row in parquet_rows] == snapshot[name]
        assert manifest["counts"][f"recursive_{name}"] == len(snapshot[name])
        assert all(row["known_at_bar_index"] >= row["end_bar_index"] for row in parquet_rows)


def test_changed_source_revision_removes_dependent_parent_at_current_cursor() -> None:
    runtime, before = calculate(PRICES)
    roots = lines_for(PRICES)
    # Remove enough complete input that the prior parent cannot be certified.
    revised = tuple(
        replace(
            line,
            fact=replace(
                line.fact, ref=replace(line.fact.ref, object_revision=2), known_at_bar_index=300
            ),
        )
        for line in roots[:5]
    )
    after = runtime.update(revised, {i: i for i in range(301)}, cursor=300, discovery_at=300)
    assert not after["centers"] or all(row["level"] == 0 for row in after["centers"])
    ids = {row["object_id"] for rows in after.values() for row in rows}
    removed = [row["object_id"] for row in before["centers"] if row["level"] == 1]
    assert removed and all(object_id not in ids for object_id in removed)
    assert all(runtime.state()["versions"][object_id][2] == 300 for object_id in removed)


def test_real_forming_tail_checkpoint_keeps_future_event_sequence_and_rejects_tampering() -> None:
    root = Path(__file__).parents[2] / "trading-data"
    relative = "normalized/SHFE.AOL9.5m/0a49ce61a806/"
    meta_path = root / relative / "meta.json"
    if not meta_path.is_file():
        pytest.skip("requires the explicit immutable AOL9 0a49ce61a806 dataset")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    spec = definition()
    request = {
        "dataset": {
            "dataset_id": meta["dataset_id"],
            "data_revision": meta["data_revision"],
            "bars_path": relative + "bars.parquet",
            "meta_path": relative + "meta.json",
        },
        "algorithm": {
            key: spec[key] for key in ("kind", "algorithm_id", "algorithm_version", "source_hash")
        },
        "parameters": {"checkpoint_interval": 1024},
    }
    engine, _, _ = run_chan(request, PathGuard(root), threading.Event(), last_bar_index=2876)
    assert any(row["status"] == "forming" for row in engine.emitter.current("divergence"))
    state = json.loads(json.dumps(engine.export_state()))
    restored = ChanEngine.from_state(state)
    before = len(engine.emitter.events)
    from tvbt.chan.engine import RawBar

    for row in pq.read_table(
        root / relative / "bars.parquet",
        filters=[("bar_index", ">", 2876), ("bar_index", "<=", 2880)],
    ).to_pylist():
        bar = RawBar(
            row["bar_index"],
            row["timestamp_utc"],
            row["open_i64"],
            row["high_i64"],
            row["low_i64"],
            row["close_i64"],
        )
        engine.update(bar)
        restored.update(bar)
    assert engine.export_state() == restored.export_state()
    assert [event.row() for event in engine.emitter.events[before:]] == [
        event.row() for event in restored.emitter.events
    ]
    core = next(
        row
        for key, row in state["emitter"]["objects"].items()
        if key.startswith("recursive_center:")
    )
    core["zg_i64"] += 1
    with pytest.raises(ValueError, match="checkpoint recursive snapshot"):
        ChanEngine.from_state(state)
