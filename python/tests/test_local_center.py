from __future__ import annotations

import json
from dataclasses import replace
from itertools import pairwise
from typing import Literal

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from tvbt.chan.engine import ChanEngine, Fractal, LineObject, RawBar
from tvbt.chan.local_center import (
    CenterStreamKey,
    CenterUnit,
    LocalCenterAccumulator,
    LocalCenterRevisionTracker,
    compare_center_boundaries,
    decompose_local_centers,
    preview_local_center,
)
from tvbt.chan.storage import CENTER_AUDIT_EVENT_SCHEMA, LOCAL_CENTER_SCHEMA


def _stream(
    *,
    unit_kind: Literal["BI", "SEGMENT"] = "BI",
    structural_level: str = "stroke",
    anchor_id: str = "full",
) -> CenterStreamKey:
    return CenterStreamKey(
        symbol="AO",
        timeframe="5m",
        unit_kind=unit_kind,
        structural_level=structural_level,
        algorithm_version="17.0.0",
        anchor_id=anchor_id,
    )


def _units(
    prices: list[int],
    *,
    confirmed_count: int | None = None,
    source_revisions: dict[int, str] | None = None,
) -> tuple[CenterUnit, ...]:
    result: list[CenterUnit] = []
    confirmed_count = len(prices) - 1 if confirmed_count is None else confirmed_count
    source_revisions = source_revisions or {}
    for index, (start, end) in enumerate(pairwise(prices)):
        result.append(
            CenterUnit(
                id=f"U{index}",
                index=index,
                direction="up" if end > start else "down",
                start_pivot_id=f"P{index}",
                end_pivot_id=f"P{index + 1}",
                start_time=index * 1_000,
                end_time=(index + 1) * 1_000,
                start_price_tick=start,
                end_price_tick=end,
                low_tick=min(start, end),
                high_tick=max(start, end),
                confirmed_at=index + 10 if index < confirmed_count else None,
                source_revision=source_revisions.get(index, "r1"),
            )
        )
    return tuple(result)


def _engine_lines(prices: list[int]) -> list[LineObject]:
    pivots = [
        Fractal(
            object_id=f"F{index}",
            fractal_type="top" if index % 2 == 0 else "bottom",
            normalized_index=index,
            bar_index=index,
            time=index * 1_000,
            price_i64=price,
            confirmed_at_bar_index=index + 10,
            known_at_bar_index=index + 10,
        )
        for index, price in enumerate(prices)
    ]
    return [
        LineObject(
            object_id=f"BI{index}",
            start=pivots[index],
            end=pivots[index + 1],
            direction="up" if end > start else "down",
            confirmed_at_bar_index=index + 10,
            known_at_bar_index=index + 10,
        )
        for index, (start, end) in enumerate(pairwise(prices))
    ]


def test_a01_three_units_form_earliest_positive_overlap_seed() -> None:
    result = decompose_local_centers(_units([120, 100, 115, 105]), _stream())

    assert len(result.centers) == 1
    center = result.centers[0]
    assert center.seed_ids == ("U0", "U1", "U2")
    assert (center.zd_tick, center.zg_tick) == (105, 115)
    assert center.status == "ACTIVE"
    assert result.events[0].event_type == "SEED_FOUND"


def test_a02_internal_oscillation_does_not_create_rolling_centers() -> None:
    result = decompose_local_centers(_units([120, 100, 115, 105, 112, 108, 114, 109]), _stream())

    assert len(result.centers) == 1
    assert result.centers[0].seed_ids == ("U0", "U1", "U2")
    assert (result.centers[0].zd_tick, result.centers[0].zg_tick) == (105, 115)


def test_a03_departure_whose_retest_returns_into_core_does_not_split() -> None:
    result = decompose_local_centers(_units([120, 100, 115, 105, 130, 110]), _stream())

    assert result.centers[0].status == "ACTIVE"
    assert result.centers[0].exit_id is None
    assert [event.event_type for event in result.events].count("RETEST_TOUCH") == 1
    assert not result.connections


