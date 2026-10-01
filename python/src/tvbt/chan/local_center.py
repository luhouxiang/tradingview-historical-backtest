from __future__ import annotations

import hashlib
import inspect
import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass, replace
from typing import Any, Literal

Direction = Literal["up", "down"]
UnitKind = Literal["BI", "SEGMENT"]
CenterStatus = Literal["ACTIVE", "PENDING_BREAK", "CLOSED"]
CenterStreamState = Literal["SEEK", "ACTIVE", "PENDING_BREAK"]
BreakDirection = Literal["up", "down"]
EntryRole = Literal["FROM_BELOW", "FROM_ABOVE"]
Operation = Literal["upsert", "delete"]
CenterEventType = Literal[
    "SEED_FOUND",
    "EXIT_PENDING",
    "RETEST_TOUCH",
    "BREAK_CONFIRMED",
    "SEARCH_RESTARTED",
    "ENTRY_LINKED",
    "CENTER_REVISED",
]

RULE_VERSION = "local_center_boundary_v1"
SOURCE_FILE = "python/src/tvbt/chan/local_center.py"


def _stable_id(prefix: str, *parts: object) -> str:
    encoded = json.dumps(parts, ensure_ascii=False, separators=(",", ":")).encode()
    return f"{prefix}-{hashlib.sha256(encoded).hexdigest()[:20]}"


@dataclass(frozen=True)
class CenterStreamKey:
    symbol: str
    timeframe: str
    unit_kind: UnitKind
    structural_level: str
    algorithm_version: str
    anchor_id: str

    @property
    def value(self) -> str:
        return "|".join(
            (
                self.symbol,
                self.timeframe,
                self.unit_kind,
                self.structural_level,
                self.algorithm_version,
                self.anchor_id,
            )
        )


@dataclass(frozen=True)
class CenterUnit:
    """One confirmed or preview structural unit in a local-center stream.

    ``confirmed_at`` is the causal bar index at which the unit became usable.
    Wall-clock anchors remain in ``start_time`` and ``end_time``.
    """

    id: str
    index: int
    direction: Direction
    start_pivot_id: str
    end_pivot_id: str
    start_time: int
    end_time: int
    start_price_tick: int
    end_price_tick: int
    low_tick: int
    high_tick: int
    confirmed_at: int | None
    source_revision: str

    @property
    def confirmed(self) -> bool:
        return self.confirmed_at is not None


@dataclass(frozen=True)
class LocalCenter:
    id: str
    stream_key: str
    rule_version: str
    unit_kind: UnitKind
    structural_level: str
    scan_floor: int
    seed_ids: tuple[str, str, str]
    zd_tick: int
    zg_tick: int
    seed_start: int
    seed_end: int
    formed_at: int
    body_start: int
    body_end: int | None
    observed_start: int
    observed_end: int
    observed_low: int
    observed_high: int
    status: CenterStatus
    pending_exit_id: str | None
    exit_id: str | None
    first_retest_id: str | None
    entry_id: str | None
    local_entry: EntryRole | None
    break_direction: BreakDirection | None
    break_confirmed_at: int | None
    parent_id: str | None
    left_context_incomplete: bool
    roles_overlap_seed: bool
    source_revision: str


@dataclass(frozen=True)
class CenterConnection:
    id: str
    stream_key: str
    rule_version: str
    from_center_id: str
    to_center_id: str | None
    unit_ids: tuple[str, ...]
    exit_unit_id: str
    entry_unit_id: str | None
    first_retest_id: str
    confirmed_at: int
    roles_overlap_seed: bool
    source_revision: str


@dataclass(frozen=True)
class CenterAuditEvent:
    id: str
    event_type: CenterEventType
    center_id: str
    unit_ids: tuple[str, ...]
    zd_tick: int
    zg_tick: int
    comparison_value: int | None
    event_time: int
    known_at: int
    rule_version: str
    source_file: str
    source_line: int
    revision: int = 1


@dataclass(frozen=True)
class LocalCenterDecomposition:
    stream_state: CenterStreamState
    centers: tuple[LocalCenter, ...]
    connections: tuple[CenterConnection, ...]
    events: tuple[CenterAuditEvent, ...]


@dataclass(frozen=True)
class CenterObjectRevision:
    object_kind: Literal["center", "connection"]
    object_id: str
    operation: Operation
    object_revision: int
    known_at: int
    event_type: Literal["SEED_FOUND", "ENTRY_LINKED", "CENTER_REVISED"]
    payload: dict[str, Any] | None


@dataclass(frozen=True)
class CenterBoundaryRelation:
    previous_center_id: str
    current_center_id: str
    core_relation: Literal["CORE_ABOVE", "CORE_BELOW", "CORE_TOUCH_OR_OVERLAP"]
    higher_level_review_required: bool
    relative_dir: Literal["UP", "DOWN", "OVERLAP", "UNKNOWN"] = "UNKNOWN"
    trend_status: Literal["UNVERIFIED"] = "UNVERIFIED"


