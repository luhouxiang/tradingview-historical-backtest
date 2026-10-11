from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from itertools import pairwise
from typing import Any, Literal

from tvbt.chan.recursive_graph import RecursiveProofGraph
from tvbt.chan.recursive_proof import (
    MovementClaim,
    ObjectRef,
    ProofAnchor,
    ProofScope,
    RangeFact,
    StructureProofError,
)

CompletionRule = Literal["fixed_same_level_center_block_v2"]
COMPLETION_RULES: tuple[CompletionRule, ...] = ("fixed_same_level_center_block_v2",)


def _identity(prefix: str, *parts: object) -> str:
    encoded = json.dumps(parts, sort_keys=True, separators=(",", ":")).encode()
    return prefix + "-" + hashlib.sha256(encoded).hexdigest()[:20]


@dataclass(frozen=True, slots=True)
class RecursiveLine:
    fact: RangeFact
    direction: Literal["up", "down"]

    @property
    def object_id(self) -> str:
        return self.fact.ref.object_id

    @property
    def start(self) -> ProofAnchor:
        return self.fact.start

    @property
    def end(self) -> ProofAnchor:
        return self.fact.end

    @property
    def known_at_bar_index(self) -> int:
        return self.fact.known_at_bar_index

    @property
    def range_low_i64(self) -> int:
        return self.fact.low_i64

    @property
    def range_high_i64(self) -> int:
        return self.fact.high_i64


@dataclass(frozen=True, slots=True)
class RecursiveCenter:
    object_id: str
    level: int
    base: int
    seed_end: int
    body_end: int
    zd: int
    zg: int
    dd: int
    gg: int
    observed_low: int
    observed_high: int
    formed_at: int
    known_at: int
    exit_index: int | None
    retest_index: int | None


@dataclass(frozen=True, slots=True)
class CompletionDecision:
    floor: int
    end_index: int
    rule: CompletionRule
    centers: tuple[RecursiveCenter, ...]
    witness_indices: tuple[int, ...]
    known_at: int
    context: Literal["fixed_same_level"] = "fixed_same_level"


def _aggregate(lines: Sequence[RecursiveLine], scope: ProofScope, level: int) -> RecursiveLine:
    low = min(lines, key=lambda line: (line.fact.low_i64, line.fact.low_source_bar_index))
    high = max(lines, key=lambda line: (line.fact.high_i64, -line.fact.high_source_bar_index))
    direction: Literal["up", "down"] = (
        "up" if lines[-1].end.price_i64 > lines[0].start.price_i64 else "down"
    )
    return RecursiveLine(
        RangeFact(
            ObjectRef(_identity("recursive-aggregate", [line.object_id for line in lines]), 1),
            scope,
            level,
            "segment" if level == -1 else "movement",
            lines[0].start,
            lines[-1].end,
            low.fact.low_i64,
            high.fact.high_i64,
            low.fact.low_source_bar_index,
            high.fact.high_source_bar_index,
            max(line.known_at_bar_index for line in lines),
        ),
        direction,
    )


