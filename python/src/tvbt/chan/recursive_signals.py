from __future__ import annotations

import json
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import replace
from itertools import pairwise
from typing import Any, Literal

from tvbt.chan.recursive import RecursiveDecomposition, RecursiveLine, _identity
from tvbt.chan.recursive_association import derive_trend_association
from tvbt.chan.recursive_proof import ObjectRef, StructureProofError
from tvbt.chan.signals import (
    ChanSignal,
    StructuralCenter,
    chan_divergences,
    chan_trade_points,
)


def _complete_type_thirds(
    lines: Sequence[RecursiveLine],
    projections: Sequence[StructuralCenter],
    center_ids: Sequence[str],
    rows: Mapping[str, dict[str, Any]],
    divergences: list[tuple[str, ChanSignal]],
) -> list[ChanSignal]:
    """First complete type and its actual whole-range pullback, never finer atoms.

    Consolidation is a neutral type: its range can pull back even when its net
    endpoint displacement continues the departure. Segment bootstrap atoms
    still require the opposite segment direction. Direction overrides below
    are formatting roles only; no source fact or canonical owner is changed.
    """
    result: list[ChanSignal] = []
    for ordinal, (center, center_id) in enumerate(zip(projections, center_ids, strict=True)):
        stop = len(lines) - 1
        if ordinal + 1 < len(projections):
            stop = min(stop, projections[ordinal + 1].base_index + 1)
        for index in range(center.seed_end_index + 1, stop):
            departure, retest = lines[index : index + 2]
            upward = departure.direction == "up" and departure.end.price_i64 > center.zg_i64
            downward = departure.direction == "down" and departure.end.price_i64 < center.zd_i64
            if not upward and not downward:
                continue
            returning_is_consolidation = (
                rows.get(retest.object_id, {}).get("movement_type") == "consolidation"
            )
            holds = (
                (
                    retest.range_low_i64 >= center.zg_i64
                    and retest.range_low_i64 < departure.end.price_i64
                    and (returning_is_consolidation or retest.direction == "down")
                )
                if upward
                else (
                    retest.range_high_i64 <= center.zd_i64
                    and retest.range_high_i64 > departure.end.price_i64
                    and (returning_is_consolidation or retest.direction == "up")
                )
            )
            if holds:
                role_lines = list(lines)
                role_lines[index + 1] = RecursiveLine(retest.fact, "down" if upward else "up")
                proof = replace(
                    center,
                    exit_index=index,
                    status="left",
                    leave_direction="up" if upward else "down",
                )
                result.extend(
                    replace(
                        signal, reference_center_ordinal=ordinal + 1, older_center_count=ordinal
                    )
                    for signal in chan_trade_points(role_lines, [proof], [center_id], divergences)
                    if signal.signal_type.endswith("_3")
                )
            break
    return result


def _analysis_views(
    runtime: RecursiveDecomposition,
    by_level: Mapping[int, list[RecursiveLine]],
    rows: Mapping[str, dict[str, Any]],
    all_centers: Sequence[dict[str, Any]],
    cursor: int,
) -> Iterator[tuple[int, list[RecursiveLine], list[dict[str, Any]], str | None]]:
    """Canonical analysis plus proved regroupings; never rebuild parent seeds."""
    graph = runtime.graph
    assert graph is not None
    centers_by_id = {row["object_id"]: row for row in all_centers}
    for level, original in sorted(by_level.items()):
        original = [
            RecursiveLine(
                line.fact,
                "up"
                if line.end.price_i64 > line.start.price_i64
                else "down"
                if line.end.price_i64 < line.start.price_i64
                else line.direction,
            )
            for line in sorted(original, key=lambda line: line.start.bar_index)
        ]
        centers = sorted(
            [row for row in all_centers if row["level"] == level],
            key=lambda row: row["start_bar_index"],
        )
        yield level, original, centers, None
        indices = {line.object_id: index for index, line in enumerate(original)}
        for row in rows.values():
            if row["construction_kind"] != "association" or row["level"] != level - 1:
                continue
            refs = tuple(ObjectRef(**ref) for ref in row["component_refs"])
            view = derive_trend_association(refs, graph, rows, centers_by_id, cursor=cursor)
            if row["object_id"] != view.fact.ref.object_id or row["promotable"] is not False:
                raise StructureProofError("association publication differs from its proof identity")
            if row.get("center_refs") != [
                {"object_id": ref.object_id, "object_revision": ref.object_revision}
                for ref in view.center_refs
            ]:
                raise StructureProofError("association center revisions are stale")
            current = runtime.state()["versions"].get(row["object_id"])
            semantic = {
                key: value
                for key, value in row.items()
                if key not in {"object_revision", "known_at_bar_index"}
            }
            if (
                current is None
                or json.loads(current[0]) != semantic
                or tuple(current[1:]) != (row["object_revision"], row["known_at_bar_index"])
            ):
                raise StructureProofError("association publication revision lacks current evidence")
            if not view.fact.known_at_bar_index <= row["known_at_bar_index"] <= cursor:
                raise StructureProofError("association confirmation predates evidence or is future")
            first, last = indices[refs[0].object_id], indices[refs[-1].object_id]
            if [line.object_id for line in original[first : last + 1]] != [
                ref.object_id for ref in refs
            ]:
                raise StructureProofError("association is not a continuous canonical subsequence")
            grouped = RecursiveLine(
                replace(
                    view.fact,
                    ref=ObjectRef(row["object_id"], row["object_revision"]),
                    known_at_bar_index=row["known_at_bar_index"],
                ),
                view.direction,
            )
            lines = [*original[:first], grouped, *original[last + 1 :]]
            available = {line.object_id for line in lines}
            valid_centers = [
                center
                for center in centers
                if all(ref["object_id"] in available for ref in center["seed_refs"])
            ]
            yield level, lines, valid_centers, grouped.object_id