@dataclass(frozen=True)
class LocalCenterPreview:
    """A non-tradable projection; never modifies the confirmed decomposition."""

    id: str
    center_id: str
    unit_id: str
    exit_unit_id: str
    state: Literal["EXIT_PENDING", "RETEST_PENDING", "RETEST_TOUCH"]
    direction: BreakDirection
    comparison_value: int
    event_time: int
    known_at: int
    source_revision: str
    rule_version: str = RULE_VERSION
    confirmed: Literal[False] = False


def preview_local_center(
    units: Sequence[CenterUnit],
    decomposition: LocalCenterDecomposition,
    *,
    known_at: int,
) -> LocalCenterPreview | None:
    """Inspect one explicitly unconfirmed tail using its complete observed range.

    Future confirmed records are not converted into previews. The caller must supply
    only currently observed units; causal guards reject a future confirmed prefix.
    A touched low/high stays in the full unit range even if its endpoint rebounds.
    """
    _validate_units(units)
    if any(unit.confirmed_at is not None and unit.confirmed_at > known_at for unit in units):
        raise ValueError("preview cannot consume future confirmed units")
    tails = [unit for unit in units if not unit.confirmed]
    if len(tails) > 1:
        raise ValueError("preview requires exactly one unconfirmed tail unit")
    if not tails or not decomposition.centers:
        return None
    center = decomposition.centers[-1]
    if center.status == "CLOSED" or center.formed_at > known_at:
        return None
    tail = tails[0]
    state: Literal["EXIT_PENDING", "RETEST_PENDING", "RETEST_TOUCH"]
    if len(units) > 1 and center.pending_exit_id == units[-2].id:
        departure = units[-2]
        direction = _departure_direction(departure, center)
        if direction is None:
            raise ValueError("pending preview exit does not match the confirmed center")
        comparison = tail.low_tick if direction == "up" else tail.high_tick
        separated = (
            comparison > center.zg_tick if direction == "up" else comparison < center.zd_tick
        )
        state = "RETEST_PENDING" if separated else "RETEST_TOUCH"
        exit_id = departure.id
    else:
        direction = _departure_direction(tail, center)
        if direction is None:
            return None
        comparison = tail.end_price_tick
        state = "EXIT_PENDING"
        exit_id = tail.id
    return LocalCenterPreview(
        id=_stable_id("local-center-preview", center.id, tail.id),
        center_id=center.id,
        unit_id=tail.id,
        exit_unit_id=exit_id,
        state=state,
        direction=direction,
        comparison_value=comparison,
        event_time=tail.end_time,
        known_at=known_at,
        source_revision=tail.source_revision,
    )


def _validate_units(units: Sequence[CenterUnit]) -> None:
    seen: set[str] = set()
    preview_seen = False
    for position, unit in enumerate(units):
        if not unit.id or unit.id in seen:
            raise ValueError("center units must have unique non-empty ids")
        seen.add(unit.id)
        if unit.low_tick > min(unit.start_price_tick, unit.end_price_tick):
            raise ValueError("unit low must contain both structural endpoints")
        if unit.high_tick < max(unit.start_price_tick, unit.end_price_tick):
            raise ValueError("unit high must contain both structural endpoints")
        if unit.low_tick > unit.high_tick or unit.start_time > unit.end_time:
            raise ValueError("unit price/time range is invalid")
        if (unit.direction == "up" and unit.end_price_tick <= unit.start_price_tick) or (
            unit.direction == "down" and unit.end_price_tick >= unit.start_price_tick
        ):
            raise ValueError("unit direction must match its structural endpoints")
        if preview_seen and unit.confirmed:
            raise ValueError("confirmed units cannot follow preview units")
        preview_seen = preview_seen or not unit.confirmed
        if position == 0:
            continue
        previous = units[position - 1]
        if (
            unit.confirmed_at is not None
            and previous.confirmed_at is not None
            and unit.confirmed_at < previous.confirmed_at
        ):
            raise ValueError("center unit confirmation times must be monotonic")
        if unit.index != previous.index + 1:
            raise ValueError("center unit indexes must be continuous")
        if unit.direction == previous.direction:
            raise ValueError("center unit directions must alternate")
        if unit.start_pivot_id != previous.end_pivot_id:
            raise ValueError("adjacent center units must share a pivot")
        if unit.start_time != previous.end_time:
            raise ValueError("adjacent center units must share endpoint time")
        if unit.start_price_tick != previous.end_price_tick:
            raise ValueError("adjacent center units must share endpoint price")


def _source_revision(units: Sequence[CenterUnit]) -> str:
    return _stable_id("revision", *((unit.id, unit.source_revision) for unit in units))


def _confirmed_at(unit: CenterUnit) -> int:
    if unit.confirmed_at is None:
        raise ValueError(f"unit {unit.id} is not confirmed")
    return unit.confirmed_at


