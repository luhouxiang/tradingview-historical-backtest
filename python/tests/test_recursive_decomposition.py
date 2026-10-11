from __future__ import annotations

import json
import random
from dataclasses import replace
from itertools import pairwise

import pytest

from tvbt.chan.recursive import RecursiveDecomposition, RecursiveLine
from tvbt.chan.recursive_proof import (
    ObjectRef,
    ProofAnchor,
    ProofScope,
    RangeFact,
    StructureProofError,
)

SCOPE = ProofScope("synthetic-recursive", "sha256:" + "c" * 64, "fixed-same-level-full")
PRICES = [0, 20, -5, -1, -2, 10, 0, 5, 1, 3, -5, 15, -1, 3, 1, 4, -2, 1, -1, 0, -4, 0]


def lines_for(prices: list[int]) -> tuple[RecursiveLine, ...]:
    anchors = [ProofAnchor(i * 10, (i + 1) * 1000, price) for i, price in enumerate(prices)]
    result = []
    for index in range(len(prices) - 1):
        start, end = anchors[index : index + 2]
        low, high = sorted((start, end), key=lambda anchor: anchor.price_i64)
        result.append(
            RecursiveLine(
                RangeFact(
                    ObjectRef(f"segment-{index}", 1),
                    SCOPE,
                    -1,
                    "segment",
                    start,
                    end,
                    low.price_i64,
                    high.price_i64,
                    low.bar_index,
                    high.bar_index,
                    end.bar_index + 1,
                ),
                "up" if end.price_i64 > start.price_i64 else "down",
            )
        )
    return tuple(result)


def calculate(prices: list[int]):
    lines = lines_for(prices)
    cursor = lines[-1].known_at_bar_index
    runtime = RecursiveDecomposition(SCOPE)
    return runtime, runtime.update(lines, {i: i for i in range(cursor + 1)}, cursor=cursor)


def test_three_complete_overlapping_units_construct_block_without_strength_or_reverse() -> None:
    _, before = calculate([0, 20, 10])
    assert not any(row["status"] == "completed" for row in before["movements"])
    _, after = calculate([0, 20, 10, 15])
    first = after["movements"][0]
    assert (first["end_bar_index"], first["known_at_bar_index"]) == (30, 31)
    assert first["status"] == "completed"
    assert first["completion_rule"] == "fixed_same_level_center_block_v2"
    assert first["strength_evidence"] is None and first["reversal_id"] is None


def test_six_unit_extension_is_two_consolidations_not_unknown_composite() -> None:
    _, result = calculate([0, 20, 10, 15, 11, 14, 12])
    rows = [row for row in result["movements"] if row["level"] == 0]
    assert [(row["start_bar_index"], row["end_bar_index"]) for row in rows] == [(0, 30), (30, 60)]
    assert all(
        row["movement_type"] == "consolidation" and row["status"] == "completed" for row in rows
    )
    assert not any(row["level"] > 0 for row in result["centers"])


def test_nonoverlapping_prefix_stays_building_without_complete_center() -> None:
    _, result = calculate([0, 20, -5, -1, -10, -6])
    assert result["centers"] == []
    assert all(
        row["status"] == "building" and row["movement_type"] == "unknown"
        for row in result["movements"]
    )


def test_entry_prefix_is_owned_once_and_not_in_core_seeds() -> None:
    _, result = calculate(PRICES[:6])
    first, center = result["movements"][0], result["centers"][0]
    assert len(first["component_refs"]) == 5
    assert [ref["object_id"] for ref in center["seed_refs"]] == [
        "segment-2",
        "segment-3",
        "segment-4",
    ]
    assert (center["zd_i64"], center["zg_i64"]) == (-2, -1)
    assert {
        edge["child_ref"]["object_id"]
        for edge in result["memberships"]
        if edge["parent_id"] == center["object_id"] and edge["role"] == "entry"
    } == {"segment-0", "segment-1"}


def test_parent_core_uses_three_complete_actual_ranges_not_child_cores() -> None:
    runtime, result = calculate(PRICES)
    lower = [
        row for row in result["movements"] if row["level"] == 0 and row["status"] == "completed"
    ]
    assert [(row["start_bar_index"], row["end_bar_index"]) for row in lower[:3]] == [
        (0, 50),
        (50, 80),
        (80, 110),
    ]
    parent = next(row for row in result["centers"] if row["level"] == 1)
    assert (parent["zd_i64"], parent["zg_i64"], parent["formed_at_bar_index"]) == (0, 10, 111)
    assert [ref["object_id"] for ref in parent["seed_refs"]] == [
        row["object_id"] for row in lower[:3]
    ]
    assert parent["zd_i64"] == max(row["range_low_i64"] for row in lower[:3])
    assert parent["zg_i64"] == min(row["range_high_i64"] for row in lower[:3])
    assert runtime.graph is not None and parent["object_id"] in runtime.graph.centers


def test_strength_and_raw_position_lattice_do_not_change_structural_partition() -> None:
    lines = lines_for(PRICES[:7])
    positions = {i: i for i in range(62)}
    del positions[5]
    result = RecursiveDecomposition(SCOPE).update(lines, positions, cursor=61)
    _, complete = calculate(PRICES[:7])
    assert result == complete


