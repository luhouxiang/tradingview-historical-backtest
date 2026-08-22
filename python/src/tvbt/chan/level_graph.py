from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from itertools import pairwise
from typing import Literal

CenterStatus = Literal["confirmed", "extended", "left", "promoted", "terminated"]
MovementClassification = Literal[
    "consolidation", "uptrend", "downtrend", "higher_level_center_candidate"
]


def _stable_id(prefix: str, *parts: object) -> str:
    data = json.dumps(parts, ensure_ascii=False, separators=(",", ":"), default=str).encode()
    return f"{prefix}-{hashlib.sha256(data).hexdigest()[:20]}"


@dataclass(frozen=True)
class GraphCenter:
    """A causally known center used by the recursive structural-level classifier."""

    object_id: str
    level_id: str
    start_bar_index: int
    start_time: int
    end_bar_index: int
    end_time: int
    zd_i64: int
    zg_i64: int
    dd_i64: int
    gg_i64: int
    component_kind: str
    component_object_ids: tuple[str, ...]
    component_known_at: tuple[int, ...]
    status: CenterStatus
    confirmed_at_bar_index: int
    known_at_bar_index: int


@dataclass(frozen=True)
class LevelGraph:
    centers: tuple[tuple[str, dict[str, object], int], ...]
    movements: tuple[tuple[str, dict[str, object], int], ...]


def center_relation(previous: GraphCenter, current: GraphCenter) -> str:
    """Compare complete fluctuation envelopes; touching envelopes overlap."""
    if current.dd_i64 > previous.gg_i64:
        return "up"
    if current.gg_i64 < previous.dd_i64:
        return "down"
    return "overlap"


def _next_level(level_id: str) -> str:
    if not level_id.startswith("L") or not level_id[1:].isdigit():
        raise ValueError(f"invalid structural level: {level_id}")
    return f"L{int(level_id[1:]) + 1}"


def _classify(centers: list[GraphCenter]) -> MovementClassification:
    if len(centers) == 1:
        return "consolidation"
    relations = [center_relation(left, right) for left, right in pairwise(centers)]
    if all(relation == "up" for relation in relations):
        return "uptrend"
    if all(relation == "down" for relation in relations):
        return "downtrend"
    return "higher_level_center_candidate"


def _movement_spec(centers: list[GraphCenter]) -> tuple[str, dict[str, object], int]:
    classification = _classify(centers)
    first = centers[0]
    last = centers[-1]
    object_id = _stable_id("level-movement", first.level_id, first.object_id)
    prior = _classify(centers[:-1]) if len(centers) > 1 else None
    reclassified = prior is not None and prior != classification
    terminal = last.status in {"left", "terminated"}
    direction = (
        "up" if classification == "uptrend" else "down" if classification == "downtrend" else None
    )
    payload: dict[str, object] = {
        "level_id": first.level_id,
        "start_bar_index": first.start_bar_index,
        "start_time": first.start_time,
        "end_bar_index": last.end_bar_index,
        "end_time": last.end_time,
        "low_i64": min(center.dd_i64 for center in centers),
        "high_i64": max(center.gg_i64 for center in centers),
        "component_center_ids": [center.object_id for center in centers],
        "classification": classification,
        "direction": direction,
        "status": "confirmed" if terminal else "reclassified" if reclassified else "candidate",
        "previous_classification": prior if reclassified else None,
        "reclassification_reason": "full_envelope_relation" if reclassified else None,
        "parent_center_candidate_id": None,
        "catalog_event": "movement_reclassified"
        if reclassified
        else "movement_confirmed"
        if terminal
        else "movement_candidate",
        "catalog_algorithm_id": "ALG-GEO-006",
        "confirmed": terminal,
        "confirmed_at_bar_index": last.known_at_bar_index if terminal else None,
    }
    return object_id, payload, last.known_at_bar_index


