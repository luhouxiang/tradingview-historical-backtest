from __future__ import annotations

import random
from copy import deepcopy

import pytest
from test_recursive_decomposition import PRICES, calculate

from tvbt.chan.recursive_association import derive_trend_association
from tvbt.chan.recursive_proof import ObjectRef, StructureProofError
from tvbt.chan.recursive_signals import _analysis_views, recursive_signals

PRICES_TREND = [0, 20, -5, -1, -2, 10, 0, 30, 25, 50, 35, 45, 40]


def standard_prices() -> list[int]:
    rng = random.Random(10)
    prices = [0]
    for index in range(232):
        prices.append(prices[-1] + rng.randint(1, 100) * (1 if index % 2 == 0 else -1))
    return [*prices, 501, 465, 470, 460, 468, 465, 490, 480, 486, 482]


def evaluate(prices: list[int]):
    from test_recursive_decomposition import lines_for

    runtime, snapshot = calculate(prices)
    cursor = (len(prices) - 1) * 10 + 1
    return snapshot, recursive_signals(
        runtime, lines_for(prices), snapshot, cursor, {i: i for i in range(cursor + 1)}
    )


def test_registered_proofs_confirm_actual_composite_c_and_standard_first_point() -> None:
    snapshot, (divergences, points) = evaluate(standard_prices())
    _, signal = next(item for item in divergences if item[1].divergence_profile == "standard_trend")
    assert signal.level_id == "L1" and signal.signal_type == "bottom_divergence"
    assert (signal.bar_index, signal.time, signal.price_i64, signal.known_at_bar_index) == (
        2360,
        237000,
        460,
        2421,
    )
    assert signal.c_meets_sublevel is True and signal.c_contains_type3 is True
    assert len(signal.c_sublevel_center_ids) == 2
    c = next(
        row
        for row in snapshot["movements"]
        if row["object_id"] == signal.comparison_current_object_id
    )
    assert c["construction_kind"] == "association" and c["promotable"] is False
    assert (signal.c_type3_departure_id, signal.c_type3_retest_id) == tuple(
        ref["object_id"] for ref in c["component_refs"]
    )
    members = {row["object_id"]: row for row in snapshot["movements"]}
    assert all(members[ref["object_id"]]["level"] == 0 for ref in c["component_refs"])
    first = next(value for _, value in points if value.signal_type == "buy_1")
    assert (first.bar_index, first.known_at_bar_index) == (2360, 2421)
    internal_third = next(
        value
        for _, value in points
        if value.signal_type == "sell_3" and value.reference_object_id == signal.b_center_id
    )
    assert internal_third.level_id == "L1"
    assert internal_third.departure_object_id == signal.c_type3_departure_id
    assert internal_third.return_object_id == signal.c_type3_retest_id
    assert (
        internal_third.bar_index,
        internal_third.price_i64,
        internal_third.known_at_bar_index,
    ) == (2330, 501, 2371)
    assert not any(
        c["object_id"] == ref["object_id"]
        for center in snapshot["centers"]
        for ref in center["seed_refs"]
    )


def test_standard_composite_price_mirror_confirms_sell_1() -> None:
    _, (divergences, points) = evaluate([-value for value in standard_prices()])
    signal = next(value for _, value in divergences if value.divergence_profile == "standard_trend")
    assert signal.signal_type == "top_divergence" and signal.known_at_bar_index == 2421
    assert any(value.signal_type == "sell_1" for _, value in points)


def test_composite_structure_is_known_before_first_reverse_confirmation() -> None:
    prices = standard_prices()
    for end in (237, 238, 239, 240, 241):
        snapshot, (divergences, points) = evaluate(prices[: end + 1])
        assert any(
            row["construction_kind"] == "association" and row["start_bar_index"] == 2290
            for row in snapshot["movements"]
        )
        assert not any(signal.divergence_profile == "standard_trend" for _, signal in divergences)
        assert not any(signal.signal_type in {"buy_1", "sell_1"} for _, signal in points)