def test_a04_retest_equal_to_zg_is_touch_not_upward_split() -> None:
    result = decompose_local_centers(_units([120, 100, 115, 105, 130, 115]), _stream())

    assert result.centers[0].status == "ACTIVE"
    touch = next(event for event in result.events if event.event_type == "RETEST_TOUCH")
    assert touch.comparison_value == result.centers[0].zg_tick


def test_a05_strict_upward_retest_confirms_split_and_restarts_at_retest() -> None:
    result = decompose_local_centers(_units([120, 100, 115, 105, 130, 118]), _stream())

    center = result.centers[0]
    assert center.status == "CLOSED"
    assert (center.exit_id, center.first_retest_id, center.break_direction) == (
        "U3",
        "U4",
        "up",
    )
    assert center.break_confirmed_at == 14
    assert any(event.event_type == "SEARCH_RESTARTED" for event in result.events)


def test_a06_unconfirmed_retest_keeps_pending_break() -> None:
    result = decompose_local_centers(
        _units([120, 100, 115, 105, 130, 118], confirmed_count=4), _stream()
    )

    center = result.centers[0]
    assert center.status == "PENDING_BREAK"
    assert center.pending_exit_id == "U3"
    assert not any(event.event_type == "BREAK_CONFIRMED" for event in result.events)


@pytest.mark.parametrize("unit_kind", ["BI", "SEGMENT"])
@pytest.mark.parametrize(
    ("prices", "expected", "comparison"),
    [
        ([120, 100, 115, 105, 130, 118], "RETEST_PENDING", 118),
        ([120, 100, 115, 105, 130, 115], "RETEST_TOUCH", 115),
        ([120, 100, 115, 105, 130, 90], "RETEST_TOUCH", 90),
        ([100, 120, 105, 115, 90, 100], "RETEST_PENDING", 100),
        ([100, 120, 105, 115, 90, 105], "RETEST_TOUCH", 105),
        ([100, 120, 105, 115, 90, 130], "RETEST_TOUCH", 130),
    ],
)
def test_preview_retest_is_immediate_symmetric_and_non_tradable(
    unit_kind: Literal["BI", "SEGMENT"], prices: list[int], expected: str, comparison: int
) -> None:
    units = _units(prices, confirmed_count=4)
    confirmed = decompose_local_centers(units[:-1], _stream(unit_kind=unit_kind))
    snapshot = confirmed
    preview = preview_local_center(units, confirmed, known_at=20)
    assert preview is not None
    assert (preview.state, preview.comparison_value) == (expected, comparison)
    assert preview.confirmed is False
    assert preview.exit_unit_id == "U3"
    assert confirmed == snapshot
    assert confirmed.centers[-1].status == "PENDING_BREAK"
    assert not any(event.event_type == "BREAK_CONFIRMED" for event in confirmed.events)


def test_preview_touch_uses_complete_range_not_rebounding_endpoint() -> None:
    units = _units([120, 100, 115, 105, 130, 118], confirmed_count=4)
    confirmed = decompose_local_centers(units[:-1], _stream())
    untouched = preview_local_center(units, confirmed, known_at=20)
    touched_units = (
        *units[:-1],
        replace(units[-1], low_tick=115, source_revision="observed-touch"),
    )
    touched = preview_local_center(touched_units, confirmed, known_at=21)
    assert untouched is not None and touched is not None
    assert untouched.id == touched.id
    assert touched.state == "RETEST_TOUCH"
    assert touched.comparison_value == 115


def test_preview_departure_does_not_confirm_or_form_a_new_seed() -> None:
    units = _units([120, 100, 115, 105, 130], confirmed_count=3)
    confirmed = decompose_local_centers(units[:-1], _stream())
    preview = preview_local_center(units, confirmed, known_at=20)
    assert preview is not None and preview.state == "EXIT_PENDING"
    assert len(confirmed.centers) == 1
    assert confirmed.centers[-1].status == "ACTIVE"
    assert preview_local_center(units[:-1], confirmed, known_at=20) is None
    with pytest.raises(ValueError, match="future confirmed"):
        preview_local_center(units, confirmed, known_at=11)


