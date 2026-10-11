from __future__ import annotations

import json
from copy import deepcopy

from test_recursive_decomposition import calculate, lines_for

from tvbt.chan.engine import _signal_payload
from tvbt.chan.events import ChanEvent
from tvbt.chan.recursive_signals import recursive_signals
from tvbt.strategy import _strategy_center_events


def events():
    prices = [0, 20, 10, 15, 12, 25, 22]
    runtime, snapshot = calculate(prices)
    _, points = recursive_signals(
        runtime, lines_for(prices), snapshot, 61, {i: i for i in range(62)}
    )
    object_id, point = next(item for item in points if item[1].signal_type == "buy_3")
    center = snapshot["centers"][0]
    return [
        ChanEvent(1, 31, "recursive_center", center["object_id"], "upsert", 1, json.dumps(center)),
        ChanEvent(2, 61, "trade_point", object_id, "upsert", 1, json.dumps(_signal_payload(point))),
    ]


def test_strategy_projection_requires_actual_completed_third_point_and_keeps_source_immutable() -> (
    None
):
    source = events()
    before = deepcopy(source)
    projections = [
        event for event in _strategy_center_events(source) if event.object_type == "local_center"
    ]
    assert [event.known_at_bar_index for event in projections] == [31, 61]
    values = [json.loads(event.payload_json) for event in projections]
    assert values[0]["status"] == "CONSTRUCTED" and values[0]["leave_direction"] is None
    assert values[1]["status"] == "CLOSED" and values[1]["leave_direction"] == "up"
    assert values[1]["context_confirmation_source_id"] == source[1].object_id
    assert values[1]["source_object_type"] == "recursive_center"
    assert values[1]["source_object_revision"] == 1
    assert source == before


def test_withdrawn_third_point_removes_closure_without_removing_the_fixed_block() -> None:
    source = events()
    source.append(ChanEvent(3, 70, "trade_point", source[1].object_id, "delete", 2, "{}"))
    projections = [
        event for event in _strategy_center_events(source) if event.object_type == "local_center"
    ]
    latest = json.loads(projections[-1].payload_json)
    assert latest["status"] == "CONSTRUCTED" and latest["context_confirmation_source_id"] is None
    assert projections[-1].known_at_bar_index == 70


def test_revised_source_requires_a_current_third_point_binding() -> None:
    source = events()
    center = {**json.loads(source[0].payload_json), "object_revision": 2}
    source.append(
        ChanEvent(3, 70, "recursive_center", source[0].object_id, "upsert", 2, json.dumps(center))
    )
    projections = [
        event for event in _strategy_center_events(source) if event.object_type == "local_center"
    ]
    latest = json.loads(projections[-1].payload_json)
    assert latest["status"] == "CONSTRUCTED" and latest["source_object_revision"] == 2


def test_invalidated_point_upsert_cannot_leave_a_closed_projection() -> None:
    source = events()
    point = {**json.loads(source[1].payload_json), "confirmed": False, "status": "invalidated"}
    source.append(
        ChanEvent(3, 70, "trade_point", source[1].object_id, "upsert", 2, json.dumps(point))
    )
    projections = [
        event for event in _strategy_center_events(source) if event.object_type == "local_center"
    ]
    assert json.loads(projections[-1].payload_json)["status"] == "CONSTRUCTED"


def test_higher_level_center_is_not_projected_as_a_segment_center() -> None:
    source = events()
    higher = {**json.loads(source[0].payload_json), "level": 1}
    source = [
        ChanEvent(1, 31, "recursive_center", "parent-center", "upsert", 1, json.dumps(higher))
    ]
    assert not any(event.object_type == "local_center" for event in _strategy_center_events(source))
