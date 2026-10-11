from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from itertools import pairwise
from typing import Literal

RULE_VERSION = "fixed_same_level_recursive_v2"


def _i64(value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not -(2**63) <= value < 2**63:
        raise StructureProofError("structural prices and indices require signed int64 values")


class StructureProofError(ValueError):
    """A structural claim lacks its causal, versioned construction evidence."""


@dataclass(frozen=True, slots=True)
class ProofScope:
    dataset_id: str
    data_revision: str
    decomposition_id: str
    rule_version: str = RULE_VERSION

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, str) or not value
            for value in (self.dataset_id, self.decomposition_id, self.rule_version)
        ):
            raise StructureProofError("dataset and decomposition identities are required")
        if not isinstance(self.data_revision, str) or not re.fullmatch(
            r"sha256:[0-9a-f]{64}", self.data_revision
        ):
            raise StructureProofError("data_revision must be a full sha256 identity")
        if self.rule_version != RULE_VERSION:
            raise StructureProofError("unsupported recursive rule version")


@dataclass(frozen=True, slots=True)
class ObjectRef:
    object_id: str
    object_revision: int

    def __post_init__(self) -> None:
        _i64(self.object_revision)
        if not isinstance(self.object_id, str) or not self.object_id or self.object_revision < 1:
            raise StructureProofError("a reference requires object identity and positive revision")


@dataclass(frozen=True, slots=True)
class ProofAnchor:
    bar_index: int
    time: int
    price_i64: int

    def __post_init__(self) -> None:
        for value in (self.bar_index, self.time, self.price_i64):
            _i64(value)
        if self.bar_index < 0 or self.time < 0:
            raise StructureProofError("anchor index and UTC milliseconds must not be negative")


@dataclass(frozen=True, slots=True)
class RangeFact:
    ref: ObjectRef
    scope: ProofScope
    level: int
    kind: Literal["segment", "movement", "completion"]
    start: ProofAnchor
    end: ProofAnchor
    low_i64: int
    high_i64: int
    low_source_bar_index: int
    high_source_bar_index: int
    known_at_bar_index: int

    def __post_init__(self) -> None:
        for value in (
            self.level,
            self.low_i64,
            self.high_i64,
            self.low_source_bar_index,
            self.high_source_bar_index,
            self.known_at_bar_index,
        ):
            _i64(value)
        if self.kind not in {"segment", "movement", "completion"}:
            raise StructureProofError("unknown structural fact kind")
        if (
            self.level < -1
            or (self.kind == "movement" and self.level < 0)
            or (self.kind == "segment" and self.level != -1)
        ):
            raise StructureProofError("invalid structural level")
        if not 0 <= self.start.bar_index < self.end.bar_index <= self.known_at_bar_index:
            raise StructureProofError("range facts require ordered, already known anchors")
        if not 0 <= self.start.time < self.end.time:
            raise StructureProofError("range fact timestamps must increase")
        if not self.low_i64 <= min(self.start.price_i64, self.end.price_i64):
            raise StructureProofError("range omits endpoint low")
        if not self.high_i64 >= max(self.start.price_i64, self.end.price_i64):
            raise StructureProofError("range omits endpoint high")
        for source in (self.low_source_bar_index, self.high_source_bar_index):
            if not self.start.bar_index <= source <= self.end.bar_index:
                raise StructureProofError("range extreme source escapes object interval")


@dataclass(frozen=True, slots=True)
class MovementClaim:
    fact: RangeFact
    component_refs: tuple[ObjectRef, ...]
    completion_rule: str
    completion_refs: tuple[ObjectRef, ...]


CompletionVerifier = Callable[[MovementClaim, tuple[RangeFact, ...]], bool]


@dataclass(frozen=True, slots=True)
class VerifiedMovement:
    """Transient verified view; never persist a boolean in place of the source proof."""

    claim: MovementClaim
    completed_at_bar_index: int


def _resolve(
    refs: Sequence[ObjectRef],
    facts: Mapping[str, RangeFact],
    scope: ProofScope,
    cursor: int,
) -> tuple[RangeFact, ...]:
    if len({ref.object_id for ref in refs}) != len(refs):
        raise StructureProofError("duplicate dependency objects")
    result: list[RangeFact] = []
    for ref in refs:
        fact = facts.get(ref.object_id)
        if fact is None or fact.ref != ref:
            raise StructureProofError("missing or stale dependency revision")
        if fact.scope != scope:
            raise StructureProofError("cross-dataset, revision or decomposition dependency")
        if fact.known_at_bar_index > cursor:
            raise StructureProofError("dependency is not known at the current cursor")
        result.append(fact)
    return tuple(result)


def _continuous(facts: Sequence[RangeFact]) -> None:
    for left, right in pairwise(facts):
        if left.end != right.start:
            raise StructureProofError("component sequence must share exact time/price anchors")