def test_accumulator_preview_does_not_mutate_checkpoint_or_confirmed_audit_events() -> None:
    units = _units([120, 100, 115, 105, 130, 118], confirmed_count=4)
    accumulator = LocalCenterAccumulator(_stream())
    accumulator.update(units[:-1])
    snapshot = json.loads(json.dumps(accumulator.state()))
    events = accumulator.updated_events
    preview = accumulator.preview(units[-1], known_at=20)
    assert preview is not None and preview.state == "RETEST_PENDING"
    restored = LocalCenterAccumulator.from_state(snapshot)
    assert restored.preview(units[-1], known_at=20) == preview
    assert accumulator.state() == snapshot
    assert accumulator.updated_events == events
    with pytest.raises(ValueError, match="must be unconfirmed"):
        accumulator.preview(replace(units[-1], confirmed_at=20), known_at=20)


@pytest.mark.parametrize("unit_kind", ["BI", "SEGMENT"])
def test_production_preview_events_touch_without_changing_confirmed_center(
    unit_kind: Literal["BI", "SEGMENT"],
) -> None:
    lines = _engine_lines([120, 100, 115, 105, 130, 118])
    runtime = ChanEngine()
    if unit_kind == "BI":
        runtime.bi = lines[:4]
    else:
        runtime._segment_lines = lines[:4]
    runtime.raw_bars = [RawBar(20, 20_000, 120, 130, 110, 120)]
    runtime._update_local_center_objects(20)
    candidate = lines[4]
    object_type = "bi" if unit_kind == "BI" else "segment"
    runtime.emitter.upsert(
        20, object_type, candidate.object_id, candidate.payload(confirmed=False, status="candidate")
    )
    if unit_kind == "BI":
        runtime._active_bi_candidate_id = candidate.object_id
    confirmed = runtime.result_rows()["local_centers"]
    runtime.emitter.events.clear()
    runtime._publish_local_center_previews(20)
    preview = next(
        row
        for row in runtime.result_rows()["center_audit_events"]
        if row["event_type"] == "PREVIEW_UPDATED"
    )
    assert preview["preview_state"] == "RETEST_PENDING"
    assert preview["preview_confirmed"] is False
    runtime._publish_local_center_previews(20)
    assert len(runtime.emitter.events) == 1
    touched = {**candidate.payload(confirmed=False, status="candidate"), "range_low_i64": 115}
    runtime.emitter.upsert(21, object_type, candidate.object_id, touched)
    runtime.raw_bars.append(RawBar(21, 21_000, 118, 120, 115, 118))
    runtime._publish_local_center_previews(21)
    preview = runtime.emitter.get("center_audit_event", preview["object_id"])
    assert preview is not None and preview["preview_state"] == "RETEST_TOUCH"
    assert preview["comparison_i64"] == 115
    assert runtime.result_rows()["local_centers"] == confirmed
    sink = pa.BufferOutputStream()
    pq.write_table(pa.Table.from_pylist([preview], schema=CENTER_AUDIT_EVENT_SCHEMA), sink)
    assert (
        pq.read_table(pa.BufferReader(sink.getvalue())).to_pylist()[0]["preview_confirmed"] is False
    )
    runtime.emitter.upsert(22, object_type, candidate.object_id, candidate.payload(confirmed=True))
    runtime._publish_local_center_previews(22)
    assert runtime.emitter.get("center_audit_event", preview["object_id"]) is None
    assert runtime.emitter.events[-1].operation == "delete"


