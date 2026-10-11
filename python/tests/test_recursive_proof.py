from __future__ import annotations

from dataclasses import replace

import pytest

from tvbt.chan.recursive_proof import (
    MovementClaim,
    ObjectRef,
    ProofAnchor,
    ProofScope,
    RangeFact,
    StructureProofError,
    derive_parent_center,
    verify_movement,
)


def _fixture(prices: list[int] | None = None) -> tuple[list[MovementClaim], dict[str, RangeFact]]:
    scope = ProofScope("synthetic", "sha256:" + "a" * 64, "fixed-synthetic")
    prices = prices or [90, 110, 95, 120, 110, 120, 100, 110, 100, 115]
    anchors = [ProofAnchor(i, (i + 1) * 1000, price) for i, price in enumerate(prices)]
    facts: dict[str, RangeFact] = {}
    for i in range(9):
        start, end = anchors[i : i + 2]
        low_anchor, high_anchor = sorted((start, end), key=lambda item: item.price_i64)
        fact = RangeFact(
            ObjectRef(f"segment-{i}", 1),
            scope,
            -1,
            "segment",
            start,
            end,
            low_anchor.price_i64,
            high_anchor.price_i64,
            low_anchor.bar_index,
            high_anchor.bar_index,
            i + 2,
        )
        facts[fact.ref.object_id] = fact
    witness = RangeFact(
        ObjectRef("synthetic-boundary", 1),
        scope,
        -1,
        "completion",
        anchors[-1],
        ProofAnchor(10, 11000, prices[-1] - 5),
        prices[-1] - 5,
        prices[-1],
        10,
        9,
        15,
    )
    facts[witness.ref.object_id] = witness
    claims = []
    for i in range(3):
        components = [facts[f"segment-{j}"] for j in range(i * 3, i * 3 + 3)]
        low = min(components, key=lambda item: (item.low_i64, item.low_source_bar_index))
        high = max(components, key=lambda item: (item.high_i64, -item.high_source_bar_index))
        movement = RangeFact(
            ObjectRef(f"movement-{i}", 1),
            scope,
            0,
            "movement",
            components[0].start,
            components[-1].end,
            low.low_i64,
            high.high_i64,
            low.low_source_bar_index,
            high.high_source_bar_index,
            15,
        )
        facts[movement.ref.object_id] = movement
        claims.append(
            MovementClaim(
                movement,
                tuple(item.ref for item in components),
                "synthetic_boundary_only",
                (witness.ref,),
            )
        )
    return claims, facts


def _synthetic_completion(claim: MovementClaim, witnesses: tuple[RangeFact, ...]) -> bool:
    # A test-only boundary predicate exercises dispatcher behavior. It is not
    # registered by production and does not stand in for a Chan completion rule.
    return (
        len(witnesses) == 1
        and witnesses[0].kind == "completion"
        and witnesses[0].start.bar_index >= claim.fact.end.bar_index
    )


VERIFIERS = {"synthetic_boundary_only": _synthetic_completion}


def test_parent_core_uses_complete_movement_ranges_not_child_center_boxes() -> None:
    claims, facts = _fixture()
    result = derive_parent_center(claims, facts, VERIFIERS, cursor=15)
    # Independently specified complete ranges: [90,120], [100,120], [100,115].
    assert (result.zd_i64, result.zg_i64) == (100, 115)
    assert (result.dd_i64, result.gg_i64) == (90, 120)
    assert result.level == 1
    assert result.known_at_bar_index == 15
    assert result.start_bar_index == 0 and result.end_bar_index == 9
    assert [ref.object_id for ref in result.seed_refs] == [
        "movement-0",
        "movement-1",
        "movement-2",
    ]


def test_no_parent_can_exist_before_completion_witness_is_known() -> None:
    claims, facts = _fixture()
    for cursor in range(15):
        with pytest.raises(StructureProofError):
            derive_parent_center(claims, facts, VERIFIERS, cursor=cursor)


def test_unregistered_or_failed_completion_is_not_a_completed_status_shortcut() -> None:
    claims, facts = _fixture()
    for verifiers in ({}, {"synthetic_boundary_only": lambda claim, witnesses: False}):
        with pytest.raises(StructureProofError, match="completion rule"):
            derive_parent_center(claims, facts, verifiers, cursor=15)


@pytest.mark.parametrize("mutation", ["revision", "dataset", "decomposition", "future"])
def test_completion_dependency_rejects_wrong_identity_or_future(mutation: str) -> None:
    claims, facts = _fixture()
    witness = facts["synthetic-boundary"]
    if mutation == "revision":
        witness = replace(witness, ref=ObjectRef(witness.ref.object_id, 2))
    elif mutation == "dataset":
        witness = replace(witness, scope=replace(witness.scope, dataset_id="different"))
    elif mutation == "decomposition":
        witness = replace(witness, scope=replace(witness.scope, decomposition_id="different"))
    else:
        witness = replace(witness, known_at_bar_index=16)
    facts[witness.ref.object_id] = witness
    with pytest.raises(StructureProofError):
        derive_parent_center(claims, facts, VERIFIERS, cursor=15)


def test_parent_rejects_different_seed_levels_without_upgrading_on_contact() -> None:
    claims, facts = _fixture()
    changed = replace(claims[2].fact, level=1)
    facts[changed.ref.object_id] = changed
    claims[2] = replace(claims[2], fact=changed)
    with pytest.raises(StructureProofError, match="lower layer"):
        derive_parent_center(claims, facts, VERIFIERS, cursor=15)