def _promote_nine_component_center(
    center: GraphCenter,
) -> tuple[str, dict[str, object], int] | None:
    if center.level_id != "L0" or len(center.component_object_ids) < 9:
        return None
    level_id = _next_level(center.level_id)
    object_id = _stable_id("level-center", level_id, "nine", center.object_id)
    confirmed_at = center.component_known_at[8]
    if center.status == "left":
        status = "terminated"
        catalog_event = "center_terminated"
    elif len(center.component_object_ids) > 9:
        status = "extended"
        catalog_event = "center_extended"
    else:
        status = "promoted"
        catalog_event = "center_promoted"
    payload: dict[str, object] = {
        "level_id": level_id,
        "parent_level_id": center.level_id,
        "start_bar_index": center.start_bar_index,
        "start_time": center.start_time,
        "end_bar_index": center.end_bar_index,
        "end_time": center.end_time,
        "zd_i64": center.zd_i64,
        "zg_i64": center.zg_i64,
        "dd_i64": center.dd_i64,
        "gg_i64": center.gg_i64,
        "component_kind": "segment",
        "component_object_ids": list(center.component_object_ids),
        "source_center_ids": [center.object_id],
        "status": status,
        "promotion_reason": "nine_component_extension",
        "promoted_from_center_id": center.object_id,
        "catalog_event": catalog_event,
        "catalog_algorithm_id": "ALG-GEO-005",
        "confirmed": True,
        "confirmed_at_bar_index": confirmed_at,
    }
    return object_id, payload, center.known_at_bar_index


def build_level_graph(base_centers: list[GraphCenter]) -> LevelGraph:
    """Build L1 promotions and recursively classify every confirmed structural level."""
    if any(center.level_id != "L0" for center in base_centers):
        raise ValueError("base_centers must contain only L0 centers")
    ordered_base = sorted(base_centers, key=lambda item: (item.start_bar_index, item.object_id))
    center_specs: list[tuple[str, dict[str, object], int]] = []
    movement_specs: list[tuple[str, dict[str, object], int]] = []
    levels: dict[str, list[GraphCenter]] = {"L0": ordered_base}

    promoted: list[GraphCenter] = []
    for source in ordered_base:
        spec = _promote_nine_component_center(source)
        if spec is None:
            continue
        center_specs.append(spec)
        object_id, payload, known_at = spec
        confirmed_at = payload["confirmed_at_bar_index"]
        if not isinstance(confirmed_at, int):
            raise TypeError("promoted center must have a confirmation bar")
        promoted.append(
            GraphCenter(
                object_id=object_id,
                level_id=str(payload["level_id"]),
                start_bar_index=source.start_bar_index,
                start_time=source.start_time,
                end_bar_index=source.end_bar_index,
                end_time=source.end_time,
                zd_i64=source.zd_i64,
                zg_i64=source.zg_i64,
                dd_i64=source.dd_i64,
                gg_i64=source.gg_i64,
                component_kind="sublevel_movement",
                component_object_ids=(source.object_id,),
                component_known_at=(known_at,),
                status="promoted" if source.status != "left" else "terminated",
                confirmed_at_bar_index=confirmed_at,
                known_at_bar_index=known_at,
            )
        )
    if promoted:
        levels["L1"] = promoted

    for level_id in sorted(levels, key=lambda value: int(value[1:])):
        centers = levels[level_id]
        if not centers:
            continue
        movement = _movement_spec(centers)
        movement_specs.append(movement)
        movement_id, movement_payload, _ = movement
        if movement_payload["classification"] != "higher_level_center_candidate":
            continue
        overlap = [
            center
            for index, center in enumerate(centers)
            if index == 0 or center_relation(centers[index - 1], center) == "overlap"
        ]
        if len(overlap) < 2:
            overlap = centers
        next_level = _next_level(level_id)
        first, last = overlap[0], overlap[-1]
        object_id = _stable_id("level-center", next_level, "overlap", first.object_id)
        zd_i64 = max(center.dd_i64 for center in overlap)
        zg_i64 = min(center.gg_i64 for center in overlap)
        if zd_i64 > zg_i64:
            continue
        payload = {
            "level_id": next_level,
            "parent_level_id": level_id,
            "start_bar_index": first.start_bar_index,
            "start_time": first.start_time,
            "end_bar_index": last.end_bar_index,
            "end_time": last.end_time,
            "zd_i64": zd_i64,
            "zg_i64": zg_i64,
            "dd_i64": min(center.dd_i64 for center in overlap),
            "gg_i64": max(center.gg_i64 for center in overlap),
            "component_kind": "sublevel_movement",
            "component_object_ids": [movement_id],
            "source_center_ids": [center.object_id for center in overlap],
            "status": "candidate",
            "promotion_reason": "overlapping_fluctuation_ranges",
            "promoted_from_center_id": None,
            "catalog_event": "center_candidate",
            "catalog_algorithm_id": "ALG-GEO-005",
            "confirmed": False,
            "confirmed_at_bar_index": None,
        }
        center_specs.append((object_id, payload, last.known_at_bar_index))
        movement_payload["parent_center_candidate_id"] = object_id

    return LevelGraph(tuple(center_specs), tuple(movement_specs))
