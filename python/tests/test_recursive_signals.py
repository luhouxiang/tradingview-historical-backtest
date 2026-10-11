from __future__ import annotations

from copy import deepcopy

import pytest
from test_recursive_decomposition import PRICES, SCOPE, lines_for

from tvbt.chan.recursive import RecursiveDecomposition
from tvbt.chan.recursive_proof import StructureProofError
from tvbt.chan.recursive_signals import recursive_signals


def signals(prices: list[int]):
    roots = lines_for(prices)
    cursor = roots[-1].known_at_bar_index
    runtime = RecursiveDecomposition(SCOPE)
    snapshot = runtime.update(roots, {i: i for i in range(cursor + 1)}, cursor=cursor)
    return recursive_signals(runtime, roots, snapshot, cursor, {i: i for i in range(cursor + 1)})


def test_l0_trend_cannot_be_promoted_by_a_bi_center_count() -> None:
    divergences, points = signals([0, 20, 10, 40, 25, 50, 35, 80, 55, 70, 60, 90, 75])
    for _, signal in divergences:
        if signal.divergence_kind == "trend":
            assert signal.status == "candidate"
            assert signal.c_meets_sublevel is False
            assert signal.c_contains_type3 is False
    assert not any(signal.signal_type in {"buy_1", "sell_1"} for _, signal in points)


def test_block_construction_precedes_a_confirmation_and_never_waits_for_strength() -> None:
    weak = [0, 20, 10, 15, 11]
    roots = lines_for(weak)
    runtime = RecursiveDecomposition(SCOPE)
    before = runtime.update(roots[:3], {}, cursor=31)
    assert before["movements"][0]["status"] == "completed"
    assert recursive_signals(runtime, roots[:3], before, 31, {i: i for i in range(32)}) == ([], [])
    after = runtime.update(roots, {}, cursor=41)
    assert after["movements"][0] == before["movements"][0]
    divergences, _ = recursive_signals(runtime, roots, after, 41, {i: i for i in range(42)})
    assert divergences and all(signal.known_at_bar_index == 41 for _, signal in divergences)
    stronger, _ = signals([0, 20, 10, 40, 25])
    assert not stronger


def test_missing_observed_positions_disable_strength_confirmation_not_blocks() -> None:
    roots = lines_for([0, 20, 10, 15, 11])
    runtime = RecursiveDecomposition(SCOPE)
    positions = {i: i for i in range(42)}
    del positions[5]
    snapshot = runtime.update(roots, positions, cursor=41)
    assert snapshot["movements"][0]["status"] == "completed"
    divergences, _ = recursive_signals(runtime, roots, snapshot, 41, positions)
    assert not divergences


def test_initialization_and_small_to_large_have_no_external_signal_origin() -> None:
    for prices in ([0, 20, 10, 15, 11], [0, 20, -5, -1, -2, 40, 0, 30, 25]):
        divergences, points = signals(prices)
        assert all(signal.divergence_kind == "center_oscillation" for _, signal in divergences)
        assert not any(signal.signal_type.endswith("_1") for _, signal in points)


def test_recursive_signal_consumers_preserve_levels_and_real_type3_roles() -> None:
    divergences, points = signals(PRICES)
    assert divergences and points
    assert all(signal.evidence_profile == "fixed_same_level_recursive_v2" for _, signal in points)
    _, trend_points = signals([0, 20, 10, 40, 25, 50, 35, 80, 55, 70, 60, 90, 75])
    thirds = [signal for _, signal in trend_points if signal.signal_type.endswith("_3")]
    assert thirds
    assert all(signal.departure_object_id and signal.return_object_id for signal in thirds)
    assert all(signal.level_id in {"L0", "L1"} for _, signal in divergences)


def test_consumer_rejects_missing_completed_claim_instead_of_trusting_status() -> None:
    roots = lines_for(PRICES)
    runtime = RecursiveDecomposition(SCOPE)
    snapshot = runtime.update(roots, {i: i for i in range(212)}, cursor=211)
    assert runtime.graph is not None
    claim = next(iter(runtime.graph.claims.values()))
    # A genuine source revision invalidates its claim before consumer execution.
    runtime.graph.remove_source(claim.component_refs[0], cursor=212)
    with pytest.raises((KeyError, StructureProofError)):
        recursive_signals(runtime, roots, deepcopy(snapshot), 212)


def test_discovery_stamps_dependency_known_times_and_membership_roles() -> None:
    roots = lines_for(PRICES)
    runtime = RecursiveDecomposition(SCOPE)
    snapshot = runtime.update(roots, {i: i for i in range(301)}, cursor=300, discovery_at=300)
    assert all(row["known_at_bar_index"] == 300 for rows in snapshot.values() for row in rows)
    roles = {row["role"] for row in snapshot["memberships"]}
    assert {"component", "seed", "entry", "completion", "center"} <= roles
    assert runtime.graph is not None
    for claim in runtime.graph.claims.values():
        runtime.graph.verify(claim.fact.ref, cursor=300)