def _event(
    event_type: CenterEventType,
    center: LocalCenter,
    units: Sequence[CenterUnit],
    *,
    comparison_value: int | None,
    event_time: int,
    known_at: int,
) -> CenterAuditEvent:
    frame = inspect.currentframe()
    caller = frame.f_back if frame is not None else None
    source_line = caller.f_lineno if caller is not None else 0
    del frame
    return CenterAuditEvent(
        id=_stable_id(center.id, event_type, *(unit.id for unit in units), 1),
        event_type=event_type,
        center_id=center.id,
        unit_ids=tuple(unit.id for unit in units),
        zd_tick=center.zd_tick,
        zg_tick=center.zg_tick,
        comparison_value=comparison_value,
        event_time=event_time,
        known_at=known_at,
        rule_version=RULE_VERSION,
        source_file=SOURCE_FILE,
        source_line=source_line,
    )


def _seed_at(units: Sequence[CenterUnit], position: int) -> tuple[int, int] | None:
    seed = units[position : position + 3]
    if len(seed) != 3 or not all(unit.confirmed for unit in seed):
        return None
    zd_tick = max(unit.low_tick for unit in seed)
    zg_tick = min(unit.high_tick for unit in seed)
    return (zd_tick, zg_tick) if zd_tick < zg_tick else None


def _entry_role(unit: CenterUnit | None, center: LocalCenter) -> EntryRole | None:
    if unit is None:
        return None
    if (
        unit.direction == "up"
        and unit.start_price_tick < center.zd_tick
        and unit.end_price_tick >= center.zg_tick
    ):
        return "FROM_BELOW"
    if (
        unit.direction == "down"
        and unit.start_price_tick > center.zg_tick
        and unit.end_price_tick <= center.zd_tick
    ):
        return "FROM_ABOVE"
    return None


def _departure_direction(unit: CenterUnit, center: LocalCenter) -> BreakDirection | None:
    if unit.direction == "up" and unit.end_price_tick > center.zg_tick:
        return "up"
    if unit.direction == "down" and unit.end_price_tick < center.zd_tick:
        return "down"
    return None


def _separated_retest(retest: CenterUnit, direction: BreakDirection, center: LocalCenter) -> bool:
    if direction == "up":
        return retest.direction == "down" and retest.low_tick > center.zg_tick
    return retest.direction == "up" and retest.high_tick < center.zd_tick


def _restart_position(
    stream: CenterStreamKey, seed_position: int, exit_position: int, retest_position: int
) -> int:
    """A segment exit may also enter the next center, but an old seed is never reused."""
    if stream.unit_kind == "SEGMENT" and exit_position > seed_position + 2:
        return exit_position
    return retest_position


def _incoming_exit_role(
    previous: LocalCenter | None, first_seed: CenterUnit
) -> EntryRole | None:
    if previous is None or previous.exit_id != first_seed.id:
        return None
    if previous.break_direction == "up" and first_seed.direction == "up":
        return "FROM_BELOW"
    if previous.break_direction == "down" and first_seed.direction == "down":
        return "FROM_ABOVE"
    return None


def _with_observation(
    center: LocalCenter, observed: Sequence[CenterUnit], **changes: Any
) -> LocalCenter:
    return replace(
        center,
        observed_end=observed[-1].end_time,
        observed_low=min(unit.low_tick for unit in observed),
        observed_high=max(unit.high_tick for unit in observed),
        source_revision=_source_revision(observed),
        **changes,
    )


def compare_center_boundaries(
    previous: LocalCenter, current: LocalCenter
) -> CenterBoundaryRelation:
    if current.zd_tick > previous.zg_tick:
        relation: Literal["CORE_ABOVE", "CORE_BELOW", "CORE_TOUCH_OR_OVERLAP"] = "CORE_ABOVE"
    elif current.zg_tick < previous.zd_tick:
        relation = "CORE_BELOW"
    else:
        relation = "CORE_TOUCH_OR_OVERLAP"
    envelope_touches = not (
        current.observed_low > previous.observed_high
        or current.observed_high < previous.observed_low
    )
    relative_dir = classify_center_relative_direction(
        previous.zd_tick,
        previous.zg_tick,
        previous.observed_low,
        previous.observed_high,
        current.zd_tick,
        current.zg_tick,
        current.observed_low,
        current.observed_high,
        previous_confirmed=previous.status == "CLOSED",
    )
    return CenterBoundaryRelation(
        previous_center_id=previous.id,
        current_center_id=current.id,
        core_relation=relation,
        higher_level_review_required=(
            relation in {"CORE_ABOVE", "CORE_BELOW"} and envelope_touches
        ),
        relative_dir=relative_dir,
    )