def test_later_successful_return_cannot_repair_first_failed_internal_type3() -> None:
    prices = standard_prices()
    prices[233] = 550
    _, (divergences, points) = evaluate(prices)
    signal = next(value for _, value in divergences if value.divergence_kind == "trend")
    assert signal.c_meets_sublevel is True and signal.c_contains_type3 is False
    assert signal.status == "candidate"
    assert not any(value.signal_type in {"buy_1", "sell_1"} for _, value in points)


def test_finer_atom_return_does_not_replace_b_actual_sublevel_return() -> None:
    prices = standard_prices()
    prices[231] = 550
    _, (divergences, points) = evaluate(prices)
    assert any(value.divergence_profile == "standard_trend" for _, value in divergences)
    assert any(value.signal_type == "buy_1" for _, value in points)


def test_reverse_type_with_a_new_low_cannot_confirm_the_old_first_point() -> None:
    prices = standard_prices()
    prices[238] = 450
    _, (divergences, points) = evaluate(prices)
    signal = next(value for _, value in divergences if value.divergence_kind == "trend")
    assert signal.c_meets_sublevel is True and signal.c_contains_type3 is True
    assert signal.status == "candidate"
    assert not any(value.signal_type in {"buy_1", "sell_1"} for _, value in points)


def test_strong_composite_keeps_its_structure_without_a_signal() -> None:
    prices = [*standard_prices()[:233], 501, 300, 310, 100, 110, 90, 100, 90, 120, 110, 116, 112]
    snapshot, (divergences, points) = evaluate(prices)
    assert any(
        row["construction_kind"] == "association" and row["start_bar_index"] == 2290
        for row in snapshot["movements"]
    )
    assert not any(value.divergence_profile == "standard_trend" for _, value in divergences)
    assert not any(value.signal_type in {"buy_1", "sell_1"} for _, value in points)


def fixture(prices=PRICES_TREND):
    runtime, snapshot = calculate(prices)
    members = [
        row
        for row in snapshot["movements"]
        if row["level"] == 0
        and row["status"] == "completed"
        and row["construction_kind"] == "canonical"
    ]
    return (
        runtime,
        snapshot,
        tuple(ObjectRef(row["object_id"], row["object_revision"]) for row in members),
    )


def test_real_canonical_blocks_prove_nonpromotable_same_level_trend_view() -> None:
    runtime, snapshot, refs = fixture()
    before = deepcopy(runtime.state())
    assert runtime.graph is not None
    view = derive_trend_association(
        refs,
        runtime.graph,
        {row["object_id"]: row for row in snapshot["movements"]},
        {row["object_id"]: row for row in snapshot["centers"]},
        cursor=121,
    )
    assert view.direction == "up" and view.fact.level == 0
    assert (view.fact.start.bar_index, view.fact.end.bar_index, view.fact.known_at_bar_index) == (
        0,
        120,
        121,
    )
    assert (view.fact.low_i64, view.fact.high_i64) == (-5, 50)
    assert view.promotable is False and len(view.center_refs) == 2
    assert runtime.state() == before
    assert view.fact.ref.object_id not in runtime.graph.claims
    with pytest.raises(StructureProofError, match="verified movement claim"):
        runtime.graph.upsert_source(view.fact, cursor=121)
    with pytest.raises(StructureProofError):
        runtime.graph.derive_center((view.fact.ref, *refs), cursor=121)


def test_price_mirror_proves_corresponding_downtrend() -> None:
    runtime, snapshot, refs = fixture([-value for value in PRICES_TREND])
    assert runtime.graph is not None
    view = derive_trend_association(
        refs,
        runtime.graph,
        {row["object_id"]: row for row in snapshot["movements"]},
        {row["object_id"]: row for row in snapshot["centers"]},
        cursor=121,
    )
    assert view.direction == "down"
    assert (view.fact.low_i64, view.fact.high_i64) == (-50, 5)


