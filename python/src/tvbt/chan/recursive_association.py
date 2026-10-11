"""Prove same-level trend views without changing canonical ownership or promotion."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from itertools import pairwise
from typing import Any, Literal

from tvbt.chan.recursive import _identity
from tvbt.chan.recursive_graph import RecursiveProofGraph
from tvbt.chan.recursive_proof import ObjectRef, RangeFact, StructureProofError


@dataclass(frozen=True, slots=True)
class TrendAssociation:
    """An alternative analytical grouping, never a canonical parent seed."""

    fact: RangeFact
    direction: Literal["up", "down"]
    member_refs: tuple[ObjectRef, ...]
    center_refs: tuple[ObjectRef, ...]
    construction_rule: Literal["same_level_trend_association_v1"] = (
        "same_level_trend_association_v1"
    )
    promotable: Literal[False] = False


def derive_trend_association(
    member_refs: Sequence[ObjectRef],
    graph: RecursiveProofGraph,
    movements: Mapping[str, Mapping[str, Any]],
    centers: Mapping[str, Mapping[str, Any]],
    *,
    cursor: int,
) -> TrendAssociation:
    """Verify every canonical claim and its actual owned center before grouping.

    Strictly ordered nonoverlapping cores AND Zn envelopes determine a trend;
    perimeter overlap only supports review, never this proof. This analytical
    view shares no duration with another canonical owner because it allocates
    no ownership at all: member_refs explicitly reference existing ownership.
    """
    if len(member_refs) < 2 or len({ref.object_id for ref in member_refs}) != len(member_refs):
        raise StructureProofError("association requires distinct complete canonical members")
    facts: list[RangeFact] = []
    owned: list[Mapping[str, Any]] = []
    for ref in member_refs:
        graph.verify(ref, cursor=cursor)
        claim = graph.claims[ref.object_id]
        row = movements.get(ref.object_id)
        if (
            row is None
            or row.get("status") != "completed"
            or row.get("movement_type") != "consolidation"
            or claim.completion_rule != "fixed_same_level_center_block_v2"
            or row.get("object_revision") != ref.object_revision
            or row.get("component_refs") != [asdict(item) for item in claim.component_refs]
            or row.get("level") != claim.fact.level
        ):
            raise StructureProofError("association input is not the current canonical proof")
        ids = row.get("center_ids", [])
        if len(ids) != 1 or ids[0] not in centers:
            raise StructureProofError("association requires each member's uniquely owned center")
        center = centers[ids[0]]
        seeds = tuple(ObjectRef(**item) for item in center["seed_refs"])
        expected_center_id = _identity(
            "recursive-center",
            asdict(graph.scope),
            claim.fact.level,
            [ref.object_id for ref in claim.component_refs[-3:]],
        )
        if (
            seeds != claim.component_refs[-3:]
            or center["object_id"] != expected_center_id
            or center["component_refs"] != [asdict(ref) for ref in seeds]
        ):
            raise StructureProofError("association center seeds are not owned by their member")
        seed_facts = []
        for seed in seeds:
            fact = graph.facts.get(seed.object_id)
            if fact is None or fact.ref != seed or fact.known_at_bar_index > cursor:
                raise StructureProofError("association center uses stale or future seed evidence")
            seed_facts.append(fact)
        if any(left.end != right.start for left, right in pairwise(seed_facts)):
            raise StructureProofError("association center seeds are not continuous")
        if (
            center["level"] != claim.fact.level
            or center["rule_version"] != graph.scope.rule_version
            or center["dataset_id"] != graph.scope.dataset_id
            or center["data_revision"] != graph.scope.data_revision
            or center["decomposition_id"] != graph.scope.decomposition_id
            or center["known_at_bar_index"] > cursor
            or center["known_at_bar_index"] < max(item.known_at_bar_index for item in seed_facts)
            or center["start_bar_index"] != seed_facts[0].start.bar_index
            or center["end_bar_index"] != seed_facts[-1].end.bar_index
            or center["zd_i64"] != max(item.low_i64 for item in seed_facts)
            or center["zg_i64"] != min(item.high_i64 for item in seed_facts)
            or center["dd_i64"] != min(item.low_i64 for item in seed_facts[::2])
            or center["gg_i64"] != max(item.high_i64 for item in seed_facts[::2])
        ):
            raise StructureProofError("association center geometry differs from its seed proof")
        facts.append(claim.fact)
        owned.append(center)
    if any(item.scope != graph.scope or item.level != facts[0].level for item in facts):
        raise StructureProofError(
            "association cannot combine different structural levels or identities"
        )
    if any(left.end != right.start for left, right in pairwise(facts)):
        raise StructureProofError("association members must share continuous anchors")
    components = [
        ref for member in member_refs for ref in graph.claims[member.object_id].component_refs
    ]
    if len(set(components)) != len(components):
        raise StructureProofError("association members duplicate canonical ownership")
    upward = all(
        right["zd_i64"] > left["zg_i64"] and right["dd_i64"] > left["gg_i64"]
        for left, right in pairwise(owned)
    )
    downward = all(
        right["zg_i64"] < left["zd_i64"] and right["gg_i64"] < left["dd_i64"]
        for left, right in pairwise(owned)
    )
    if not upward and not downward:
        raise StructureProofError("association centers do not prove a same-level trend")
    direction: Literal["up", "down"] = "up" if upward else "down"
    if (direction == "up" and facts[-1].end.price_i64 <= facts[0].start.price_i64) or (
        direction == "down" and facts[-1].end.price_i64 >= facts[0].start.price_i64
    ):
        raise StructureProofError("association endpoints contradict its trend direction")
    low = min(facts, key=lambda item: (item.low_i64, item.low_source_bar_index))
    high = max(facts, key=lambda item: (item.high_i64, -item.high_source_bar_index))
    return TrendAssociation(
        RangeFact(
            ObjectRef(
                _identity(
                    "recursive-association",
                    asdict(graph.scope),
                    facts[0].level,
                    [asdict(ref) for ref in member_refs],
                ),
                1,
            ),
            graph.scope,
            facts[0].level,
            "movement",
            facts[0].start,
            facts[-1].end,
            low.low_i64,
            high.high_i64,
            low.low_source_bar_index,
            high.high_source_bar_index,
            max(
                *(fact.known_at_bar_index for fact in facts),
                *(item["known_at_bar_index"] for item in owned),
            ),
        ),
        direction,
        tuple(member_refs),
        tuple(ObjectRef(item["object_id"], item["object_revision"]) for item in owned),
    )
