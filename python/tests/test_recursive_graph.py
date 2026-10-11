from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace

import pytest
from test_recursive_proof import VERIFIERS, _fixture

from tvbt.chan.recursive_graph import RecursiveProofGraph
from tvbt.chan.recursive_proof import MovementClaim, ObjectRef, RangeFact, StructureProofError


def _graph() -> tuple[RecursiveProofGraph, list[MovementClaim], str]:
    claims, facts = _fixture()
    graph = RecursiveProofGraph(claims[0].fact.scope, VERIFIERS)
    for fact in facts.values():
        if fact.kind != "movement":
            graph.upsert_source(fact, cursor=15)
    for claim in claims:
        graph.upsert_movement(claim, cursor=15)
    center = graph.derive_center(tuple(claim.fact.ref for claim in claims), cursor=15)
    return graph, claims, center.object_id


def test_source_revision_invalidates_dependent_movement_and_parent_at_revision_cursor() -> None:
    graph, _, center_id = _graph()
    before = graph.centers[center_id]
    source = graph.facts["segment-0"]
    corrected = replace(
        source,
        ref=ObjectRef(source.ref.object_id, 2),
        known_at_bar_index=20,
        high_i64=111,
    )
    graph.upsert_source(corrected, cursor=20)
    assert center_id not in graph.centers
    assert "movement-0" not in graph.facts
    assert "movement-1" in graph.facts and "movement-2" in graph.facts
    assert [
        (item.object_id, item.known_at_bar_index, item.reason) for item in graph.invalidations
    ] == [
        ("movement-0", 20, "source_revised"),
        (center_id, 20, "source_revised"),
    ]
    assert before.known_at_bar_index == 15 and before.zg_i64 == 115


def test_removed_completion_witness_invalidates_all_movements_once_and_parent_once() -> None:
    graph, _, center_id = _graph()
    graph.remove_source(graph.facts["synthetic-boundary"].ref, cursor=20)
    assert not graph.centers
    assert not any(fact.kind == "movement" for fact in graph.facts.values())
    ids = [item.object_id for item in graph.invalidations]
    assert set(ids) == {"movement-0", "movement-1", "movement-2", center_id}
    assert len(ids) == len(set(ids))
    assert all(item.known_at_bar_index == 20 for item in graph.invalidations)


def test_unrelated_source_and_identical_upserts_do_not_change_frozen_parent_core() -> None:
    graph, claims, center_id = _graph()
    before = graph.centers[center_id]
    graph.upsert_movement(claims[0], cursor=20)
    graph.upsert_source(
        replace(graph.facts["synthetic-boundary"], ref=ObjectRef("unrelated", 1)),
        cursor=20,
    )
    assert graph.derive_center(tuple(claim.fact.ref for claim in claims), cursor=20) == before
    assert not graph.invalidations


def test_failed_movement_claim_does_not_mutate_graph_or_cursor() -> None:
    graph, claims, _ = _graph()
    state = graph.state()
    revised = replace(
        claims[0],
        completion_rule="unregistered",
        fact=replace(claims[0].fact, ref=ObjectRef("movement-0", 2), known_at_bar_index=20),
    )
    with pytest.raises(StructureProofError, match="completion rule"):
        graph.upsert_movement(revised, cursor=20)
    assert graph.state() == state


def test_source_revisions_cannot_change_membership_without_revision_or_backdate_correction() -> (
    None
):
    graph, _, _ = _graph()
    source = graph.facts["segment-0"]
    before = graph.state()
    for revised in (
        replace(source, high_i64=111),
        replace(source, ref=ObjectRef("segment-0", 2), high_i64=111),
    ):
        with pytest.raises(StructureProofError):
            graph.upsert_source(revised, cursor=20)
        assert graph.state() == before


def test_removed_fact_cannot_be_resurrected_using_its_old_revision() -> None:
    graph, _, _ = _graph()
    source = graph.facts["segment-0"]
    graph.remove_source(source.ref, cursor=20)
    with pytest.raises(StructureProofError, match="newer revision"):
        graph.upsert_source(source, cursor=20)
    graph.upsert_source(
        replace(source, ref=ObjectRef("segment-0", 2), known_at_bar_index=21),
        cursor=21,
    )
    assert graph.facts["segment-0"].ref.object_revision == 2


def test_checkpoint_roundtrip_revalidates_and_continues_with_identical_invalidations() -> None:
    original, _, _ = _graph()
    encoded = json.dumps(original.state(), sort_keys=True)
    restored = RecursiveProofGraph.from_state(json.loads(encoded), original.scope, VERIFIERS)
    assert json.dumps(restored.state(), sort_keys=True) == encoded
    for graph in (original, restored):
        graph.remove_source(graph.facts["segment-4"].ref, cursor=20)
    assert original.state() == restored.state()
    restarted = RecursiveProofGraph.from_state(
        json.loads(json.dumps(restored.state())),
        restored.scope,
        VERIFIERS,
    )
    assert restarted.state() == restored.state()


@pytest.mark.parametrize(
    "mutation", ["core", "claim", "duplicate", "orphan", "floor", "scope", "cycle"]
)
def test_checkpoint_does_not_trust_corrupted_core_membership_or_identity(mutation: str) -> None:
    graph, _, _ = _graph()
    state = deepcopy(graph.state())
    if mutation == "core":
        state["centers"][0]["zg_i64"] += 1
    elif mutation == "claim":
        state["claims"][0]["completion_rule"] = "unknown"
    elif mutation == "duplicate":
        state["facts"].append(deepcopy(state["facts"][0]))
    elif mutation == "orphan":
        state["claims"].pop()
    elif mutation == "floor":
        state["revision_floors"]["segment-0"] = 0
    elif mutation == "cycle":
        state["claims"][0]["completion_refs"] = [state["claims"][1]["fact_ref"]]
        state["claims"][1]["completion_refs"] = [state["claims"][0]["fact_ref"]]
    else:
        state["scope"]["data_revision"] = "sha256:" + "b" * 64
    with pytest.raises(StructureProofError):
        RecursiveProofGraph.from_state(state, graph.scope, VERIFIERS)