def recursive_signals(
    runtime: RecursiveDecomposition,
    roots: Sequence[RecursiveLine],
    snapshot: dict[str, list[dict[str, Any]]],
    cursor: int,
    raw_positions: Mapping[int, int] | None = None,
    raw_times: Mapping[int, int] | None = None,
) -> tuple[list[tuple[str, ChanSignal]], list[tuple[str, ChanSignal]]]:
    """A confirms signals over the proven partition; it never splits movements.

    Frozen fixed-layer blocks can be followed by departures and reversals. The
    first such pair is analyzed separately from center/movement construction.
    Canonical consolidations are not relabeled as multi-center trend c proofs.
    """
    graph = runtime.graph
    if graph is None:
        return [], []
    times = {
        anchor.bar_index: anchor.time
        for fact in graph.facts.values()
        for anchor in (fact.start, fact.end)
    }
    if raw_times is not None:
        if any(index in raw_times and raw_times[index] != time for index, time in times.items()):
            raise StructureProofError("signal UTC lattice differs from structural anchors")
        times.update(raw_times)

    def price_anchor(signal: ChanSignal, line: RecursiveLine) -> ChanSignal:
        upward_point = signal.signal_type == "top_divergence" or "sell" in signal.signal_type
        price = line.fact.high_i64 if upward_point else line.fact.low_i64
        source = line.fact.high_source_bar_index if upward_point else line.fact.low_source_bar_index
        if signal.price_i64 != price:
            if signal.price_i64 == line.end.price_i64:
                source = line.end.bar_index
            elif signal.price_i64 == line.start.price_i64:
                source = line.start.bar_index
            else:
                raise StructureProofError("signal price has no actual source anchor")
        if source not in times or source > signal.known_at_bar_index:
            raise StructureProofError("signal price extreme lacks an already known UTC anchor")
        return replace(signal, bar_index=source, time=times[source])

    rows = {row["object_id"]: row for row in snapshot["movements"]}
    versions = runtime.state()["versions"]
    for row in [*rows.values(), *snapshot["centers"]]:
        binding = versions.get(row["object_id"])
        semantic = {
            key: value
            for key, value in row.items()
            if key not in {"object_revision", "known_at_bar_index"}
        }
        if (
            binding is None
            or json.loads(binding[0]) != semantic
            or tuple(binding[1:]) != (row["object_revision"], row["known_at_bar_index"])
        ):
            raise StructureProofError("recursive publication lacks current source evidence")
    by_level: dict[int, list[RecursiveLine]] = {0: list(roots)}
    for row in rows.values():
        if row["status"] != "completed" or row["construction_kind"] != "canonical":
            continue
        ref = ObjectRef(row["object_id"], row["object_revision"])
        graph.verify(ref, cursor=cursor)
        if row["movement_type"] == "unknown":
            raise StructureProofError("unclassified movement cannot be a signal input")
        by_level.setdefault(row["level"] + 1, []).append(
            RecursiveLine(graph.claims[ref.object_id].fact, row["direction"])
        )
    divergences: list[tuple[str, ChanSignal]] = []
    points: list[tuple[str, ChanSignal]] = []
    for level, lines, centers, association_id in _analysis_views(
        runtime, by_level, rows, snapshot["centers"], cursor
    ):
        indices = {line.object_id: i for i, line in enumerate(lines)}
        projections: list[StructuralCenter] = []
        for ordinal, center in enumerate(centers):
            seed = [indices[ref["object_id"]] for ref in center["seed_refs"]]
            # Only the first external departure and its adjacent reverse unit
            # may confirm this context; a later return cannot repair that pair.
            exit_index = None
            stop = len(lines) - 1
            if ordinal + 1 < len(centers):
                stop = min(stop, indices[centers[ordinal + 1]["seed_refs"][0]["object_id"]] + 1)
            for index in range(seed[-1] + 1, stop):
                line, reverse = lines[index : index + 2]
                leaves = (line.direction == "up" and line.end.price_i64 > center["zg_i64"]) or (
                    line.direction == "down" and line.end.price_i64 < center["zd_i64"]
                )
                if leaves:
                    if line.direction != reverse.direction:
                        exit_index = index
                    break
            previous = centers[ordinal - 1] if ordinal else None
            relation: Literal["UP", "DOWN", "OVERLAP", "UNKNOWN"] = "UNKNOWN"
            if previous is not None:
                if center["zd_i64"] > previous["zg_i64"] and center["dd_i64"] > previous["gg_i64"]:
                    relation = "UP"
                elif (
                    center["zg_i64"] < previous["zd_i64"] and center["gg_i64"] < previous["dd_i64"]
                ):
                    relation = "DOWN"
                else:
                    relation = "OVERLAP"
            projections.append(
                StructuralCenter(
                    seed[0],
                    seed[-1],
                    seed[-1],
                    exit_index,
                    center["start_bar_index"],
                    center["end_bar_index"],
                    center["start_time"],
                    center["end_time"],
                    center["zd_i64"],
                    center["zg_i64"],
                    center["known_at_bar_index"],
                    "left" if exit_index is not None else "extended",
                    None if exit_index is None else lines[exit_index].direction,
                    formation_dir="UP" if center["formation_dir"] == "up" else "DOWN",
                    relative_dir=relation,
                    comparison_dd_i64=center["dd_i64"],
                    comparison_gg_i64=center["gg_i64"],
                )
            )
        center_ids = [row["object_id"] for row in centers]
        positions = {} if raw_positions is None else raw_positions
        local = []
        for signal in chan_divergences(
            lines,
            projections,
            center_ids,
            {},
            raw_positions=positions,
            strength_profile="price_displacement_speed_v1",
        ):
            if (
                association_id is not None
                and lines[signal.segment_index].object_id != association_id
            ):
                continue
            reverse_index = signal.segment_index + 1
            if reverse_index >= len(lines):
                continue
            reverse = lines[reverse_index]
            if (
                reverse.end.price_i64 == reverse.start.price_i64
                or reverse.direction == lines[signal.segment_index].direction
            ):
                continue
            current = lines[signal.segment_index]
            c = rows.get(current.object_id)
            c_centers = (
                []
                if c is None
                else [item for item in snapshot["centers"] if item["object_id"] in c["center_ids"]]
            )
            departure_id = retest_id = None
            if (
                c is not None
                and c["construction_kind"] == "association"
                and signal.divergence_kind == "trend"
            ):
                boundary = next(
                    item for item in centers if item["object_id"] == signal.reference_object_id
                )
                # B's actual sublevel types are the same-level canonical
                # members of this c, not their finer atoms. Never promote a
                # lower-atom return into a B-level third-point certificate.
                children = [graph.facts[ref["object_id"]] for ref in c["component_refs"]]
                for left, right in pairwise(children):
                    upward = current.direction == "up"
                    leaves = (
                        (
                            left.end.price_i64 > boundary["zg_i64"]
                            and left.end.price_i64 > left.start.price_i64
                        )
                        if upward
                        else (
                            left.end.price_i64 < boundary["zd_i64"]
                            and left.end.price_i64 < left.start.price_i64
                        )
                    )
                    if not leaves:
                        continue
                    holds = (
                        (right.low_i64 < left.end.price_i64 and right.low_i64 >= boundary["zg_i64"])
                        if upward
                        else (
                            right.high_i64 > left.end.price_i64
                            and right.high_i64 <= boundary["zd_i64"]
                        )
                    )
                    if holds:
                        departure_id, retest_id = left.ref.object_id, right.ref.object_id
                    break
            meets = (
                c is not None
                and len(c_centers) >= 2
                and all(
                    item["level"] == level - 1
                    and item["known_at_bar_index"] <= current.known_at_bar_index
                    for item in c_centers
                )
            )
            reversal_holds_extreme = (
                reverse.fact.low_i64 >= current.fact.low_i64
                if current.direction == "down"
                else reverse.fact.high_i64 <= current.fact.high_i64
            )
            standard = (
                signal.divergence_kind == "trend"
                and meets
                and retest_id is not None
                and reversal_holds_extreme
            )
            dependency_known = [current.known_at_bar_index, reverse.known_at_bar_index]
            reference_id = signal.comparison_reference_object_id
            if reference_id in indices:
                dependency_known.append(lines[indices[reference_id]].known_at_bar_index)
            dependency_known.extend(
                center["known_at_bar_index"]
                for center in centers
                if center["object_id"]
                in {signal.reference_object_id, signal.a_center_id, signal.b_center_id}
            )
            dependency_known.extend(center["known_at_bar_index"] for center in c_centers)
            signal = replace(
                signal,
                known_at_bar_index=max(signal.known_at_bar_index, *dependency_known),
                follow_through_object_id=reverse.object_id,
                follow_through_status="observed",
                level_id=f"L{level}",
                evidence_profile="fixed_same_level_recursive_v2",
                center_chain_profile="fixed_same_level_recursive_v2",
                status="confirmed"
                if standard
                else "candidate"
                if not reversal_holds_extreme and signal.divergence_kind != "center_oscillation"
                else signal.status,
                invalidation_reason="reversal_crossed_current_extreme"
                if not reversal_holds_extreme and signal.divergence_kind != "center_oscillation"
                else signal.invalidation_reason,
                divergence_profile="standard_trend" if standard else signal.divergence_profile,
                c_meets_sublevel=meets
                if c is not None and signal.divergence_kind == "trend"
                else None,
                c_contains_type3=(retest_id is not None)
                if c is not None and signal.divergence_kind == "trend"
                else None,
                c_sublevel_center_ids=tuple(item["object_id"] for item in c_centers)
                if signal.divergence_kind == "trend"
                else (),
                c_type3_departure_id=departure_id,
                c_type3_retest_id=retest_id,
                c_proof_known_at_bar_index=current.known_at_bar_index
                if c is not None and signal.divergence_kind == "trend"
                else None,
                c_sublevel_profile="recursive_completed_movement_type3_v2"
                if signal.divergence_kind == "trend" and c is not None
                else None,
            )
            signal = price_anchor(signal, current)
            object_id = _identity(
                "recursive-divergence",
                runtime.scope.decomposition_id,
                level,
                signal.divergence_kind,
                signal.reference_object_id,
                lines[signal.segment_index].object_id,
            )
            local.append((object_id, signal))
        divergences.extend(local)
        trade_signals = [
            signal
            for signal in chan_trade_points(lines, projections, center_ids, local)
            if not signal.signal_type.endswith("_3")
        ]
        if association_id is None:
            trade_signals.extend(_complete_type_thirds(lines, projections, center_ids, rows, local))
        for signal in trade_signals:
            if association_id is not None and signal.signal_type.endswith("_3"):
                # Third points belong to the canonical stream's first pair;
                # grouping must not mint alternative first-return histories.
                continue
            dependencies = [lines[signal.segment_index].known_at_bar_index]
            for key in (signal.departure_object_id, signal.return_object_id):
                if key in indices:
                    dependencies.append(lines[indices[key]].known_at_bar_index)
            dependencies.extend(
                center["known_at_bar_index"]
                for center in centers
                if center["object_id"] == signal.reference_object_id
            )
            signal = replace(
                signal, known_at_bar_index=max(signal.known_at_bar_index, *dependencies)
            )
            signal = price_anchor(signal, lines[signal.segment_index])
            signal = replace(
                signal, level_id=f"L{level}", evidence_profile="fixed_same_level_recursive_v2"
            )
            points.append(
                (
                    _identity(
                        "recursive-trade-point",
                        runtime.scope.decomposition_id,
                        level,
                        signal.signal_type,
                        lines[signal.segment_index].object_id,
                        signal.reference_object_id,
                    ),
                    signal,
                )
            )
    return divergences, points
