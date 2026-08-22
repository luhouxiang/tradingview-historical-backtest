from __future__ import annotations

import pytest

from tvbt.chan.level_graph import GraphCenter, build_level_graph, center_relation


def center(
    object_id: str,
    start: int,
    dd: int,
    gg: int,
    *,
    component_count: int = 3,
    status: str = "confirmed",
) -> GraphCenter:
    return GraphCenter(
        object_id=object_id,
        level_id="L0",
        start_bar_index=start,
        start_time=start * 1000,
        end_bar_index=start + component_count,
        end_time=(start + component_count) * 1000,
        zd_i64=dd + 2,
        zg_i64=gg - 2,
        dd_i64=dd,
        gg_i64=gg,
        component_kind="segment",
        component_object_ids=tuple(f"{object_id}-s{index}" for index in range(component_count)),
        component_known_at=tuple(start + index + 1 for index in range(component_count)),
        status=status,  # type: ignore[arg-type]
        confirmed_at_bar_index=start + 3,
        known_at_bar_index=start + component_count,
    )


def test_complete_envelope_relation_treats_touch_as_overlap() -> None:
    first = center("a", 0, 10, 20)
    assert center_relation(first, center("up", 10, 21, 30)) == "up"
    assert center_relation(first, center("down", 10, 0, 9)) == "down"
    assert center_relation(first, center("touch", 10, 20, 30)) == "overlap"


def test_single_and_ordered_centers_are_mutually_exclusive_movements() -> None:
    single = build_level_graph([center("a", 0, 10, 20)])
    assert single.movements[0][1]["classification"] == "consolidation"
    assert single.movements[0][1]["status"] == "candidate"

    rising = build_level_graph([center("a", 0, 10, 20), center("b", 10, 21, 30)])
    movement = rising.movements[0][1]
    assert movement["classification"] == "uptrend"
    assert movement["previous_classification"] == "consolidation"
    assert movement["catalog_event"] == "movement_reclassified"
    assert not rising.centers


def test_overlapping_envelopes_create_next_level_candidate() -> None:
    graph = build_level_graph([center("a", 0, 10, 20), center("b", 10, 15, 25)])
    movement_id, movement, _ = graph.movements[0]
    candidate_id, candidate, _ = graph.centers[0]
    assert movement["classification"] == "higher_level_center_candidate"
    assert movement["parent_center_candidate_id"] == candidate_id
    assert candidate["level_id"] == "L1"
    assert candidate["zd_i64"] == 15
    assert candidate["zg_i64"] == 20
    assert candidate["component_object_ids"] == [movement_id]
    assert candidate["confirmed"] is False


def test_ninth_component_promotes_with_frozen_core_and_stable_identity() -> None:
    promoted = build_level_graph([center("wide", 0, 10, 30, component_count=9)])
    object_id, payload, _ = promoted.centers[0]
    assert payload["level_id"] == "L1"
    assert payload["status"] == "promoted"
    assert payload["catalog_event"] == "center_promoted"
    assert payload["confirmed_at_bar_index"] == 9
    assert (payload["zd_i64"], payload["zg_i64"]) == (12, 28)

    extended = build_level_graph([center("wide", 0, 10, 30, component_count=10)])
    extended_id, extended_payload, _ = extended.centers[0]
    assert extended_id == object_id
    assert extended_payload["status"] == "extended"
    assert extended_payload["confirmed_at_bar_index"] == 9

    terminated = build_level_graph([center("wide", 0, 10, 30, component_count=10, status="left")])
    assert terminated.centers[0][0] == object_id
    assert terminated.centers[0][1]["status"] == "terminated"


def test_promoted_l1_centers_can_form_l2_candidate() -> None:
    graph = build_level_graph(
        [
            center("a", 0, 10, 30, component_count=9),
            center("b", 20, 20, 40, component_count=9),
        ]
    )
    assert [spec[1]["level_id"] for spec in graph.centers] == ["L1", "L1", "L1", "L2"]
    assert (
        sum(spec[1]["promotion_reason"] == "nine_component_extension" for spec in graph.centers)
        == 2
    )
    assert [spec[1]["classification"] for spec in graph.movements] == [
        "higher_level_center_candidate",
        "higher_level_center_candidate",
    ]


def test_base_level_validation_and_prefix_identity() -> None:
    first = center("a", 0, 10, 20)
    initial = build_level_graph([first])
    extended = build_level_graph([first, center("b", 10, 21, 30)])
    assert initial.movements[0][0] == extended.movements[0][0]
    with pytest.raises(ValueError, match="only L0"):
        build_level_graph([GraphCenter(**{**first.__dict__, "level_id": "L1"})])