@pytest.mark.parametrize("unit_kind", ["BI", "SEGMENT"])
def test_production_local_units_use_ticks_but_public_prices_keep_native_fixed_point(
    unit_kind: Literal["BI", "SEGMENT"],
) -> None:
    runtime = ChanEngine(price_tick_i64=5)
    lines = _engine_lines([price * 5 for price in [120, 100, 115, 105, 130, 118]])
    if unit_kind == "BI":
        runtime.bi = lines[:4]
    else:
        runtime._segment_lines = lines[:4]
    runtime.raw_bars = [RawBar(20, 20_000, 600, 650, 550, 600)]
    runtime._update_local_center_objects(20)
    accumulator = runtime._local_center_accumulators[unit_kind]
    assert accumulator.last_unit is not None
    assert accumulator.last_unit.end_price_tick == 130
    center = runtime.result_rows()["local_centers"][0]
    assert (center["zd_i64"], center["zg_i64"]) == (525, 575)
    seed_event = next(
        row
        for row in runtime.result_rows()["center_audit_events"]
        if row["event_type"] == "SEED_FOUND"
    )
    assert (seed_event["zd_i64"], seed_event["zg_i64"]) == (525, 575)
    candidate = lines[4]
    runtime.emitter.upsert(
        20,
        "bi" if unit_kind == "BI" else "segment",
        candidate.object_id,
        candidate.payload(confirmed=False, status="candidate"),
    )
    if unit_kind == "BI":
        runtime._active_bi_candidate_id = candidate.object_id
    runtime._publish_local_center_previews(20)
    preview = next(
        row
        for row in runtime.result_rows()["center_audit_events"]
        if row["event_type"] == "PREVIEW_UPDATED"
    )
    assert (preview["zd_i64"], preview["zg_i64"], preview["comparison_i64"]) == (525, 575, 590)
    assert runtime.export_state()["stream_identity"]["price_tick_i64"] == 5
    if unit_kind == "BI":
        runtime.bi = lines[:5]
    else:
        runtime._segment_lines = lines[:5]
    runtime._update_local_center_objects(21)
    restart = next(
        row
        for row in runtime.result_rows()["center_audit_events"]
        if row["event_type"] == "SEARCH_RESTARTED"
    )
    assert restart["comparison_i64"] == 4  # Unit index, not a price in ticks.


def test_tick_conversion_rejects_off_grid_structural_prices_instead_of_rounding() -> None:
    runtime = ChanEngine(price_tick_i64=5)
    runtime.bi = _engine_lines([601, 500, 575, 525])
    runtime.raw_bars = [RawBar(20, 20_000, 600, 650, 550, 600)]
    with pytest.raises(ValueError, match="not aligned"):
        runtime._update_local_center_objects(20)
    with pytest.raises(ValueError, match="positive integer"):
        ChanEngine(price_tick_i64=0)


def test_a07_strict_downward_retest_confirms_split() -> None:
    result = decompose_local_centers(_units([100, 120, 105, 115, 90, 100]), _stream())

    center = result.centers[0]
    assert center.status == "CLOSED"
    assert (center.exit_id, center.first_retest_id, center.break_direction) == (
        "U3",
        "U4",
        "down",
    )


def test_a08_split_without_three_new_units_has_only_open_connection() -> None:
    result = decompose_local_centers(_units([120, 100, 115, 105, 130, 118]), _stream())

    assert len(result.centers) == 1
    assert len(result.connections) == 1
    assert result.connections[0].to_center_id is None
    assert result.stream_state == "SEEK"


def test_a09_retest_is_replayed_as_new_seed_and_departure_is_connection() -> None:
    units = _units([120, 100, 115, 105, 130, 118, 128, 120])
    result = decompose_local_centers(units, _stream())

    assert len(result.centers) == 2
    first, second = result.centers
    assert second.seed_ids == ("U4", "U5", "U6")
    assert second.scan_floor == 4
    assert second.entry_id == "U3"
    assert result.connections[0].unit_ids == ("U3",)
    assert result.connections[0].first_retest_id == "U4"
    assert result.connections[0].roles_overlap_seed is True
    assert first.first_retest_id in second.seed_ids


def test_a10_third_seed_can_also_be_departure_without_shortening_body() -> None:
    result = decompose_local_centers(_units([90, 110, 100, 120, 115]), _stream())

    center = result.centers[0]
    assert center.exit_id == "U2"
    assert center.roles_overlap_seed is True
    assert center.body_end == center.seed_end
    assert result.connections[0].roles_overlap_seed is True


def test_third_seed_exit_overlap_survives_a_later_non_overlapping_new_seed() -> None:
    units = _units([90, 110, 100, 120, 115, 140, 130, 150, 135])
    result = decompose_local_centers(units, _stream())
    assert result.centers[1].seed_ids == ("U4", "U5", "U6")
    assert result.connections[0].unit_ids == ("U2", "U3")
    assert result.connections[0].roles_overlap_seed is True
    accumulator = LocalCenterAccumulator(_stream())
    for count in range(1, len(units) + 1):
        accumulator.update(units[:count])
    assert accumulator.result().connections[0].roles_overlap_seed is True