def test_every_prefix_preserves_previously_completed_blocks_and_cores() -> None:
    roots = lines_for(PRICES)
    runtime = RecursiveDecomposition(SCOPE)
    frozen = {}
    cores = {}
    for count in range(1, len(roots) + 1):
        cursor = roots[count - 1].known_at_bar_index
        result = runtime.update(roots[:count], {}, cursor=cursor)
        current = {row["object_id"]: row for row in result["movements"]}
        assert all(current[key] == value for key, value in frozen.items())
        frozen.update({key: row for key, row in current.items() if row["status"] == "completed"})
        current_cores = {row["object_id"]: row for row in result["centers"]}
        assert all(current_cores[key] == value for key, value in cores.items())
        cores.update(current_cores)


def test_no_parent_until_third_complete_block() -> None:
    _, before = calculate(PRICES[:11])
    assert not any(row["level"] > 0 for row in before["centers"])
    _, after = calculate(PRICES[:12])
    assert [row["level"] for row in after["centers"]].count(1) == 1


def test_checkpoint_restoration_preserves_future_revisions_and_known_times() -> None:
    roots = lines_for(PRICES)
    runtime = RecursiveDecomposition(SCOPE)
    for count in range(1, 12):
        runtime.update(roots[:count], {}, cursor=roots[count - 1].known_at_bar_index)
    restored = RecursiveDecomposition.from_state(json.loads(json.dumps(runtime.state())), SCOPE)
    for count in range(12, len(roots) + 1):
        cursor = roots[count - 1].known_at_bar_index
        assert runtime.update(roots[:count], {}, cursor=cursor) == restored.update(
            roots[:count], {}, cursor=cursor
        )


def test_components_partition_each_level_and_parent_never_removes_lower_blocks() -> None:
    _, result = calculate(PRICES)
    for level in {row["level"] for row in result["movements"]}:
        seen = set()
        rows = [row for row in result["movements"] if row["level"] == level]
        for row in rows:
            ids = {ref["object_id"] for ref in row["component_refs"]}
            assert not ids.intersection(seen)
            seen.update(ids)
        assert all(
            left["end_bar_index"] == right["start_bar_index"] for left, right in pairwise(rows)
        )
    assert len([row for row in result["movements"] if row["level"] == 0]) == 7


def test_l2_uses_registered_classified_complete_l1_inputs() -> None:
    rng = random.Random(3)
    prices = [0]
    for index in range(600):
        prices.append(prices[-1] + rng.randint(1, 100) * (1 if index % 2 == 0 else -1))
    runtime, snapshot = calculate(prices)
    parent = next(row for row in snapshot["centers"] if row["level"] == 2)
    by_id = {row["object_id"]: row for row in snapshot["movements"]}
    seeds = [by_id[ref["object_id"]] for ref in parent["seed_refs"]]
    assert all(
        row["level"] == 1
        and row["status"] == "completed"
        and row["movement_type"] == "consolidation"
        for row in seeds
    )
    assert parent["zd_i64"] == max(row["range_low_i64"] for row in seeds)
    assert parent["zg_i64"] == min(row["range_high_i64"] for row in seeds)
    assert runtime.graph is not None
    for claim in runtime.graph.claims.values():
        runtime.graph.verify(claim.fact.ref, cursor=6001)


def test_price_mirror_preserves_partition_and_mirrors_parent_core() -> None:
    _, result = calculate([-price for price in PRICES])
    parent = next(row for row in result["centers"] if row["level"] == 1)
    assert (parent["zd_i64"], parent["zg_i64"]) == (-10, 0)
    assert result["movements"][0]["direction"] == "down"


def test_root_rejects_future_cross_scope_and_noncontinuous_objects() -> None:
    roots = lines_for(PRICES[:7])
    with pytest.raises(StructureProofError, match="currently known"):
        RecursiveDecomposition(SCOPE).update(roots, {}, cursor=60)
    wrong = replace(roots[0], fact=replace(roots[0].fact, scope=replace(SCOPE, dataset_id="other")))
    with pytest.raises(StructureProofError, match="same-scope"):
        RecursiveDecomposition(SCOPE).update((wrong, *roots[1:]), {}, cursor=61)
    with pytest.raises(StructureProofError, match="contiguous"):
        RecursiveDecomposition(SCOPE).update((roots[0], *roots[2:]), {}, cursor=61)


def test_overlapping_envelopes_only_request_higher_review_without_merging() -> None:
    _, result = calculate([0, 20, -5, -1, -2, 40, 0, 30, 25])
    assert all(
        row["completion_rule"] in {None, "fixed_same_level_center_block_v2"}
        for row in result["movements"]
    )
    for row in result["centers"]:
        if row["higher_level_review_required"]:
            assert row["relative_dir"] == "overlap" and row["previous_center_id"]
    reviews = [row for row in result["memberships"] if row["role"] == "higher_level_review"]
    assert len(reviews) == 1
    assert reviews[0]["level"] == 0 and reviews[0]["known_at_bar_index"] == 81
    assert not any(row["level"] > 0 for row in result["centers"])


def test_v1_checkpoint_is_not_silently_restored_as_v2() -> None:
    runtime, _ = calculate(PRICES)
    state = runtime.state()
    state["scope"]["rule_version"] = "fixed_same_level_recursive_v1"
    with pytest.raises(StructureProofError, match="identity differs"):
        RecursiveDecomposition.from_state(state, SCOPE)