def classify_center_relative_direction(
    previous_zd: int,
    previous_zg: int,
    previous_dd: int,
    previous_gg: int,
    current_zd: int,
    current_zg: int,
    current_dd: int,
    current_gg: int,
    *,
    previous_confirmed: bool,
) -> Literal["UP", "DOWN", "OVERLAP", "UNKNOWN"]:
    """Both cores and body oscillation envelopes must separate strictly."""
    if not previous_confirmed:
        return "UNKNOWN"
    if current_zd > previous_zg and current_dd > previous_gg:
        return "UP"
    if current_zg < previous_zd and current_gg < previous_dd:
        return "DOWN"
    return "OVERLAP"


def decompose_local_centers(
    units: Sequence[CenterUnit],
    stream: CenterStreamKey,
    *,
    left_context_complete: bool = False,
    known_at: int | None = None,
) -> LocalCenterDecomposition:
    """Apply the versioned local-center boundary profile to one BI/SEGMENT stream."""

    if known_at is not None:
        units = tuple(
            unit
            if unit.confirmed_at is None or unit.confirmed_at <= known_at
            else replace(unit, confirmed_at=None)
            for unit in units
        )
    _validate_units(units)
    centers: list[LocalCenter] = []
    connections: list[CenterConnection] = []
    events: list[CenterAuditEvent] = []
    scan_floor_position = 0
    pending_closed: tuple[LocalCenter, int] | None = None

    while scan_floor_position + 2 < len(units):
        seed_position: int | None = None
        seed_core: tuple[int, int] | None = None
        for candidate in range(scan_floor_position, len(units) - 2):
            core = _seed_at(units, candidate)
            if core is not None:
                seed_position = candidate
                seed_core = core
                break
        if seed_position is None or seed_core is None:
            break

        seed = units[seed_position : seed_position + 3]
        formed_at = max(int(unit.confirmed_at) for unit in seed if unit.confirmed_at is not None)
        center = LocalCenter(
            id=_stable_id("local-center", stream.value, RULE_VERSION, seed[0].id, seed[2].id),
            stream_key=stream.value,
            rule_version=RULE_VERSION,
            unit_kind=stream.unit_kind,
            structural_level=stream.structural_level,
            scan_floor=units[scan_floor_position].index,
            seed_ids=(seed[0].id, seed[1].id, seed[2].id),
            zd_tick=seed_core[0],
            zg_tick=seed_core[1],
            seed_start=seed[0].start_time,
            seed_end=seed[2].end_time,
            formed_at=formed_at,
            body_start=seed[0].start_time,
            body_end=None,
            observed_start=seed[0].start_time,
            observed_end=seed[2].end_time,
            observed_low=min(unit.low_tick for unit in seed),
            observed_high=max(unit.high_tick for unit in seed),
            status="ACTIVE",
            pending_exit_id=None,
            exit_id=None,
            first_retest_id=None,
            entry_id=None,
            local_entry=None,
            break_direction=None,
            break_confirmed_at=None,
            parent_id=None,
            left_context_incomplete=(
                seed_position == 0 and scan_floor_position == 0 and not left_context_complete
            ),
            roles_overlap_seed=False,
            source_revision=_source_revision(seed),
        )
        predecessor = units[seed_position - 1] if seed_position > 0 else None
        entry_role = _entry_role(predecessor, center)
        if entry_role is not None and predecessor is not None:
            center = replace(center, entry_id=predecessor.id, local_entry=entry_role)
        if stream.unit_kind == "SEGMENT" and pending_closed is not None:
            incoming = _incoming_exit_role(pending_closed[0], seed[0])
            if incoming is not None:
                center = replace(center, entry_id=seed[0].id, local_entry=incoming)
        events.append(
            _event(
                "SEED_FOUND",
                center,
                seed,
                comparison_value=center.zg_tick - center.zd_tick,
                event_time=seed[-1].end_time,
                known_at=formed_at,
            )
        )

        if pending_closed is not None:
            previous, exit_position = pending_closed
            stop = max(exit_position, seed_position - 1)
            connection_units = units[exit_position : stop + 1]
            overlap = (
                previous.roles_overlap_seed
                or previous.first_retest_id in center.seed_ids
                or previous.exit_id in center.seed_ids
            )
            connection = CenterConnection(
                id=_stable_id("center-connection", stream.value, previous.id),
                stream_key=stream.value,
                rule_version=RULE_VERSION,
                from_center_id=previous.id,
                to_center_id=center.id,
                unit_ids=tuple(unit.id for unit in connection_units),
                exit_unit_id=str(previous.exit_id),
                entry_unit_id=center.entry_id,
                first_retest_id=str(previous.first_retest_id),
                confirmed_at=formed_at,
                roles_overlap_seed=overlap,
                source_revision=_source_revision(connection_units),
            )
            connections.append(connection)
            events.append(
                _event(
                    "ENTRY_LINKED",
                    center,
                    connection_units,
                    comparison_value=None,
                    event_time=center.seed_start,
                    known_at=formed_at,
                )
            )
            pending_closed = None

        closed = False
        pair_position = seed_position + 2
        last_confirmed_position = max(
            (position for position, unit in enumerate(units) if unit.confirmed),
            default=seed_position + 2,
        )
        while pair_position <= last_confirmed_position:
            departure = units[pair_position]
            direction = _departure_direction(departure, center)
            if direction is None:
                pair_position += 1
                continue
            events.append(
                _event(
                    "EXIT_PENDING",
                    center,
                    [departure],
                    comparison_value=departure.end_price_tick,
                    event_time=departure.end_time,
                    known_at=_confirmed_at(departure),
                )
            )
            retest_position = pair_position + 1
            if retest_position >= len(units) or not units[retest_position].confirmed:
                observed = units[seed_position : pair_position + 1]
                center = _with_observation(
                    center,
                    observed,
                    status="PENDING_BREAK",
                    pending_exit_id=departure.id,
                    break_direction=direction,
                )
                break
            retest = units[retest_position]
            if _separated_retest(retest, direction, center):
                observed = units[seed_position : retest_position + 1]
                center = _with_observation(
                    center,
                    observed,
                    status="CLOSED",
                    pending_exit_id=None,
                    exit_id=departure.id,
                    first_retest_id=retest.id,
                    break_direction=direction,
                    break_confirmed_at=_confirmed_at(retest),
                    body_end=max(center.seed_end, departure.start_time),
                    roles_overlap_seed=departure.id in center.seed_ids,
                )
                events.append(
                    _event(
                        "BREAK_CONFIRMED",
                        center,
                        [departure, retest],
                        comparison_value=(
                            retest.low_tick if direction == "up" else retest.high_tick
                        ),
                        event_time=retest.end_time,
                        known_at=_confirmed_at(retest),
                    )
                )
                restart_position = _restart_position(
                    stream, seed_position, pair_position, retest_position
                )
                events.append(
                    _event(
                        "SEARCH_RESTARTED",
                        center,
                        [units[restart_position]],
                        comparison_value=units[restart_position].index,
                        event_time=retest.end_time,
                        known_at=_confirmed_at(retest),
                    )
                )
                centers.append(center)
                pending_closed = (center, pair_position)
                scan_floor_position = restart_position
                closed = True
                break
            events.append(
                _event(
                    "RETEST_TOUCH",
                    center,
                    [departure, retest],
                    comparison_value=(retest.low_tick if direction == "up" else retest.high_tick),
                    event_time=retest.end_time,
                    known_at=_confirmed_at(retest),
                )
            )
            pair_position += 1

        if closed:
            continue
        if center.status == "ACTIVE":
            observed = units[seed_position : last_confirmed_position + 1]
            center = _with_observation(center, observed)
        centers.append(center)
        break

    if pending_closed is not None:
        previous, exit_position = pending_closed
        last_confirmed_position = max(
            (position for position, unit in enumerate(units) if unit.confirmed),
            default=exit_position,
        )
        connection_units = units[exit_position : last_confirmed_position + 1]
        connections.append(
            CenterConnection(
                id=_stable_id("center-connection", stream.value, previous.id),
                stream_key=stream.value,
                rule_version=RULE_VERSION,
                from_center_id=previous.id,
                to_center_id=None,
                unit_ids=tuple(unit.id for unit in connection_units),
                exit_unit_id=str(previous.exit_id),
                entry_unit_id=None,
                first_retest_id=str(previous.first_retest_id),
                confirmed_at=max(
                    int(unit.confirmed_at)
                    for unit in connection_units
                    if unit.confirmed_at is not None
                ),
                roles_overlap_seed=previous.roles_overlap_seed,
                source_revision=_source_revision(connection_units),
            )
        )

    stream_state: CenterStreamState = (
        "SEEK" if not centers or centers[-1].status == "CLOSED" else centers[-1].status
    )
    return LocalCenterDecomposition(stream_state, tuple(centers), tuple(connections), tuple(events))