def test_production_view_is_published_with_references_but_not_parent_ownership() -> None:
    runtime, snapshot, refs = fixture()
    rows = {row["object_id"]: row for row in snapshot["movements"]}
    view = next(row for row in rows.values() if row["construction_kind"] == "association")
    assert view["promotable"] is False and view["movement_type"] == "uptrend"
    assert view["component_refs"] == [
        {"object_id": ref.object_id, "object_revision": ref.object_revision} for ref in refs
    ]
    assert runtime.graph is not None
    assert view["object_id"] not in runtime.graph.claims
    assert all(
        ref["object_id"] != view["object_id"]
        for center in snapshot["centers"]
        for ref in center["seed_refs"]
    )
    assert all(
        edge["role"] in {"association", "center"}
        for edge in snapshot["memberships"]
        if edge["parent_id"] == view["object_id"]
    )
    from tvbt.chan.recursive import RecursiveLine

    lines = [
        RecursiveLine(runtime.graph.claims[ref.object_id].fact, rows[ref.object_id]["direction"])
        for ref in refs
    ]
    variants = list(_analysis_views(runtime, {1: lines}, rows, snapshot["centers"], 121))
    grouped = next(item for item in variants if item[3] == view["object_id"])
    assert len(grouped[1]) == 1 and grouped[1][0].direction == "up"


def test_consumer_rejects_tampered_association_publication() -> None:
    from test_recursive_decomposition import lines_for

    runtime, snapshot, _ = fixture()
    view = next(row for row in snapshot["movements"] if row["construction_kind"] == "association")
    view["range_high_i64"] += 1
    with pytest.raises(StructureProofError, match="current source evidence"):
        recursive_signals(
            runtime, lines_for(PRICES_TREND), snapshot, 121, {i: i for i in range(122)}
        )


@pytest.mark.parametrize(
    "change", ["unclassified", "fake_core", "foreign", "stale_seed", "unowned_seed", "future"]
)
def test_association_rejects_unproved_geometry_or_membership(change: str) -> None:
    runtime, snapshot, refs = fixture()
    movements = {row["object_id"]: deepcopy(row) for row in snapshot["movements"]}
    centers = {row["object_id"]: deepcopy(row) for row in snapshot["centers"]}
    first = movements[refs[0].object_id]
    center = centers[first["center_ids"][0]]
    if change == "unclassified":
        first["movement_type"] = "unknown"
    elif change == "fake_core":
        center["zg_i64"] += 1
    elif change == "foreign":
        center["data_revision"] = "sha256:" + "a" * 64
    elif change == "stale_seed":
        center["seed_refs"][0]["object_revision"] += 1
    elif change == "unowned_seed":
        center["seed_refs"][0] = deepcopy(movements[refs[1].object_id]["component_refs"][0])
    else:
        center["known_at_bar_index"] = 122
    assert runtime.graph is not None
    with pytest.raises(StructureProofError):
        derive_trend_association(refs, runtime.graph, movements, centers, cursor=121)


def test_overlap_cannot_be_renamed_trend_and_reverse_order_cannot_combine() -> None:
    runtime, snapshot, refs = fixture(PRICES)
    assert runtime.graph is not None
    with pytest.raises(StructureProofError, match="same-level trend"):
        derive_trend_association(
            refs[:2],
            runtime.graph,
            {row["object_id"]: row for row in snapshot["movements"]},
            {row["object_id"]: row for row in snapshot["centers"]},
            cursor=211,
        )
    runtime, snapshot, refs = fixture()
    assert runtime.graph is not None
    with pytest.raises(StructureProofError, match="continuous anchors"):
        derive_trend_association(
            refs[::-1],
            runtime.graph,
            {row["object_id"]: row for row in snapshot["movements"]},
            {row["object_id"]: row for row in snapshot["centers"]},
            cursor=121,
        )