def test_a11_core_relation_is_separate_from_envelope_and_trend() -> None:
    base = decompose_local_centers(_units([120, 100, 115, 105]), _stream()).centers[0]
    upper = replace(
        base,
        id="upper",
        zd_tick=121,
        zg_tick=130,
        observed_low=110,
        observed_high=140,
    )

    relation = compare_center_boundaries(base, upper)
    assert relation.core_relation == "CORE_ABOVE"
    assert relation.higher_level_review_required is True
    assert relation.trend_status == "UNVERIFIED"


def test_a12_parent_and_child_overlaps_survive_on_independent_layers() -> None:
    units = _units([120, 100, 115, 105])
    child = decompose_local_centers(units, _stream(structural_level="stroke")).centers[0]
    parent = decompose_local_centers(
        units, _stream(unit_kind="SEGMENT", structural_level="parent-segment")
    ).centers[0]
    child = replace(child, parent_id=parent.id)

    assert child.id != parent.id
    assert child.parent_id == parent.id
    assert child.structural_level != parent.structural_level
    assert child.body_start == parent.body_start
    assert child.observed_end == parent.observed_end


def test_a13_first_visible_seed_marks_incomplete_left_context() -> None:
    center = decompose_local_centers(
        _units([120, 100, 115, 105]), _stream(), left_context_complete=False
    ).centers[0]

    assert center.left_context_incomplete is True
    assert center.entry_id is None
    assert center.local_entry is None


def test_a14_failed_retest_is_immediately_reused_as_opposite_departure() -> None:
    result = decompose_local_centers(_units([120, 100, 115, 105, 130, 90, 100]), _stream())

    center = result.centers[0]
    assert center.status == "CLOSED"
    assert center.exit_id == "U4"
    assert center.first_retest_id == "U5"
    assert center.break_direction == "down"
    assert [event.event_type for event in result.events].count("RETEST_TOUCH") == 1


def test_a15_replay_is_deterministic_and_does_not_read_future_confirmations() -> None:
    units = _units([120, 100, 115, 105, 130, 118, 128, 120])
    first = decompose_local_centers(units, _stream())
    second = decompose_local_centers(units, _stream())
    prefix = decompose_local_centers(units[:5], _stream())
    as_of_prefix = decompose_local_centers(units, _stream(), known_at=14)

    assert first == second
    assert as_of_prefix == prefix
    assert len({event.id for event in first.events}) == len(first.events)
    assert all(event.source_file.endswith("local_center.py") for event in first.events)
    assert all(event.source_line > 0 for event in first.events)


def test_a16_source_revision_change_emits_explicit_object_revision() -> None:
    tracker = LocalCenterRevisionTracker(_stream())
    first, initial = tracker.apply(_units([120, 100, 115, 105]), known_at=20)
    second, revisions = tracker.apply(
        _units([120, 100, 115, 105], source_revisions={1: "r2"}), known_at=21
    )

    assert first.centers[0].id == second.centers[0].id
    assert any(
        revision.object_kind == "center"
        and revision.object_id == first.centers[0].id
        and revision.operation == "upsert"
        and revision.object_revision == 2
        and revision.event_type == "CENTER_REVISED"
        for revision in revisions
    )
    assert initial[0].object_revision == 1


def test_a17_anchor_change_prevents_cross_history_identity_collision() -> None:
    units = _units([120, 100, 115, 105])
    full = decompose_local_centers(units, _stream(anchor_id="full"))
    clipped = decompose_local_centers(units, _stream(anchor_id="clipped"))

    assert full.centers[0].id != clipped.centers[0].id
    assert full.centers[0].stream_key != clipped.centers[0].stream_key


def test_rejects_non_alternating_or_disconnected_units() -> None:
    units = list(_units([120, 100, 115, 105]))
    with pytest.raises(ValueError, match="directions must alternate"):
        decompose_local_centers(
            [
                units[0],
                replace(
                    units[1],
                    direction="down",
                    start_price_tick=115,
                    end_price_tick=100,
                ),
                units[2],
            ],
            _stream(),
        )
    with pytest.raises(ValueError, match="share a pivot"):
        decompose_local_centers(
            [units[0], replace(units[1], start_pivot_id="wrong"), units[2]], _stream()
        )