def scan_recursive_centers(
    lines: Sequence[RecursiveLine],
    scope: ProofScope,
    level: int,
    *,
    floor: int = 0,
    stop: int | None = None,
) -> tuple[RecursiveCenter, ...]:
    """Frozen three-unit cores; Z envelopes use formation-parity components.

    The connecting exit is not silently made the next center's first seed.
    Scanning resumes at the first completed return, keeping bridge ownership
    explicit. L0 uses confirmed segments; upper layers use verified movements.
    """
    stop = len(lines) if stop is None else stop
    result = []
    scan = floor
    while scan + 3 <= stop:
        seed = None
        for index in range(scan, stop - 2):
            selected = lines[index : index + 3]
            zd = max(line.fact.low_i64 for line in selected)
            zg = min(line.fact.high_i64 for line in selected)
            if zd <= zg:
                seed = (index, zd, zg)
                break
        if seed is None:
            break
        base, zd, zg = seed
        seed_end = base + 2
        exit_index = retest_index = None
        for index in range(seed_end + 1, stop - 1):
            departure, retest = lines[index : index + 2]
            if departure.direction == retest.direction:
                continue
            holds = (
                departure.direction == "up"
                and departure.end.price_i64 > zg
                and retest.fact.low_i64 >= zg
            ) or (
                departure.direction == "down"
                and departure.end.price_i64 < zd
                and retest.fact.high_i64 <= zd
            )
            if holds:
                exit_index, retest_index = index, index + 1
                break
        body_end = stop - 1 if exit_index is None else exit_index - 1
        components = lines[base : body_end + 1]
        z_lines = lines[base : body_end + 1 : 2]
        known_lines = lines[base : (retest_index + 1 if retest_index is not None else stop)]
        encoded = json.dumps(
            [asdict(scope), level, [line.object_id for line in lines[base : base + 3]]],
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        result.append(
            RecursiveCenter(
                "recursive-center-" + hashlib.sha256(encoded).hexdigest()[:20],
                level,
                base,
                seed_end,
                body_end,
                zd,
                zg,
                min(line.fact.low_i64 for line in z_lines),
                max(line.fact.high_i64 for line in z_lines),
                min(line.fact.low_i64 for line in components),
                max(line.fact.high_i64 for line in components),
                max(line.known_at_bar_index for line in lines[base : base + 3]),
                max(line.known_at_bar_index for line in known_lines),
                exit_index,
                retest_index,
            )
        )
        if retest_index is None:
            break
        scan = retest_index
    return tuple(result)


def _relative(first: RecursiveCenter, second: RecursiveCenter) -> Literal["up", "down", "overlap"]:
    if first.level != second.level:
        raise StructureProofError("center comparison requires the same structural level")
    if second.zd > first.zg and second.dd > first.gg:
        return "up"
    if second.zg < first.zd and second.gg < first.dd:
        return "down"
    return "overlap"


def find_movement_completion(
    lines: Sequence[RecursiveLine],
    scope: ProofScope,
    level: int,
    raw_positions: Mapping[int, int],
    *,
    floor: int,
    certified_complete_positions: bool = False,
) -> CompletionDecision | None:
    """Earliest oriented three-type overlap completes a fixed consolidation block.

    Lesson 38 permits consolidation + consolidation and does not extend this
    layer's center. A leading uncentered prefix belongs to this block's entry;
    seeds begin in the block's orientation. No strength/reversal predicate is
    allowed to delay the partition, and no unclassified composite is promoted.
    raw_positions is deliberately irrelevant to structural construction.
    """
    for base in range(floor, len(lines) - 2, 2):
        seed = lines[base : base + 3]
        if not (seed[0].direction != seed[1].direction != seed[2].direction):
            continue
        if max(line.range_low_i64 for line in seed) > min(line.range_high_i64 for line in seed):
            continue
        end = base + 2
        center = scan_recursive_centers(lines, scope, level, floor=base, stop=end + 1)[0]
        witnesses = tuple(range(floor, end + 1))
        return CompletionDecision(
            floor,
            end,
            "fixed_same_level_center_block_v2",
            (center,),
            witnesses,
            max(lines[i].known_at_bar_index for i in witnesses),
        )
    return None


class RecursiveDecomposition:
    """Single dataset fixed-level streams with independent upward recursion."""

    def __init__(self, scope: ProofScope) -> None:
        self.scope = scope
        self._versions: dict[str, tuple[str, int, int]] = {}
        self._active_ids: set[str] = set()
        self.graph: RecursiveProofGraph | None = None
        self._discovery_at: int | None = None

    def _version(self, payload: dict[str, Any], known_at: int) -> dict[str, Any]:
        object_id = str(payload["object_id"])
        semantic = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        previous = self._versions.get(object_id)
        if previous is not None and previous[0] == semantic:
            if "parent_id" not in payload and previous[2] < known_at:
                raise StructureProofError("recursive known_at predates its dependency evidence")
            revision, known_at = previous[1:]
        else:
            revision = 1 if previous is None else previous[1] + 1
            known_at = known_at if previous is None else max(known_at, previous[2])
            if self._discovery_at is not None:
                known_at = max(known_at, self._discovery_at)
            self._versions[object_id] = (semantic, revision, known_at)
        return {**payload, "object_revision": revision, "known_at_bar_index": known_at}

    def _center_payload(
        self,
        center: RecursiveCenter,
        lines: Sequence[RecursiveLine],
        previous: RecursiveCenter | None = None,
    ) -> dict[str, Any]:
        seed = lines[center.base : center.seed_end + 1]
        relation = "unknown" if previous is None else _relative(previous, center)
        review = (
            previous is not None
            and relation == "overlap"
            and (center.zd > previous.zg or center.zg < previous.zd)
        )
        return self._version(
            {
                **asdict(self.scope),
                "object_id": center.object_id,
                "level": center.level,
                "seed_refs": [asdict(line.fact.ref) for line in seed],
                "component_refs": [
                    asdict(line.fact.ref) for line in lines[center.base : center.body_end + 1]
                ],
                "start_bar_index": seed[0].start.bar_index,
                "start_time": seed[0].start.time,
                "end_bar_index": lines[center.body_end].end.bar_index,
                "end_time": lines[center.body_end].end.time,
                "zd_i64": center.zd,
                "zg_i64": center.zg,
                "dd_i64": center.dd,
                "gg_i64": center.gg,
                "observed_low_i64": center.observed_low,
                "observed_high_i64": center.observed_high,
                "formation_dir": seed[0].direction,
                "status": "closed",
                "previous_center_id": None if previous is None else previous.object_id,
                "relative_dir": relation,
                "higher_level_review_required": review,
                "relation_profile": "fixed_seed_zn_same_level_v2",
                "exit_id": None
                if center.exit_index is None
                else lines[center.exit_index].object_id,
                "retest_id": None
                if center.retest_index is None
                else lines[center.retest_index].object_id,
                "exit_ref": None
                if center.exit_index is None
                else asdict(lines[center.exit_index].fact.ref),
                "retest_ref": None
                if center.retest_index is None
                else asdict(lines[center.retest_index].fact.ref),
                "formed_at_bar_index": center.formed_at,
            },
            center.known_at,
        )

    def update(
        self,
        source_lines: Sequence[RecursiveLine],
        raw_positions: Mapping[int, int],
        *,
        cursor: int,
        discovery_at: int | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        if discovery_at is not None and discovery_at != cursor:
            raise StructureProofError("discovery must be stamped at the current cursor")
        self._discovery_at = discovery_at
        for index, line in enumerate(source_lines):
            if (
                line.fact.scope != self.scope
                or line.fact.kind != "segment"
                or line.known_at_bar_index > cursor
            ):
                raise StructureProofError(
                    "recursive root requires currently known same-scope segments"
                )
            if index and (
                source_lines[index - 1].end != line.start
                or source_lines[index - 1].direction == line.direction
            ):
                raise StructureProofError(
                    "recursive root segments must be contiguous and alternating"
                )
        movements: list[dict[str, Any]] = []
        center_rows: dict[str, dict[str, Any]] = {}
        memberships: list[dict[str, Any]] = []
        decisions: dict[str, tuple[CompletionDecision, Sequence[RecursiveLine]]] = {}
        # Validate the observed-position lattice once per update. Certification
        # avoids recreating the entire raw-bar set for every historical prefix.
        # Missing position evidence keeps the detailed strength check enabled.
        observed = set(raw_positions.values())
        positions_complete = bool(observed) and (
            len(observed) == len(raw_positions)
            and max(observed) - min(observed) + 1 == len(observed)
        )

        def completion_verifier(claim: MovementClaim, witnesses: tuple[RangeFact, ...]) -> bool:
            item = decisions.get(claim.fact.ref.object_id)
            if item is None:
                return False
            expected, inputs = item
            rerun = find_movement_completion(
                inputs[: max(expected.witness_indices) + 1],
                self.scope,
                claim.fact.level,
                raw_positions,
                floor=expected.floor,
                certified_complete_positions=positions_complete,
            )
            if rerun != expected:
                raise StructureProofError(
                    f"completion prefix differs at level {claim.fact.level}, "
                    f"floor {expected.floor}: "
                    f"end {expected.end_index}, witness end {max(expected.witness_indices)}, "
                    f"rule {expected.rule}, rerun available {rerun is not None}"
                )
            return (
                rerun == expected
                and claim.completion_rule == expected.rule
                and tuple(fact.ref for fact in witnesses)
                == tuple(inputs[i].fact.ref for i in expected.witness_indices)
                and claim.component_refs
                == tuple(line.fact.ref for line in inputs[expected.floor : expected.end_index + 1])
            )

        graph = RecursiveProofGraph(
            self.scope, {rule: completion_verifier for rule in COMPLETION_RULES}
        )
        for line in source_lines:
            graph.upsert_source(line.fact, cursor=cursor)
        lines = tuple(source_lines)
        level = 0
        while lines:
            floor = 0
            previous_center = None
            completed: list[RecursiveLine] = []
            while floor < len(lines):
                decision = find_movement_completion(
                    lines,
                    self.scope,
                    level,
                    raw_positions,
                    floor=floor,
                    certified_complete_positions=positions_complete,
                )
                end = len(lines) - 1 if decision is None else decision.end_index
                selected = lines[floor : end + 1]
                aggregate = _aggregate(selected, self.scope, level)
                # A pending suffix has no complete fixed-layer block yet.
                centers = () if decision is None else decision.centers
                for center in centers:
                    row = self._center_payload(center, lines, previous_center)
                    previous_center = center
                    center_rows[center.object_id] = row
                    if row["higher_level_review_required"]:
                        previous_row = center_rows[row["previous_center_id"]]
                        memberships.append(
                            self._membership(
                                {
                                    **row,
                                    "known_at_bar_index": max(
                                        row["known_at_bar_index"],
                                        previous_row["known_at_bar_index"],
                                    ),
                                },
                                "center",
                                ObjectRef(
                                    previous_row["object_id"], previous_row["object_revision"]
                                ),
                                "center",
                                "higher_level_review",
                            )
                        )
                    for index in range(floor, center.base):
                        memberships.append(
                            self._membership(
                                row,
                                "center",
                                lines[index].fact.ref,
                                "segment" if level == 0 else "movement",
                                "entry",
                            )
                        )
                    for role, role_index in (
                        ("exit", center.exit_index),
                        ("retest", center.retest_index),
                    ):
                        if role_index is not None:
                            memberships.append(
                                self._membership(
                                    row,
                                    "center",
                                    lines[role_index].fact.ref,
                                    "segment" if level == 0 else "movement",
                                    role,
                                )
                            )
                    if level > 0:
                        parent = graph.derive_center(
                            tuple(
                                lines[i].fact.ref for i in range(center.base, center.seed_end + 1)
                            ),
                            cursor=cursor,
                        )
                        if parent.object_id != center.object_id or (
                            parent.zd_i64,
                            parent.zg_i64,
                        ) != (center.zd, center.zg):
                            raise StructureProofError(
                                "recursive producer differs from parent proof kernel"
                            )
                movement_type = "unknown" if not centers else "consolidation"
                object_id = _identity(
                    "recursive-movement", asdict(self.scope), level, lines[floor].object_id
                )
                refs = (
                    ()
                    if decision is None
                    else tuple(lines[i].fact.ref for i in decision.witness_indices)
                )
                row = self._version(
                    {
                        **asdict(self.scope),
                        "object_id": object_id,
                        "level": level,
                        "construction_kind": "canonical",
                        "promotable": True,
                        "movement_type": movement_type,
                        "direction": lines[floor].direction,
                        "status": "building" if decision is None else "completed",
                        "start_bar_index": aggregate.start.bar_index,
                        "start_time": aggregate.start.time,
                        "start_price_i64": aggregate.start.price_i64,
                        "end_bar_index": aggregate.end.bar_index,
                        "end_time": aggregate.end.time,
                        "end_price_i64": aggregate.end.price_i64,
                        "range_low_i64": aggregate.fact.low_i64,
                        "range_high_i64": aggregate.fact.high_i64,
                        "range_low_source_bar_index": aggregate.fact.low_source_bar_index,
                        "range_high_source_bar_index": aggregate.fact.high_source_bar_index,
                        "component_refs": [asdict(line.fact.ref) for line in selected],
                        "center_ids": [center.object_id for center in centers],
                        "completion_rule": None if decision is None else decision.rule,
                        "completion_context": "none" if decision is None else decision.context,
                        "completion_refs": [asdict(ref) for ref in refs],
                        "comparison_reference_ids": [],
                        "comparison_current_id": None,
                        "reversal_id": None,
                        "strength_evidence": None,
                    },
                    aggregate.known_at_bar_index if decision is None else decision.known_at,
                )
                movements.append(row)
                for line in selected:
                    memberships.append(
                        self._membership(
                            row,
                            "movement",
                            line.fact.ref,
                            "segment" if level == 0 else "movement",
                            "component",
                        )
                    )
                for ref in refs:
                    memberships.append(
                        self._membership(
                            row,
                            "movement",
                            ref,
                            "segment" if level == 0 else "movement",
                            "completion",
                        )
                    )
                for center in centers:
                    center_row = center_rows[center.object_id]
                    memberships.append(
                        self._membership(
                            row,
                            "movement",
                            ObjectRef(center.object_id, int(center_row["object_revision"])),
                            "center",
                            "center",
                        )
                    )
                if decision is None:
                    break
                fact = replace(
                    aggregate.fact,
                    ref=ObjectRef(object_id, int(row["object_revision"])),
                    known_at_bar_index=int(row["known_at_bar_index"]),
                )
                claim = MovementClaim(
                    fact, tuple(line.fact.ref for line in selected), decision.rule, refs
                )
                decisions[object_id] = (decision, lines)
                graph.upsert_movement(claim, cursor=cursor)
                completed.append(RecursiveLine(fact, lines[floor].direction))
                floor = end + 1
            # Every completed movement consumes at least three lower components,
            # so recursion strictly shrinks without a fake layer/depth cap.
            if not completed:
                break
            lines = tuple(completed)
            level += 1
        for row in center_rows.values():
            for ref in row["component_refs"]:
                memberships.append(
                    self._membership(
                        row,
                        "center",
                        ObjectRef(**ref),
                        "segment" if row["level"] == 0 else "movement",
                        "component",
                    )
                )
            for ref in row["seed_refs"]:
                memberships.append(
                    self._membership(
                        row,
                        "center",
                        ObjectRef(**ref),
                        "segment" if row["level"] == 0 else "movement",
                        "seed",
                    )
                )
        # Association views refer to canonical ownership at the SAME level.
        # They are never appended to the parent's completed input stream.
        self._append_associations(movements, center_rows, memberships, graph, cursor)
        self.graph = graph
        if any(
            row["known_at_bar_index"] > cursor
            for row in (*movements, *center_rows.values(), *memberships)
        ):
            raise StructureProofError("recursive snapshot contains future discovery metadata")
        active = {
            str(row["object_id"]) for row in (*movements, *center_rows.values(), *memberships)
        }
        for object_id in self._active_ids - active:
            previous = self._versions[object_id]
            self._versions[object_id] = ("", previous[1] + 1, cursor)
        self._active_ids = active
        return {
            "movements": movements,
            "centers": list(center_rows.values()),
            "memberships": memberships,
        }

    def _append_associations(
        self,
        movements: list[dict[str, Any]],
        centers: dict[str, dict[str, Any]],
        memberships: list[dict[str, Any]],
        graph: RecursiveProofGraph,
        cursor: int,
    ) -> None:
        from tvbt.chan.recursive_association import derive_trend_association

        canonical = {row["object_id"]: row for row in movements if row["status"] == "completed"}
        for level in sorted({row["level"] for row in canonical.values()}):
            ordered = sorted(
                [row for row in canonical.values() if row["level"] == level],
                key=lambda row: row["start_bar_index"],
            )
            for start in range(len(ordered) - 1):
                for stop in range(start + 2, len(ordered) + 1):
                    selected = ordered[start:stop]
                    owned = [centers[row["center_ids"][0]] for row in selected]
                    relations = [
                        "up"
                        if right["zd_i64"] > left["zg_i64"] and right["dd_i64"] > left["gg_i64"]
                        else "down"
                        if right["zg_i64"] < left["zd_i64"] and right["gg_i64"] < left["dd_i64"]
                        else "overlap"
                        for left, right in pairwise(owned)
                    ]
                    if "overlap" in relations or len(set(relations)) != 1:
                        break
                    direction = relations[0]
                    if (
                        direction == "up"
                        and selected[-1]["end_price_i64"] <= selected[0]["start_price_i64"]
                    ) or (
                        direction == "down"
                        and selected[-1]["end_price_i64"] >= selected[0]["start_price_i64"]
                    ):
                        continue
                    refs = tuple(
                        ObjectRef(row["object_id"], row["object_revision"]) for row in selected
                    )
                    view = derive_trend_association(refs, graph, canonical, centers, cursor=cursor)
                    row = self._version(
                        {
                            **{
                                key: value
                                for key, value in selected[0].items()
                                if key not in {"object_revision", "known_at_bar_index"}
                            },
                            "object_id": view.fact.ref.object_id,
                            "construction_kind": "association",
                            "promotable": False,
                            "movement_type": "uptrend" if view.direction == "up" else "downtrend",
                            "direction": view.direction,
                            "start_bar_index": view.fact.start.bar_index,
                            "start_time": view.fact.start.time,
                            "start_price_i64": view.fact.start.price_i64,
                            "end_bar_index": view.fact.end.bar_index,
                            "end_time": view.fact.end.time,
                            "end_price_i64": view.fact.end.price_i64,
                            "range_low_i64": view.fact.low_i64,
                            "range_high_i64": view.fact.high_i64,
                            "range_low_source_bar_index": view.fact.low_source_bar_index,
                            "range_high_source_bar_index": view.fact.high_source_bar_index,
                            "component_refs": [asdict(ref) for ref in view.member_refs],
                            "completion_refs": [asdict(ref) for ref in view.member_refs],
                            "center_ids": [ref.object_id for ref in view.center_refs],
                            "center_refs": [asdict(ref) for ref in view.center_refs],
                            "completion_rule": view.construction_rule,
                            "completion_context": "same_level_association",
                        },
                        view.fact.known_at_bar_index,
                    )
                    # Publication metadata is derived separately from semantics.
                    movements.append(row)
                    for ref in view.member_refs:
                        memberships.append(
                            self._membership(row, "movement", ref, "movement", "association")
                        )
                    for ref in view.center_refs:
                        memberships.append(
                            self._membership(row, "movement", ref, "center", "center")
                        )

    def _membership(
        self,
        parent: dict[str, Any],
        parent_kind: str,
        ref: ObjectRef,
        child_kind: str,
        role: str,
    ) -> dict[str, Any]:
        return self._version(
            {
                "object_id": _identity(
                    "recursive-membership", parent["object_id"], ref.object_id, role
                ),
                "parent_id": parent["object_id"],
                "parent_kind": parent_kind,
                "child_ref": asdict(ref),
                "child_kind": child_kind,
                "role": role,
                "level": parent["level"],
            },
            int(parent["known_at_bar_index"]),
        )

    def state(self) -> dict[str, Any]:
        return {
            "scope": asdict(self.scope),
            "versions": self._versions,
            "active_ids": sorted(self._active_ids),
        }

    @classmethod
    def from_state(cls, state: dict[str, Any], scope: ProofScope) -> RecursiveDecomposition:
        if state.get("scope") != asdict(scope):
            raise StructureProofError("recursive runtime checkpoint identity differs")
        result = cls(scope)
        for key, value in state["versions"].items():
            if not isinstance(value, (list, tuple)) or len(value) != 3:
                raise StructureProofError("invalid recursive revision checkpoint")
            semantic, revision, known_at = value
            if not isinstance(semantic, str) or not isinstance(known_at, int) or known_at < 0:
                raise StructureProofError("invalid recursive revision checkpoint fields")
            ObjectRef(key, revision)
            result._versions[key] = (semantic, revision, known_at)
        result._active_ids = set(state["active_ids"])
        if not result._active_ids.issubset(result._versions):
            raise StructureProofError("active recursive checkpoint object lacks revision evidence")
        return result