def test_checkpoint_requires_the_actual_completion_rule_to_be_available() -> None:
    graph, _, _ = _graph()
    with pytest.raises(StructureProofError, match="completion rule"):
        RecursiveProofGraph.from_state(graph.state(), graph.scope, {})


def test_fact_snapshot_cannot_be_used_as_a_completed_movement_without_its_claim() -> None:
    claims, _ = _fixture()
    graph = RecursiveProofGraph(claims[0].fact.scope, VERIFIERS)
    with pytest.raises(StructureProofError, match="verified movement claim"):
        graph.upsert_source(claims[0].fact, cursor=15)


def test_graph_rejects_historical_mutation_after_later_source_revision() -> None:
    graph, _, _ = _graph()
    source = graph.facts["segment-0"]
    graph.upsert_source(source, cursor=20)
    with pytest.raises(StructureProofError, match="earlier causal prefix"):
        graph.remove_source(source.ref, cursor=19)


@pytest.mark.parametrize("cursor", [True, 15.5, 2**63])
def test_checkpoint_cannot_restore_a_non_int64_or_boolean_cursor(cursor: int) -> None:
    graph, _, _ = _graph()
    state = graph.state()
    state["cursor"] = cursor
    with pytest.raises(StructureProofError):
        RecursiveProofGraph.from_state(state, graph.scope, VERIFIERS)


def test_checkpoint_does_not_accept_numeric_identity_as_a_dataset_name() -> None:
    graph, _, _ = _graph()
    state = graph.state()
    state["scope"]["dataset_id"] = True
    with pytest.raises(StructureProofError):
        RecursiveProofGraph.from_state(state, graph.scope, VERIFIERS)


def test_l2_parent_rebuild_and_source_invalidation_follow_the_entire_ancestry() -> None:
    original_claims, _ = _fixture()
    graph = RecursiveProofGraph(original_claims[0].fact.scope, VERIFIERS)
    upper_claims = []
    for block in range(3):
        claims, facts = _fixture([90, 110, 95, 120, 110, 120, 100, 110, 100, 95])
        prefix = f"block-{block}-"
        shifted: dict[str, RangeFact] = {}
        for key, fact in facts.items():
            shifted[key] = replace(
                fact,
                ref=ObjectRef(prefix + key, 1),
                start=replace(
                    fact.start,
                    bar_index=fact.start.bar_index + 9 * block,
                    time=fact.start.time + 9000 * block,
                    price_i64=fact.start.price_i64 + 5 * block,
                ),
                end=replace(
                    fact.end,
                    bar_index=fact.end.bar_index + 9 * block,
                    time=fact.end.time + 9000 * block,
                    price_i64=fact.end.price_i64 + 5 * block,
                ),
                low_i64=fact.low_i64 + 5 * block,
                high_i64=fact.high_i64 + 5 * block,
                low_source_bar_index=fact.low_source_bar_index + 9 * block,
                high_source_bar_index=fact.high_source_bar_index + 9 * block,
                known_at_bar_index=fact.known_at_bar_index + 9 * block,
            )
        for fact in shifted.values():
            if fact.kind != "movement":
                graph.upsert_source(fact, cursor=33)
        children = []
        for claim in claims:
            child = replace(
                claim,
                fact=shifted[claim.fact.ref.object_id],
                component_refs=tuple(
                    ObjectRef(prefix + ref.object_id, 1) for ref in claim.component_refs
                ),
                completion_refs=tuple(
                    ObjectRef(prefix + ref.object_id, 1) for ref in claim.completion_refs
                ),
            )
            graph.upsert_movement(child, cursor=33)
            children.append(child)
        low = min(children, key=lambda item: (item.fact.low_i64, item.fact.low_source_bar_index))
        high = max(
            children, key=lambda item: (item.fact.high_i64, -item.fact.high_source_bar_index)
        )
        upper = MovementClaim(
            replace(
                children[0].fact,
                ref=ObjectRef(f"upper-{block}", 1),
                level=1,
                end=children[-1].fact.end,
                low_i64=low.fact.low_i64,
                high_i64=high.fact.high_i64,
                low_source_bar_index=low.fact.low_source_bar_index,
                high_source_bar_index=high.fact.high_source_bar_index,
            ),
            tuple(child.fact.ref for child in children),
            "synthetic_boundary_only",
            children[0].completion_refs,
        )
        graph.upsert_movement(upper, cursor=33)
        upper_claims.append(upper)
    center = graph.derive_center(tuple(claim.fact.ref for claim in upper_claims), cursor=33)
    assert center.level == 2
    assert (center.zd_i64, center.zg_i64, center.known_at_bar_index) == (100, 120, 33)
    restored = RecursiveProofGraph.from_state(
        json.loads(json.dumps(graph.state())), graph.scope, VERIFIERS
    )
    for current in (graph, restored):
        current.remove_source(current.facts["block-0-segment-0"].ref, cursor=40)
        assert [item.object_id for item in current.invalidations] == [
            "block-0-movement-0",
            "upper-0",
            center.object_id,
        ]
        assert "upper-1" in current.facts and "upper-2" in current.facts
    assert graph.state() == restored.state()