class LocalCenterAccumulator:
    """Incrementally decompose an append-only confirmed-unit stream."""

    def __init__(self, stream: CenterStreamKey, *, left_context_complete: bool = False) -> None:
        self.stream = stream
        self.left_context_complete = left_context_complete
        self._units: list[CenterUnit] = []
        self._centers: list[LocalCenter] = []
        self._connections: list[CenterConnection] = []
        self._events: list[CenterAuditEvent] = []
        self._updated_events: list[CenterAuditEvent] = []
        self._event_ids: set[str] = set()
        self._scan_floor_position = 0
        self._active_center_position: int | None = None
        self._active_seed_position: int | None = None
        self._pair_position: int | None = None
        self._pending_closed: tuple[int, int] | None = None
        self._snapshots: dict[int, tuple[Any, ...]] = {}
        self._save_snapshot()

    def update(self, units: Sequence[CenterUnit]) -> LocalCenterDecomposition:
        """Append a stable prefix; reset and replay if an upstream unit was revised."""
        _validate_units(units)
        self._updated_events = []
        confirmed = [unit for unit in units if unit.confirmed]
        common = 0
        for old, new in zip(self._units, confirmed, strict=False):
            if old != new:
                break
            common += 1
        if common != len(self._units) or len(confirmed) < len(self._units):
            if common in self._snapshots:
                self._restore_snapshot(common)
            else:
                self._reset()
                common = 0
        for unit in confirmed[common:]:
            self._units.append(unit)
            self._advance()
            self._save_snapshot()
        return self.result()

    @property
    def updated_events(self) -> tuple[CenterAuditEvent, ...]:
        return tuple(self._updated_events)

    def preview(self, unit: CenterUnit, *, known_at: int) -> LocalCenterPreview | None:
        """Read-only preview of a contiguous tail; excluded from checkpoint state.

        The confirmed prefix was validated during update. Validate only the last
        adjacent pair here so per-bar previews do not rescan the entire history.
        """
        if unit.confirmed:
            raise ValueError("preview unit must be unconfirmed")
        return preview_local_center((*self._units[-1:], unit), self.result(), known_at=known_at)

    @property
    def last_unit(self) -> CenterUnit | None:
        return self._units[-1] if self._units else None

    def state(self) -> dict[str, Any]:
        """Return the deterministic prefix needed to rebuild rollback snapshots."""
        return {
            "stream": asdict(self.stream),
            "left_context_complete": self.left_context_complete,
            "units": [asdict(unit) for unit in self._units],
        }

    @classmethod
    def from_state(cls, state: dict[str, Any]) -> LocalCenterAccumulator:
        """Rebuild rollback snapshots without republishing historical events."""
        accumulator = cls(
            CenterStreamKey(**state["stream"]),
            left_context_complete=bool(state.get("left_context_complete", False)),
        )
        accumulator.update([CenterUnit(**item) for item in state.get("units", [])])
        accumulator._updated_events = []
        return accumulator

    def result(self) -> LocalCenterDecomposition:
        stream_state: CenterStreamState = (
            "SEEK"
            if not self._centers or self._centers[-1].status == "CLOSED"
            else self._centers[-1].status
        )
        return LocalCenterDecomposition(
            stream_state,
            tuple(self._centers),
            tuple(self._connections),
            tuple(self._events),
        )

    def _reset(self) -> None:
        self._units.clear()
        self._centers.clear()
        self._connections.clear()
        self._events.clear()
        self._updated_events.clear()
        self._event_ids.clear()
        self._scan_floor_position = 0
        self._active_center_position = None
        self._active_seed_position = None
        self._pair_position = None
        self._pending_closed = None
        self._snapshots.clear()
        self._save_snapshot()

    def _save_snapshot(self) -> None:
        prefix = len(self._units)
        self._snapshots[prefix] = (
            list(self._centers),
            list(self._connections),
            list(self._events),
            set(self._event_ids),
            self._scan_floor_position,
            self._active_center_position,
            self._active_seed_position,
            self._pair_position,
            self._pending_closed,
        )
        for old_prefix in sorted(self._snapshots)[:-33]:
            if old_prefix != 0:
                del self._snapshots[old_prefix]

    def _restore_snapshot(self, prefix: int) -> None:
        snapshot = self._snapshots[prefix]
        self._units = self._units[:prefix]
        self._centers = list(snapshot[0])
        self._connections = list(snapshot[1])
        self._events = list(snapshot[2])
        self._event_ids = set(snapshot[3])
        self._scan_floor_position = int(snapshot[4])
        self._active_center_position = snapshot[5]
        self._active_seed_position = snapshot[6]
        self._pair_position = snapshot[7]
        self._pending_closed = snapshot[8]
        for old_prefix in tuple(self._snapshots):
            if old_prefix > prefix:
                del self._snapshots[old_prefix]

    def _append_event(self, event: CenterAuditEvent) -> None:
        if event.id not in self._event_ids:
            self._event_ids.add(event.id)
            self._events.append(event)
            self._updated_events.append(event)

    def _new_center(self, seed_position: int, core: tuple[int, int]) -> LocalCenter:
        seed = self._units[seed_position : seed_position + 3]
        formed_at = max(_confirmed_at(unit) for unit in seed)
        center = LocalCenter(
            id=_stable_id("local-center", self.stream.value, RULE_VERSION, seed[0].id, seed[2].id),
            stream_key=self.stream.value,
            rule_version=RULE_VERSION,
            unit_kind=self.stream.unit_kind,
            structural_level=self.stream.structural_level,
            scan_floor=self._units[self._scan_floor_position].index,
            seed_ids=(seed[0].id, seed[1].id, seed[2].id),
            zd_tick=core[0],
            zg_tick=core[1],
            seed_start=seed[0].start_time,
            seed_end=seed[2].end_time,
            formed_at=formed_at,
            body_start=seed[0].start_time,
            body_end=None,
            observed_start=seed[0].start_time,
            observed_end=seed[2].end_time,
            observed_low=min(unit.low_tick for unit in seed),
            observed_high=max(unit.high_tick for unit in seed),
            status="ACTIVE",
            pending_exit_id=None,
            exit_id=None,
            first_retest_id=None,
            entry_id=None,
            local_entry=None,
            break_direction=None,
            break_confirmed_at=None,
            parent_id=None,
            left_context_incomplete=(
                seed_position == 0
                and self._scan_floor_position == 0
                and not self.left_context_complete
            ),
            roles_overlap_seed=False,
            source_revision=_source_revision(seed),
        )
        predecessor = self._units[seed_position - 1] if seed_position > 0 else None
        entry_role = _entry_role(predecessor, center)
        if entry_role is not None and predecessor is not None:
            center = replace(center, entry_id=predecessor.id, local_entry=entry_role)
        if self.stream.unit_kind == "SEGMENT" and self._pending_closed is not None:
            previous = self._centers[self._pending_closed[0]]
            incoming = _incoming_exit_role(previous, seed[0])
            if incoming is not None:
                center = replace(center, entry_id=seed[0].id, local_entry=incoming)
        self._append_event(
            _event(
                "SEED_FOUND",
                center,
                seed,
                comparison_value=center.zg_tick - center.zd_tick,
                event_time=seed[-1].end_time,
                known_at=formed_at,
            )
        )
        return center

    def _link_pending_center(self, center: LocalCenter, seed_position: int) -> None:
        if self._pending_closed is None:
            return
        previous_position, exit_position = self._pending_closed
        previous = self._centers[previous_position]
        connection_units = self._units[exit_position : max(exit_position + 1, seed_position)]
        overlap = (
            previous.roles_overlap_seed
            or previous.first_retest_id in center.seed_ids
            or previous.exit_id in center.seed_ids
        )
        self._connections[-1] = CenterConnection(
            id=_stable_id("center-connection", self.stream.value, previous.id),
            stream_key=self.stream.value,
            rule_version=RULE_VERSION,
            from_center_id=previous.id,
            to_center_id=center.id,
            unit_ids=tuple(unit.id for unit in connection_units),
            exit_unit_id=str(previous.exit_id),
            entry_unit_id=center.entry_id,
            first_retest_id=str(previous.first_retest_id),
            confirmed_at=center.formed_at,
            roles_overlap_seed=overlap,
            source_revision=_source_revision(connection_units),
        )
        self._append_event(
            _event(
                "ENTRY_LINKED",
                center,
                connection_units,
                comparison_value=None,
                event_time=center.seed_start,
                known_at=center.formed_at,
            )
        )
        self._pending_closed = None

    def _refresh_open_connection(self) -> None:
        if self._pending_closed is None:
            return
        _, exit_position = self._pending_closed
        connection_units = self._units[exit_position:]
        self._connections[-1] = replace(
            self._connections[-1],
            unit_ids=tuple(unit.id for unit in connection_units),
            confirmed_at=max(_confirmed_at(unit) for unit in connection_units),
            source_revision=_source_revision(connection_units),
        )

    def _advance(self) -> None:
        while True:
            if self._active_center_position is None:
                seed_position: int | None = None
                seed_core: tuple[int, int] | None = None
                for candidate in range(self._scan_floor_position, len(self._units) - 2):
                    core = _seed_at(self._units, candidate)
                    if core is not None:
                        seed_position, seed_core = candidate, core
                        break
                if seed_position is None or seed_core is None:
                    self._refresh_open_connection()
                    return
                center = self._new_center(seed_position, seed_core)
                self._link_pending_center(center, seed_position)
                self._centers.append(center)
                self._active_center_position = len(self._centers) - 1
                self._active_seed_position = seed_position
                self._pair_position = seed_position + 2

            assert self._active_center_position is not None
            assert self._active_seed_position is not None
            assert self._pair_position is not None
            center = self._centers[self._active_center_position]
            if self._pair_position >= len(self._units):
                return
            departure = self._units[self._pair_position]
            direction = _departure_direction(departure, center)
            if direction is None:
                observed = self._units[self._active_seed_position :]
                self._centers[self._active_center_position] = _with_observation(
                    center,
                    observed,
                    status="ACTIVE",
                    pending_exit_id=None,
                    break_direction=None,
                )
                self._pair_position += 1
                continue
            self._append_event(
                _event(
                    "EXIT_PENDING",
                    center,
                    [departure],
                    comparison_value=departure.end_price_tick,
                    event_time=departure.end_time,
                    known_at=_confirmed_at(departure),
                )
            )
            retest_position = self._pair_position + 1
            if retest_position >= len(self._units):
                observed = self._units[self._active_seed_position :]
                self._centers[self._active_center_position] = _with_observation(
                    center,
                    observed,
                    status="PENDING_BREAK",
                    pending_exit_id=departure.id,
                    break_direction=direction,
                )
                return
            retest = self._units[retest_position]
            if not _separated_retest(retest, direction, center):
                self._append_event(
                    _event(
                        "RETEST_TOUCH",
                        center,
                        [departure, retest],
                        comparison_value=(
                            retest.low_tick if direction == "up" else retest.high_tick
                        ),
                        event_time=retest.end_time,
                        known_at=_confirmed_at(retest),
                    )
                )
                observed = self._units[self._active_seed_position : retest_position + 1]
                self._centers[self._active_center_position] = _with_observation(
                    center,
                    observed,
                    status="ACTIVE",
                    pending_exit_id=None,
                    break_direction=None,
                )
                self._pair_position += 1
                continue

            observed = self._units[self._active_seed_position : retest_position + 1]
            center = _with_observation(
                center,
                observed,
                status="CLOSED",
                pending_exit_id=None,
                exit_id=departure.id,
                first_retest_id=retest.id,
                break_direction=direction,
                break_confirmed_at=_confirmed_at(retest),
                body_end=max(center.seed_end, departure.start_time),
                roles_overlap_seed=departure.id in center.seed_ids,
            )
            self._centers[self._active_center_position] = center
            self._append_event(
                _event(
                    "BREAK_CONFIRMED",
                    center,
                    [departure, retest],
                    comparison_value=(retest.low_tick if direction == "up" else retest.high_tick),
                    event_time=retest.end_time,
                    known_at=_confirmed_at(retest),
                )
            )
            restart_position = _restart_position(
                self.stream, self._active_seed_position, self._pair_position, retest_position
            )
            self._append_event(
                _event(
                    "SEARCH_RESTARTED",
                    center,
                    [self._units[restart_position]],
                    comparison_value=self._units[restart_position].index,
                    event_time=retest.end_time,
                    known_at=_confirmed_at(retest),
                )
            )
            exit_position = self._pair_position
            connection_units = self._units[exit_position : retest_position + 1]
            self._connections.append(
                CenterConnection(
                    id=_stable_id("center-connection", self.stream.value, center.id),
                    stream_key=self.stream.value,
                    rule_version=RULE_VERSION,
                    from_center_id=center.id,
                    to_center_id=None,
                    unit_ids=tuple(unit.id for unit in connection_units),
                    exit_unit_id=departure.id,
                    entry_unit_id=None,
                    first_retest_id=retest.id,
                    confirmed_at=_confirmed_at(retest),
                    roles_overlap_seed=center.roles_overlap_seed,
                    source_revision=_source_revision(connection_units),
                )
            )
            self._pending_closed = (self._active_center_position, exit_position)
            self._scan_floor_position = restart_position
            self._active_center_position = None
            self._active_seed_position = None
            self._pair_position = None


