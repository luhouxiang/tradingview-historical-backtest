from __future__ import annotations

import json
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any, Literal

from tvbt.chan.recursive_proof import (
    CompletionVerifier,
    MovementClaim,
    ObjectRef,
    ProofAnchor,
    ProofScope,
    RangeFact,
    RecursiveCenterProof,
    StructureProofError,
    derive_parent_center,
    verify_movement,
)

STATE_FORMAT = "tvbt-recursive-proof-state-v1"


@dataclass(frozen=True, slots=True)
class ProofInvalidation:
    object_id: str
    object_kind: Literal["movement", "center"]
    known_at_bar_index: int
    reason: Literal["source_revised", "source_removed"]


class RecursiveProofGraph:
    """Maintain causal dependencies independently of the chosen completion predicate.

    Source revisions invalidate their dependent movements and parent cores at the
    revision cursor. Checkpoints persist claims and source facts, not a trusted
    completed flag or a saved dependency graph. Restoring rebuilds and verifies
    those dependencies with the caller's explicitly registered completion rules.
    """

    def __init__(
        self, scope: ProofScope, completion_verifiers: Mapping[str, CompletionVerifier]
    ) -> None:
        self.scope = scope
        self._verifiers = dict(completion_verifiers)
        self._cursor = 0
        self._facts: dict[str, RangeFact] = {}
        self._claims: dict[str, MovementClaim] = {}
        self._centers: dict[str, RecursiveCenterProof] = {}
        self._dependencies: dict[str, set[str]] = {}
        self._dependants: dict[str, set[str]] = {}
        self._revision_floors: dict[str, int] = {}
        self.invalidations: list[ProofInvalidation] = []

    def _check_cursor(self, cursor: int) -> None:
        if (
            isinstance(cursor, bool)
            or not isinstance(cursor, int)
            or not self._cursor <= cursor < 2**63
        ):
            raise StructureProofError("proof graph cannot mutate an earlier causal prefix")

    def _check_fact(self, fact: RangeFact, cursor: int) -> None:
        self._check_cursor(cursor)
        if fact.scope != self.scope or fact.known_at_bar_index > cursor:
            raise StructureProofError("source identity or causal availability differs from graph")
        object_id = fact.ref.object_id
        previous = self._facts.get(object_id)
        floor = self._revision_floors.get(object_id, 0)
        if previous == fact:
            return
        if object_id in self._centers:
            raise StructureProofError("source identity collides with a parent center")
        if fact.ref.object_revision <= floor:
            raise StructureProofError("changed or resurrected fact requires a newer revision")
        if floor and fact.known_at_bar_index != cursor:
            raise StructureProofError("a source correction cannot backdate its known time")
        if previous is not None and previous.kind != fact.kind:
            raise StructureProofError("source kind cannot change under the same identity")

    def _descendants(self, object_id: str) -> list[str]:
        queue = deque(sorted(self._dependants.get(object_id, ())))
        seen: set[str] = set()
        ordered: list[str] = []
        while queue:
            child = queue.popleft()
            if child in seen:
                continue
            seen.add(child)
            ordered.append(child)
            queue.extend(sorted(self._dependants.get(child, ())))
        return ordered

    def _unlink(self, object_id: str) -> None:
        for dependency in self._dependencies.pop(object_id, ()):
            self._dependants.get(dependency, set()).discard(object_id)
        self._dependants.pop(object_id, None)

    def _invalidate(
        self, object_id: str, cursor: int, reason: Literal["source_revised", "source_removed"]
    ) -> None:
        # Gather the closure before removing edges; otherwise a direct child
        # deletion would hide its higher-layer dependants from traversal.
        for child in self._descendants(object_id):
            kind: Literal["movement", "center"]
            if child in self._centers:
                kind = "center"
                del self._centers[child]
            else:
                kind = "movement"
                self._claims.pop(child, None)
                self._facts.pop(child, None)
            self._unlink(child)
            self.invalidations.append(ProofInvalidation(child, kind, cursor, reason))

    def _link(self, object_id: str, dependencies: Sequence[str]) -> None:
        self._unlink(object_id)
        self._dependencies[object_id] = set(dependencies)
        for dependency in dependencies:
            self._dependants.setdefault(dependency, set()).add(object_id)

    def upsert_source(self, fact: RangeFact, *, cursor: int) -> None:
        if fact.kind not in {"segment", "completion"}:
            raise StructureProofError("movement facts require a verified movement claim")
        self._check_fact(fact, cursor)
        if self._facts.get(fact.ref.object_id) != fact:
            self._invalidate(fact.ref.object_id, cursor, "source_revised")
            self._facts[fact.ref.object_id] = fact
            self._revision_floors[fact.ref.object_id] = fact.ref.object_revision
        self._cursor = cursor

    def upsert_movement(self, claim: MovementClaim, *, cursor: int) -> None:
        fact = claim.fact
        self._check_fact(fact, cursor)
        dependencies = [ref.object_id for ref in (*claim.component_refs, *claim.completion_refs)]
        if fact.ref.object_id in dependencies or set(dependencies).intersection(
            self._descendants(fact.ref.object_id)
        ):
            raise StructureProofError("movement proof would introduce a dependency cycle")
        previous_claim = self._claims.get(fact.ref.object_id)
        if previous_claim is not None and previous_claim != claim and previous_claim.fact == fact:
            raise StructureProofError("changed membership requires a newer movement revision")
        candidate_facts = {**self._facts, fact.ref.object_id: fact}
        candidate_claims = {**self._claims, fact.ref.object_id: claim}
        verify_movement(
            claim,
            candidate_facts,
            self._verifiers,
            cursor=cursor,
            movement_claims=candidate_claims,
        )
        if previous_claim != claim:
            self._invalidate(fact.ref.object_id, cursor, "source_revised")
            self._facts[fact.ref.object_id] = fact
            self._claims[fact.ref.object_id] = claim
            self._revision_floors[fact.ref.object_id] = fact.ref.object_revision
            self._link(fact.ref.object_id, dependencies)
        self._cursor = cursor

    def derive_center(self, seed_refs: Sequence[ObjectRef], *, cursor: int) -> RecursiveCenterProof:
        self._check_cursor(cursor)
        claims = []
        for ref in seed_refs:
            claim = self._claims.get(ref.object_id)
            if claim is None or claim.fact.ref != ref:
                raise StructureProofError("parent seed has no current verified movement claim")
            claims.append(claim)
        center = derive_parent_center(
            claims,
            self._facts,
            self._verifiers,
            cursor=cursor,
            movement_claims=self._claims,
        )
        if center.object_id in self._facts:
            raise StructureProofError("parent identity collides with a source fact")
        previous = self._centers.get(center.object_id)
        if previous is not None and previous != center:
            raise StructureProofError("frozen parent proof changed without source invalidation")
        self._centers[center.object_id] = center
        self._link(center.object_id, [ref.object_id for ref in center.seed_refs])
        self._cursor = cursor
        return center

    def remove_source(self, ref: ObjectRef, *, cursor: int) -> None:
        self._check_cursor(cursor)
        fact = self._facts.get(ref.object_id)
        if fact is None or fact.ref != ref:
            raise StructureProofError("cannot remove a missing or stale source revision")
        self._invalidate(ref.object_id, cursor, "source_removed")
        self._facts.pop(ref.object_id)
        self._claims.pop(ref.object_id, None)
        self._unlink(ref.object_id)
        self._cursor = cursor

    @property
    def facts(self) -> Mapping[str, RangeFact]:
        return dict(self._facts)

    @property
    def claims(self) -> Mapping[str, MovementClaim]:
        return dict(self._claims)

    def verify(self, ref: ObjectRef, *, cursor: int) -> None:
        claim = self._claims.get(ref.object_id)
        if claim is None or claim.fact.ref != ref:
            raise StructureProofError("consumer requires a current completed movement claim")
        verify_movement(
            claim, self._facts, self._verifiers, cursor=cursor, movement_claims=self._claims
        )

    @property
    def centers(self) -> Mapping[str, RecursiveCenterProof]:
        return dict(self._centers)

    def state(self) -> dict[str, Any]:
        return {
            "format": STATE_FORMAT,
            "scope": asdict(self.scope),
            "cursor": self._cursor,
            "facts": [asdict(fact) for _, fact in sorted(self._facts.items())],
            "claims": [
                {
                    "fact_ref": asdict(claim.fact.ref),
                    "component_refs": [asdict(ref) for ref in claim.component_refs],
                    "completion_rule": claim.completion_rule,
                    "completion_refs": [asdict(ref) for ref in claim.completion_refs],
                }
                for _, claim in sorted(self._claims.items())
            ],
            "centers": [center.payload() for _, center in sorted(self._centers.items())],
            "revision_floors": dict(sorted(self._revision_floors.items())),
            "invalidations": [asdict(item) for item in self.invalidations],
        }

    @classmethod
    def from_state(
        cls,
        state: dict[str, Any],
        scope: ProofScope,
        completion_verifiers: Mapping[str, CompletionVerifier],
    ) -> RecursiveProofGraph:
        """Rebuild and validate, including registered completion predicates and cores."""
        try:
            if (
                set(state)
                != {
                    "format",
                    "scope",
                    "cursor",
                    "facts",
                    "claims",
                    "centers",
                    "revision_floors",
                    "invalidations",
                }
                or state["format"] != STATE_FORMAT
                or ProofScope(**state["scope"]) != scope
            ):
                raise StructureProofError("recursive checkpoint format or identity differs")
            graph = cls(scope, completion_verifiers)
            cursor = state["cursor"]
            graph._check_cursor(cursor)
            facts: dict[str, RangeFact] = {}
            for row in state["facts"]:
                fields = dict(row)
                fields["ref"] = ObjectRef(**fields["ref"])
                fields["scope"] = ProofScope(**fields["scope"])
                fields["start"] = ProofAnchor(**fields["start"])
                fields["end"] = ProofAnchor(**fields["end"])
                fact = RangeFact(**fields)
                if fact.ref.object_id in facts:
                    raise StructureProofError("duplicate checkpoint fact")
                facts[fact.ref.object_id] = fact
            claims: dict[str, MovementClaim] = {}
            for row in state["claims"]:
                if set(row) != {"fact_ref", "component_refs", "completion_rule", "completion_refs"}:
                    raise StructureProofError("invalid checkpoint claim fields")
                ref = ObjectRef(**row["fact_ref"])
                if ref.object_id in claims or facts[ref.object_id].ref != ref:
                    raise StructureProofError("duplicate or stale checkpoint movement")
                claims[ref.object_id] = MovementClaim(
                    facts[ref.object_id],
                    tuple(ObjectRef(**item) for item in row["component_refs"]),
                    row["completion_rule"],
                    tuple(ObjectRef(**item) for item in row["completion_refs"]),
                )
            for fact in facts.values():
                if fact.kind != "movement":
                    graph.upsert_source(fact, cursor=cursor)
                elif fact.ref.object_id not in claims:
                    raise StructureProofError("saved completed movement lacks its source claim")
            # Completion witnesses can reference other completed movements. Admit
            # claims only once all direct dependencies have already been verified.
            pending = sorted(claims)
            while pending:
                progressed = False
                for object_id in tuple(pending):
                    claim = claims[object_id]
                    refs = (*claim.component_refs, *claim.completion_refs)
                    if all(ref.object_id in graph._facts for ref in refs):
                        graph.upsert_movement(claim, cursor=cursor)
                        pending.remove(object_id)
                        progressed = True
                if not progressed:
                    raise StructureProofError("checkpoint has cyclic or missing dependencies")
            seen_centers: set[str] = set()
            for row in state["centers"]:
                center = graph.derive_center(
                    tuple(ObjectRef(**item) for item in row["seed_refs"]),
                    cursor=cursor,
                )
                if center.object_id in seen_centers or json.dumps(
                    center.payload(),
                    sort_keys=True,
                ) != json.dumps(row, sort_keys=True):
                    raise StructureProofError("saved parent core differs from rederived proof")
                seen_centers.add(center.object_id)
            floors = state["revision_floors"]
            if not isinstance(floors, dict):
                raise StructureProofError("revision floors require a mapping")
            for object_id, revision in floors.items():
                ObjectRef(object_id, revision)
            if any(floors.get(key, 0) < value for key, value in graph._revision_floors.items()):
                raise StructureProofError("checkpoint discards a source revision floor")
            graph._revision_floors = dict(floors)
            for row in state["invalidations"]:
                item = ProofInvalidation(**row)
                if (
                    not item.object_id
                    or item.object_kind not in {"movement", "center"}
                    or item.reason not in {"source_revised", "source_removed"}
                    or isinstance(item.known_at_bar_index, bool)
                    or not isinstance(item.known_at_bar_index, int)
                    or not 0 <= item.known_at_bar_index <= cursor
                ):
                    raise StructureProofError("invalid checkpoint invalidation record")
                graph.invalidations.append(item)
            graph._cursor = cursor
            return graph
        except (KeyError, TypeError, AttributeError) as exc:
            raise StructureProofError("malformed recursive proof checkpoint") from exc