def test_production_engine_projects_local_objects_and_keeps_legacy_entry() -> None:
    lines = _engine_lines([120, 100, 115, 105, 130, 118, 128, 120])
    runtime = ChanEngine(
        stream_symbol="AO",
        stream_timeframe="5m",
        stream_anchor_id="revision-1:dataset-start",
    )
    runtime.bi = lines
    runtime.raw_bars = [RawBar(20, 20_000, 120, 130, 110, 120)]
    runtime._update_local_center_objects(20)
    rows = runtime.result_rows()

    assert len(rows["local_centers"]) == 2
    first, second = rows["local_centers"]
    assert first["previous_center_id"] is None
    assert first["core_relation"] is None
    assert first["higher_level_review_required"] is False
    assert second["previous_center_id"] == first["object_id"]
    assert second["core_relation"] == "CORE_ABOVE"
    assert second["higher_level_review_required"] is True
    assert all(row["trend_status"] == "UNVERIFIED" for row in rows["local_centers"])
    sink = pa.BufferOutputStream()
    pq.write_table(pa.Table.from_pylist(rows["local_centers"], schema=LOCAL_CENTER_SCHEMA), sink)
    persisted = pq.read_table(pa.BufferReader(sink.getvalue())).to_pylist()
    for name in (
        "previous_center_id",
        "core_relation",
        "higher_level_review_required",
        "trend_status",
    ):
        assert [row[name] for row in persisted] == [row[name] for row in rows["local_centers"]]
    assert len(rows["center_connections"]) == 1
    assert rows["center_connections"][0]["ordered_unit_ids"] == ["BI3"]
    assert {row["event_type"] for row in rows["center_audit_events"]} >= {
        "SEED_FOUND",
        "EXIT_PENDING",
        "BREAK_CONFIRMED",
        "SEARCH_RESTARTED",
        "ENTRY_LINKED",
    }
    assert {event.object_type for event in runtime.emitter.events} >= {
        "local_center",
        "center_connection",
        "center_audit_event",
    }
    runtime.emitter.events.clear()
    runtime._update_local_center_objects(21)
    assert runtime.emitter.events == []
    runtime.bi = _engine_lines([120, 100, 115, 105, 130, 118, 128, 120, 125])
    runtime.raw_bars = [RawBar(21, 21_000, 120, 130, 110, 125)]
    runtime._update_local_center_objects(21)
    revised_rows = runtime.result_rows()
    assert any(row["event_type"] == "CENTER_REVISED" for row in revised_rows["center_audit_events"])
    assert revised_rows["local_centers"][-1]["object_revision"] == 2


def test_incremental_accumulator_matches_full_replay_for_every_prefix_and_revision() -> None:
    units = _units([120, 100, 115, 105, 130, 110, 125, 90, 100, 95, 105, 98])
    accumulator = LocalCenterAccumulator(_stream())
    for end in range(1, len(units) + 1):
        incremental = accumulator.update(units[:end])
        full = decompose_local_centers(units[:end], _stream())
        assert incremental.centers == full.centers
        assert incremental.connections == full.connections
        assert [replace(event, source_line=0) for event in incremental.events] == [
            replace(event, source_line=0) for event in full.events
        ]

    revised = list(units)
    revised[2] = replace(revised[2], source_revision="r2")
    incremental = accumulator.update(revised)
    full = decompose_local_centers(revised, _stream())
    assert incremental.centers == full.centers
    assert incremental.connections == full.connections
    assert [replace(event, source_line=0) for event in incremental.events] == [
        replace(event, source_line=0) for event in full.events
    ]


def test_accumulator_state_restores_snapshots_without_republishing_events() -> None:
    units = _units([120, 100, 115, 105, 130, 118, 128, 120])
    uninterrupted = LocalCenterAccumulator(_stream())
    uninterrupted.update(units[:6])
    restored = LocalCenterAccumulator.from_state(json.loads(json.dumps(uninterrupted.state())))
    assert restored.updated_events == ()

    expected = uninterrupted.update(units)
    actual = restored.update(units)

    assert actual == expected
    assert restored.updated_events == uninterrupted.updated_events