class LocalCenterRevisionTracker:
    """Compare immutable decompositions and emit explicit object revisions."""

    def __init__(self, stream: CenterStreamKey, *, left_context_complete: bool = False) -> None:
        self.stream = stream
        self.left_context_complete = left_context_complete
        self._objects: dict[tuple[str, str], dict[str, Any]] = {}
        self._revisions: dict[tuple[str, str], int] = {}

    def apply(
        self, units: Sequence[CenterUnit], *, known_at: int
    ) -> tuple[LocalCenterDecomposition, tuple[CenterObjectRevision, ...]]:
        decomposition = decompose_local_centers(
            units,
            self.stream,
            left_context_complete=self.left_context_complete,
            known_at=known_at,
        )
        current: dict[tuple[str, str], dict[str, Any]] = {
            **{("center", value.id): asdict(value) for value in decomposition.centers},
            **{("connection", value.id): asdict(value) for value in decomposition.connections},
        }
        revisions: list[CenterObjectRevision] = []
        for key in sorted(self._objects.keys() - current.keys()):
            revision = self._revisions.get(key, 0) + 1
            self._revisions[key] = revision
            kind: Literal["center", "connection"] = "center" if key[0] == "center" else "connection"
            revisions.append(
                CenterObjectRevision(
                    object_kind=kind,
                    object_id=key[1],
                    operation="delete",
                    object_revision=revision,
                    known_at=known_at,
                    event_type="CENTER_REVISED",
                    payload=None,
                )
            )
        for key, payload in sorted(current.items()):
            previous = self._objects.get(key)
            if previous == payload:
                continue
            revision = self._revisions.get(key, 0) + 1
            self._revisions[key] = revision
            kind = "center" if key[0] == "center" else "connection"
            revisions.append(
                CenterObjectRevision(
                    object_kind=kind,
                    object_id=key[1],
                    operation="upsert",
                    object_revision=revision,
                    known_at=known_at,
                    event_type=(
                        "SEED_FOUND"
                        if previous is None and key[0] == "center"
                        else "ENTRY_LINKED"
                        if previous is None
                        else "CENTER_REVISED"
                    ),
                    payload=payload,
                )
            )
        self._objects = current
        return decomposition, tuple(revisions)