def test_repeated_movement_cannot_count_as_two_parent_seeds() -> None:
    claims, facts = _fixture()
    with pytest.raises(StructureProofError, match="distinct"):
        derive_parent_center([claims[0], claims[0], claims[2]], facts, VERIFIERS, cursor=15)


def test_gap_or_reordering_in_component_sequence_is_not_membership() -> None:
    claims, facts = _fixture()
    refs = claims[0].component_refs
    for changed in ((refs[1], refs[0], refs[2]), (refs[0], refs[2], facts["segment-3"].ref)):
        with pytest.raises(StructureProofError, match="sequence"):
            verify_movement(replace(claims[0], component_refs=changed), facts, VERIFIERS, cursor=15)


def test_reused_components_and_fabricated_range_fail_before_completion() -> None:
    claims, facts = _fixture()
    with pytest.raises(StructureProofError, match="duplicate"):
        verify_movement(
            replace(claims[0], component_refs=(claims[0].component_refs[0],) * 3),
            facts,
            VERIFIERS,
            cursor=15,
        )
    changed = replace(claims[0].fact, high_i64=121)
    facts[changed.ref.object_id] = changed
    with pytest.raises(StructureProofError, match="actual component union"):
        verify_movement(replace(claims[0], fact=changed), facts, VERIFIERS, cursor=15)


def test_claim_cannot_ignore_its_own_new_revision() -> None:
    claims, facts = _fixture()
    original = claims[0].fact
    facts[original.ref.object_id] = replace(original, ref=ObjectRef(original.ref.object_id, 2))
    with pytest.raises(StructureProofError, match="stale"):
        derive_parent_center(claims, facts, VERIFIERS, cursor=15)


def test_later_unrelated_facts_do_not_change_parent_identity_core_or_known_time() -> None:
    claims, facts = _fixture()
    earlier = derive_parent_center(claims, facts, VERIFIERS, cursor=15)
    facts["later"] = replace(
        facts["synthetic-boundary"],
        ref=ObjectRef("later", 1),
        known_at_bar_index=100,
    )
    assert derive_parent_center(claims, facts, VERIFIERS, cursor=100) == earlier


def test_movement_cannot_be_backdated_before_completion_evidence() -> None:
    claims, facts = _fixture()
    changed = replace(claims[0].fact, known_at_bar_index=14)
    facts[changed.ref.object_id] = changed
    with pytest.raises(StructureProofError, match="predates"):
        verify_movement(replace(claims[0], fact=changed), facts, VERIFIERS, cursor=15)


def test_extreme_source_must_belong_to_the_actual_component_interval() -> None:
    _, facts = _fixture()
    with pytest.raises(StructureProofError, match="escapes"):
        replace(facts["segment-0"], high_source_bar_index=9)


def test_higher_movement_must_recursively_verify_its_child_claims() -> None:
    claims, facts = _fixture()
    first, last = claims[0].fact, claims[-1].fact
    higher = RangeFact(
        ObjectRef("higher-movement", 1),
        first.scope,
        1,
        "movement",
        first.start,
        last.end,
        90,
        120,
        0,
        3,
        15,
    )
    facts[higher.ref.object_id] = higher
    claim = MovementClaim(
        higher,
        tuple(item.fact.ref for item in claims),
        "synthetic_boundary_only",
        (facts["synthetic-boundary"].ref,),
    )
    with pytest.raises(StructureProofError, match="recursive child completion proofs"):
        verify_movement(claim, facts, VERIFIERS, cursor=15)
    children = {item.fact.ref.object_id: item for item in claims}
    assert (
        verify_movement(
            claim,
            facts,
            VERIFIERS,
            cursor=15,
            movement_claims=children,
        ).completed_at_bar_index
        == 15
    )
    children[claims[1].fact.ref.object_id] = replace(claims[1], completion_rule="missing-rule")
    with pytest.raises(StructureProofError, match="completion rule"):
        verify_movement(claim, facts, VERIFIERS, cursor=15, movement_claims=children)


def test_point_core_and_no_intersection_are_distinct() -> None:
    claims, facts = _fixture([90, 100, 95, 110, 100, 115, 110, 120, 115, 125])
    point = derive_parent_center(claims, facts, VERIFIERS, cursor=15)
    assert point.zd_i64 == point.zg_i64 == 110
    claims, facts = _fixture([90, 100, 95, 110, 100, 120, 111, 121, 116, 126])
    with pytest.raises(StructureProofError, match="no common price intersection"):
        derive_parent_center(claims, facts, VERIFIERS, cursor=15)


def test_parent_seed_order_must_be_the_actual_contiguous_order() -> None:
    claims, facts = _fixture()
    with pytest.raises(StructureProofError, match="sequence"):
        derive_parent_center([claims[1], claims[0], claims[2]], facts, VERIFIERS, cursor=15)


@pytest.mark.parametrize("invalid", [True, 1.5, 2**63, -(2**63) - 1])
def test_structural_prices_cannot_silently_lose_fixed_point_int64_semantics(invalid: int) -> None:
    with pytest.raises(StructureProofError, match="int64"):
        ProofAnchor(0, 1000, invalid)