def verify_movement(
    claim: MovementClaim,
    facts: Mapping[str, RangeFact],
    completion_verifiers: Mapping[str, CompletionVerifier],
    *,
    cursor: int,
    movement_claims: Mapping[str, MovementClaim] | None = None,
) -> VerifiedMovement:
    """Validate membership before dispatching the explicitly registered completion rule.

    This kernel deliberately supplies no completion shortcut. A producer must register
    a real structural rule; an unknown rule, mere status flag, or count cannot certify
    a movement. Production registers explicit versioned decomposition rules.
    """
    movement = claim.fact
    if movement.kind != "movement" or movement.known_at_bar_index > cursor:
        raise StructureProofError("movement is not available")
    if _resolve((movement.ref,), facts, movement.scope, cursor)[0] != movement:
        raise StructureProofError("movement claim differs from its current source revision")
    if len(claim.component_refs) < 3:
        raise StructureProofError("movement requires at least three lower-level components")
    components = _resolve(claim.component_refs, facts, movement.scope, cursor)
    child_kind = "segment" if movement.level == 0 else "movement"
    if any(child.kind != child_kind or child.level != movement.level - 1 for child in components):
        raise StructureProofError("movement components must be from its immediate lower layer")
    if movement.level > 0:
        if movement_claims is None:
            raise StructureProofError("higher movement requires recursive child completion proofs")
        for child in components:
            child_claim = movement_claims.get(child.ref.object_id)
            if child_claim is None or child_claim.fact != child:
                raise StructureProofError("missing or revised recursive child claim")
            verify_movement(
                child_claim,
                facts,
                completion_verifiers,
                cursor=cursor,
                movement_claims=movement_claims,
            )
    _continuous(components)
    if movement.start != components[0].start or movement.end != components[-1].end:
        raise StructureProofError("movement anchors differ from its composition")
    low = min(components, key=lambda item: (item.low_i64, item.low_source_bar_index))
    high = max(components, key=lambda item: (item.high_i64, -item.high_source_bar_index))
    if (
        movement.low_i64 != low.low_i64
        or movement.high_i64 != high.high_i64
        or movement.low_source_bar_index != low.low_source_bar_index
        or movement.high_source_bar_index != high.high_source_bar_index
    ):
        raise StructureProofError("movement range is not the actual component union")
    if not claim.completion_refs:
        raise StructureProofError("completion requires structural witness references")
    witnesses = _resolve(claim.completion_refs, facts, movement.scope, cursor)
    verifier = completion_verifiers.get(claim.completion_rule)
    if verifier is None or not verifier(claim, witnesses):
        raise StructureProofError("completion rule is unknown or its structural proof failed")
    completed_at = max(item.known_at_bar_index for item in (*components, *witnesses))
    if movement.known_at_bar_index < completed_at:
        raise StructureProofError("movement confirmation predates its construction evidence")
    return VerifiedMovement(claim, movement.known_at_bar_index)


@dataclass(frozen=True, slots=True)
class RecursiveCenterProof:
    object_id: str
    dataset_id: str
    data_revision: str
    decomposition_id: str
    rule_version: str
    level: int
    seed_refs: tuple[ObjectRef, ObjectRef, ObjectRef]
    start_bar_index: int
    start_time: int
    end_bar_index: int
    end_time: int
    zd_i64: int
    zg_i64: int
    dd_i64: int
    gg_i64: int
    known_at_bar_index: int
    range_profile: Literal["complete_seed_movement_union_v1"] = "complete_seed_movement_union_v1"

    def payload(self) -> dict[str, object]:
        return asdict(self)


def derive_parent_center(
    claims: Sequence[MovementClaim],
    facts: Mapping[str, RangeFact],
    completion_verifiers: Mapping[str, CompletionVerifier],
    *,
    cursor: int,
    movement_claims: Mapping[str, MovementClaim] | None = None,
) -> RecursiveCenterProof:
    """Revalidate all three seed proofs; never merge two center rectangles."""
    if len(claims) != 3 or len({item.fact.ref.object_id for item in claims}) != 3:
        raise StructureProofError("parent center requires three distinct movement seeds")
    verified = tuple(
        verify_movement(
            item,
            facts,
            completion_verifiers,
            cursor=cursor,
            movement_claims=movement_claims,
        )
        for item in claims
    )
    seeds = tuple(item.claim.fact for item in verified)
    scope = seeds[0].scope
    level = seeds[0].level
    if any(item.scope != scope or item.level != level for item in seeds):
        raise StructureProofError("parent seeds must have the same identity and structural level")
    _continuous(seeds)
    component_ids = [ref.object_id for claim in claims for ref in claim.component_refs]
    if len(component_ids) != len(set(component_ids)):
        raise StructureProofError("parent seeds reuse a constituent with duration")
    zd = max(item.low_i64 for item in seeds)
    zg = min(item.high_i64 for item in seeds)
    if zd > zg:
        raise StructureProofError("parent seed movements have no common price intersection")
    # Stable identity follows the frozen ordered seed identities, not mutable ranges
    # or confirmation delays. Source revisions remain in the payload for audit.
    encoded = json.dumps(
        [asdict(scope), level + 1, [item.ref.object_id for item in seeds]],
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    refs = (seeds[0].ref, seeds[1].ref, seeds[2].ref)
    return RecursiveCenterProof(
        "recursive-center-" + hashlib.sha256(encoded).hexdigest()[:20],
        scope.dataset_id,
        scope.data_revision,
        scope.decomposition_id,
        scope.rule_version,
        level + 1,
        refs,
        seeds[0].start.bar_index,
        seeds[0].start.time,
        seeds[-1].end.bar_index,
        seeds[-1].end.time,
        zd,
        zg,
        min(item.low_i64 for item in seeds),
        max(item.high_i64 for item in seeds),
        max(item.completed_at_bar_index for item in verified),
    )
