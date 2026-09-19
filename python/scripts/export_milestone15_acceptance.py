from __future__ import annotations

import argparse
import json
import sys
import threading
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pyarrow.parquet as pq

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "python" / "src"))

from tvbt.chan.algorithm import definition, run_chan  # noqa: E402
from tvbt.chan.checkpoint import dump_checkpoint, load_checkpoint  # noqa: E402
from tvbt.chan.engine import ChanEngine, RawBar  # noqa: E402
from tvbt.storage.path_guard import PathGuard  # noqa: E402

DATASET_ID = "SHFE.AOL9.5m"
DATA_REVISION = "sha256:2904362e62173a418d63feaf8855a3aef4b61ce5b0027e7801785baf9d8302fe"
TRADE_ID = "TRADE-5101-5103"
ENTRY_SIGNAL_ID = "CHAN-B3-5100-open_long-6820c728"
TARGET_BAR_INDEX = 5101
PREFIX_CUTOFF = 5100
VALIDATION_END = 5200


def _iso_local(timestamp_ms: int | None) -> str | None:
    if timestamp_ms is None:
        return None
    return (
        datetime.fromtimestamp(timestamp_ms / 1000, UTC)
        .astimezone(ZoneInfo("Asia/Shanghai"))
        .isoformat(timespec="minutes")
    )


def _read_rows(path: Path) -> list[dict[str, Any]]:
    return pq.read_table(path).to_pylist()


def _single(rows: list[dict[str, Any]], key: str, value: object) -> dict[str, Any]:
    matches = [row for row in rows if row.get(key) == value]
    if len(matches) != 1:
        raise AssertionError(f"expected one {key}={value!r}, found {len(matches)}")
    return matches[0]


def _payload(data_root: Path) -> dict[str, Any]:
    dataset_dir = data_root / "normalized" / DATASET_ID / DATA_REVISION[7:19]
    algorithm = definition()
    return {
        "dataset": {
            "dataset_id": DATASET_ID,
            "data_revision": DATA_REVISION,
            "bars_path": (dataset_dir / "bars.parquet").relative_to(data_root).as_posix(),
            "meta_path": (dataset_dir / "meta.json").relative_to(data_root).as_posix(),
        },
        "algorithm": {
            key: algorithm[key]
            for key in ("kind", "algorithm_id", "algorithm_version", "source_hash")
        },
        "parameters": {
            "checkpoint_interval": 1024,
            "center_boundary_profile": "local_center_boundary_v1",
        },
        "calculation_mode": "causal_events",
    }


def _raw_bars(path: Path, start: int, end: int) -> list[RawBar]:
    rows = _read_rows(path)
    return [
        RawBar(
            bar_index=int(row["bar_index"]),
            time=int(row["timestamp_utc"]),
            open_i64=int(row["open_i64"]),
            high_i64=int(row["high_i64"]),
            low_i64=int(row["low_i64"]),
            close_i64=int(row["close_i64"]),
        )
        for row in rows
        if start <= int(row["bar_index"]) <= end
    ]


def _assert_non_overlapping(centers: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    streams = sorted({str(center["stream_key"]) for center in centers})
    for stream in streams:
        current = [center for center in centers if center["stream_key"] == stream]
        open_centers = [center for center in current if center["body_end_time"] is None]
        if len(open_centers) > 1:
            raise AssertionError(f"stream {stream} has more than one open center")
        ordered = sorted(
            current,
            key=lambda center: int(center["body_start_time"]),
        )
        for previous, following in pairwise(ordered):
            if previous["body_end_time"] is None or int(previous["body_end_time"]) > int(
                following["body_start_time"]
            ):
                raise AssertionError(
                    f"same-stream center bodies overlap: {previous['object_id']} and "
                    f"{following['object_id']}"
                )
        counts[stream] = len(current)
    return counts


def _assert_center_relations(centers: list[dict[str, Any]]) -> None:
    streams: dict[str, list[dict[str, Any]]] = {}
    for center in centers:
        streams.setdefault(center["stream_key"], []).append(center)
    for current in streams.values():
        previous: dict[str, Any] | None = None
        for center in sorted(current, key=lambda value: value["body_start_time"]):
            relation = None
            review = False
            if previous is not None:
                relation = (
                    "CORE_ABOVE"
                    if center["zd_i64"] > previous["zg_i64"]
                    else "CORE_BELOW"
                    if center["zg_i64"] < previous["zd_i64"]
                    else "CORE_TOUCH_OR_OVERLAP"
                )
                review = relation != "CORE_TOUCH_OR_OVERLAP" and not (
                    center["observed_low_i64"] > previous["observed_high_i64"]
                    or center["observed_high_i64"] < previous["observed_low_i64"]
                )
            expected = {
                "previous_center_id": None if previous is None else previous["object_id"],
                "core_relation": relation,
                "higher_level_review_required": review,
                "trend_status": "UNVERIFIED",
            }
            if any(name not in center or center[name] != value for name, value in expected.items()):
                raise AssertionError(f"same-stream core relation is invalid: {center['object_id']}")
            previous = center


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, default=PROJECT_ROOT / "trading-data")
    parser.add_argument("--full-cache-ref", help="data_root-relative final full-history Chan cache")
    parser.add_argument(
        "--run-id",
        default="job-20260913T022904000000000-f841eea6ab73c66bf8af",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=(
            PROJECT_ROOT / "trading-data" / "acceptance" / "milestone15-aol9-center-boundary.json"
        ),
    )
    args = parser.parse_args()
    data_root = args.data_root.resolve(strict=True)
    guard = PathGuard(data_root)
    payload = _payload(data_root)
    bars_path = guard.resolve(payload["dataset"]["bars_path"])
    run_dir = guard.resolve(f"runs/{args.run_id}")

    run_manifest = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    if run_manifest["dataset"] != {
        "dataset_id": DATASET_ID,
        "data_revision": DATA_REVISION,
    }:
        raise AssertionError("preserved run does not reference the accepted AOL9 revision")
    trade = _single(_read_rows(run_dir / "trades.parquet"), "trade_id", TRADE_ID)
    event_rows = _read_rows(run_dir / "chart_events.parquet")
    signal_event = _single(event_rows, "object_id", ENTRY_SIGNAL_ID)
    evidence = json.loads(signal_event["payload_json"])
    target_bar = _single(_read_rows(bars_path), "bar_index", TARGET_BAR_INDEX)

    expected = {
        "source_center_id": "local-center-7f07eec9ff00be0036cb",
        "source_center_start_bar_index": 3479,
        "source_center_end_bar_index": 4784,
        "source_center_zd_i64": 2926,
        "source_center_zg_i64": 2970,
        "departure_start_bar_index": 4861,
        "departure_end_bar_index": 4944,
        "return_start_bar_index": 4944,
        "return_end_bar_index": 5017,
        "return_low_i64": 3085,
        "return_clearance_above_zg_i64": 115,
        "b3_confirmed_at_bar_index": PREFIX_CUTOFF,
        "entry_volume": 1153,
        "minimum_entry_volume": 0,
    }
    mismatches = {
        key: {"expected": value, "actual": evidence.get(key)}
        for key, value in expected.items()
        if evidence.get(key) != value
    }
    if mismatches:
        raise AssertionError(f"historical B3 evidence changed: {mismatches}")
    if (
        trade["entry_bar_index"] != TARGET_BAR_INDEX
        or target_bar["timestamp_utc"] != trade["entry_time"]
        or trade["entry_price_i64"] != 3103
    ):
        raise AssertionError("target trade is not the 2023-09-07 09:05 next-open fill")

    prefix, _, checkpoint_bytes = run_chan(
        payload,
        guard,
        threading.Event(),
        last_bar_index=PREFIX_CUTOFF,
        write_checkpoints=True,
    )
    validation, _, _ = run_chan(
        payload,
        guard,
        threading.Event(),
        last_bar_index=VALIDATION_END,
    )
    prefix_events = [event.row() for event in prefix.emitter.events]
    validation_prefix_events = [
        event.row()
        for event in validation.emitter.events
        if event.known_at_bar_index <= PREFIX_CUTOFF
    ]
    if prefix_events != validation_prefix_events:
        raise AssertionError("real AOL9 causal event prefix changed after later bars were loaded")

    encoded = dump_checkpoint(prefix.algorithm_version, PREFIX_CUTOFF, prefix.export_state())
    restored_index, restored_state = load_checkpoint(encoded, prefix.algorithm_version)
    if restored_index != PREFIX_CUTOFF:
        raise AssertionError("checkpoint bar index changed during serialization")
    restored = ChanEngine.from_state(restored_state)
    for bar in _raw_bars(bars_path, PREFIX_CUTOFF + 1, VALIDATION_END):
        restored.update(bar)
    resumed_events = [*prefix_events, *(event.row() for event in restored.emitter.events)]
    uninterrupted_events = [event.row() for event in validation.emitter.events]
    if resumed_events != uninterrupted_events:
        difference = next(
            (
                index
                for index, (left, right) in enumerate(
                    zip(resumed_events, uninterrupted_events, strict=False)
                )
                if left != right
            ),
            min(len(resumed_events), len(uninterrupted_events)),
        )
        raise AssertionError(
            "real AOL9 resumed events differ at position "
            f"{difference}: resumed={len(resumed_events)}, "
            f"uninterrupted={len(uninterrupted_events)}"
        )
    # JSON arrays restore as lists even when the in-memory source used tuples.
    if json.dumps(restored.result_rows(), sort_keys=True) != json.dumps(
        validation.result_rows(), sort_keys=True
    ):
        differences = [
            key
            for key, rows in validation.result_rows().items()
            if restored.result_rows()[key] != rows
        ]
        details = {}
        for key in differences:
            left = {row["object_id"]: row for row in restored.result_rows()[key]}
            right = {row["object_id"]: row for row in validation.result_rows()[key]}
            for object_id in left.keys() | right.keys():
                if left.get(object_id) != right.get(object_id):
                    details[key] = {"left": left.get(object_id), "right": right.get(object_id)}
                    break
        raise AssertionError(
            f"real AOL9 restored object snapshot differs: {differences}, {details}"
        )

    algorithm = payload["algorithm"]
    validation_rows = validation.result_rows()
    local_centers = validation_rows["local_centers"]
    connections = validation_rows["center_connections"]
    audits = validation_rows["center_audit_events"]
    stream_counts = _assert_non_overlapping(local_centers)
    _assert_center_relations(local_centers)
    preview_events = []
    preview_ids: set[str] = set()
    for event in validation.emitter.events:
        if event.object_type != "center_audit_event" or event.operation != "upsert":
            continue
        preview_payload = json.loads(event.payload_json)
        if preview_payload.get("event_type") != "PREVIEW_UPDATED":
            continue
        if preview_payload.get("preview_confirmed") is not False:
            raise AssertionError("real preview was marked as confirmed")
        preview_ids.add(event.object_id)
        preview_events.append({**event.row(), "payload": preview_payload})
    if not preview_events:
        raise AssertionError("real AOL9 prefix did not exercise the production preview stream")
    preview_withdrawals = [
        event.row()
        for event in validation.emitter.events
        if event.object_id in preview_ids and event.operation == "delete"
    ]
    old_cache_versions = sorted(
        {
            json.loads(path.read_text(encoding="utf-8"))
            .get("algorithm", {})
            .get("algorithm_version", "")
            for path in (data_root / "cache" / "chan").glob("*/manifest.json")
            if json.loads(path.read_text(encoding="utf-8")).get("schema_version") != 5
        }
    )
    if algorithm["algorithm_version"] in old_cache_versions:
        raise AssertionError("current algorithm version was found in a pre-schema-5 cache")

    relevant_centers = [
        center
        for center in local_centers
        if int(center["observed_start_bar_index"]) <= TARGET_BAR_INDEX
        and int(center["observed_end_bar_index"]) >= 3479
        and center["unit_kind"] in {"BI", "SEGMENT"}
    ]
    relevant_ids = {center["object_id"] for center in relevant_centers}
    relevant_connections = [
        row
        for row in connections
        if row["from_center_id"] in relevant_ids or row["to_center_id"] in relevant_ids
    ]
    relevant_audits = [
        row
        for row in audits
        if row["center_id"] in relevant_ids and int(row["known_at_bar_index"]) <= TARGET_BAR_INDEX
    ]
    unit_roles: dict[str, list[str]] = {}
    for center in relevant_centers:
        for ordinal, unit_id in enumerate(center["seed_ids"], 1):
            unit_roles.setdefault(unit_id, []).append(f"{center['object_id']}:seed_{ordinal}")
        for role in ("entry_id", "exit_id", "first_retest_id", "pending_exit_id"):
            if center.get(role):
                unit_roles.setdefault(center[role], []).append(f"{center['object_id']}:{role}")
    for connection in relevant_connections:
        for unit_id in connection["ordered_unit_ids"]:
            unit_roles.setdefault(unit_id, []).append(f"{connection['object_id']}:connection")
    unit_table = [
        {
            **line,
            "unit_kind": kind,
            "start_local_time": _iso_local(line["start_time"]),
            "end_local_time": _iso_local(line["end_time"]),
            "roles": unit_roles.get(line["object_id"], []),
        }
        for kind, key in (("BI", "bi"), ("SEGMENT", "segments"))
        for line in validation_rows[key]
        if line["object_id"] in unit_roles
        or (int(line["start_bar_index"]) <= TARGET_BAR_INDEX and int(line["end_bar_index"]) >= 3479)
    ]

    report = {
        "accepted_at": datetime.now(UTC).isoformat(),
        "dataset": {"dataset_id": DATASET_ID, "data_revision": DATA_REVISION},
        "target_trade": {
            "trade_id": TRADE_ID,
            "entry_signal_id": ENTRY_SIGNAL_ID,
            "signal_known_at_bar_index": trade["entry_signal_known_at_bar_index"],
            "fill_bar_index": trade["entry_bar_index"],
            "fill_time": _iso_local(trade["entry_time"]),
            "fill_price_i64": trade["entry_price_i64"],
            "evidence": evidence,
            "plain_explanation": (
                "来源线段中枢 K3479-K4784，核心 ZD=2926、ZG=2970；"
                "向上离开段 K4861-K4944 从 3049 升至 3153；"
                "首次回试段 K4944-K5017 在 2023-09-06 09:50 结束，"
                "完整段最低 3085，比 ZG 高 115；K5100 收盘确认，"
                "K5101（2023-09-07 09:05）开盘以 3103 成交。"
            ),
        },
        "current_engine": {
            "algorithm": algorithm,
            "checkpoint_indices": sorted(checkpoint_bytes),
            "prefix_cutoff": PREFIX_CUTOFF,
            "validation_end": VALIDATION_END,
            "prefix_event_count": len(prefix_events),
            "validation_event_count": len(validation.emitter.events),
            "prefix_invariant": True,
            "checkpoint_continuation_equal": True,
            "same_stream_body_non_overlap": True,
            "same_stream_core_relations_valid": True,
            "stream_center_counts": stream_counts,
            "old_cache_versions_not_reused": old_cache_versions,
        },
        "local_decomposition_near_trade": {
            "unit_explanation_table": unit_table,
            "centers": relevant_centers,
            "connections": relevant_connections,
            "audit_events": relevant_audits,
            "preview_stream": {
                "non_tradable": True,
                "upsert_count": len(preview_events),
                "withdrawal_count": len(preview_withdrawals),
                "events": preview_events,
                "withdrawals": preview_withdrawals,
            },
        },
    }
    if args.full_cache_ref:
        directory = guard.resolve(args.full_cache_ref)
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        if (
            manifest.get("schema_version") != 5
            or manifest.get("dataset_id") != DATASET_ID
            or manifest.get("data_revision") != DATA_REVISION
            or manifest.get("algorithm") != algorithm
            or manifest.get("coverage", {}).get("bar_count") != 71149
            or not (directory / "_SUCCESS").is_file()
        ):
            raise AssertionError("full-history cache does not match the final accepted identity")
        full_centers = _read_rows(directory / "local_centers.parquet")
        full_stream_counts = _assert_non_overlapping(full_centers)
        _assert_center_relations(full_centers)
        lines = {
            line["object_id"]: line
            for name in ("bi", "segments")
            for line in _read_rows(directory / f"{name}.parquet")
        }
        full_connections = _read_rows(directory / "center_connections.parquet")
        centers_by_id = {center["object_id"]: center for center in full_centers}
        ordered_lines = {
            kind: sorted(
                [
                    line
                    for line in _read_rows(directory / f"{name}.parquet")
                    if line.get("confirmed_at_bar_index") is not None
                ],
                key=lambda line: (line["start_time"], line["object_id"]),
            )
            for kind, name in (("BI", "bi"), ("SEGMENT", "segments"))
        }
        positions = {
            kind: {line["object_id"]: index for index, line in enumerate(current)}
            for kind, current in ordered_lines.items()
        }
        full_roles: dict[str, list[str]] = {}
        for center in full_centers:
            seeds = [lines[object_id] for object_id in center["seed_ids"]]
            for ordinal, unit_id in enumerate(center["seed_ids"], 1):
                full_roles.setdefault(unit_id, []).append(f"{center['object_id']}:seed_{ordinal}")
            for role in ("entry_id", "exit_id", "first_retest_id", "pending_exit_id"):
                if center.get(role):
                    full_roles.setdefault(center[role], []).append(f"{center['object_id']}:{role}")
            zd = max(line["range_low_i64"] for line in seeds)
            zg = min(line["range_high_i64"] for line in seeds)
            if not zd < zg or (zd, zg) != (center["zd_i64"], center["zg_i64"]):
                raise AssertionError(f"fixed seed core is invalid: {center['object_id']}")
            if center["body_start_time"] != seeds[0]["start_time"]:
                raise AssertionError(f"body start differs from first seed: {center['object_id']}")
            if center["status"] == "CLOSED":
                exit_line = lines[center["exit_id"]]
                retest = lines[center["first_retest_id"]]
                indexes = positions[center["unit_kind"]]
                if indexes[center["first_retest_id"]] != indexes[center["exit_id"]] + 1:
                    raise AssertionError(
                        f"retest is not immediately adjacent: {center['object_id']}"
                    )
                if center["break_confirmed_at_bar_index"] != retest["confirmed_at_bar_index"]:
                    raise AssertionError(
                        f"break confirmation differs from retest: {center['object_id']}"
                    )
                expected_end = max(seeds[-1]["end_time"], exit_line["start_time"])
                if center["body_end_time"] != expected_end:
                    raise AssertionError(f"closed body end is invalid: {center['object_id']}")
                valid_break = (
                    retest["direction"] == "down" and retest["range_low_i64"] > zg
                    if center["break_direction"] == "up"
                    else retest["direction"] == "up" and retest["range_high_i64"] < zd
                )
                if not valid_break:
                    raise AssertionError(f"confirmed retest touches core: {center['object_id']}")
        for connection in full_connections:
            previous = centers_by_id[connection["from_center_id"]]
            following = centers_by_id.get(connection["to_center_id"])
            current = ordered_lines[connection["unit_kind"]]
            indexes = positions[connection["unit_kind"]]
            start = indexes[previous["exit_id"]]
            stop = len(current) if following is None else indexes[following["seed_ids"][0]]
            expected_ids = [line["object_id"] for line in current[start:stop]]
            if connection["ordered_unit_ids"] != expected_ids:
                raise AssertionError(
                    f"connection omits or duplicates units: {connection['object_id']}"
                )
            overlap = previous["roles_overlap_seed"] or (
                following is not None
                and (
                    previous["first_retest_id"] in following["seed_ids"]
                    or previous["exit_id"] in following["seed_ids"]
                )
            )
            if connection["roles_overlap_seed"] != overlap:
                raise AssertionError(
                    f"connection seed-role overlap is invalid: {connection['object_id']}"
                )
            if following is not None:
                if following["scan_floor"] != indexes[previous["first_retest_id"]]:
                    raise AssertionError(
                        f"new search did not restart at retest: {following['object_id']}"
                    )
                if set(previous["seed_ids"]) & set(following["seed_ids"]):
                    raise AssertionError(f"closed seeds reused: {following['object_id']}")
            for unit_id in connection["ordered_unit_ids"]:
                full_roles.setdefault(unit_id, []).append(f"{connection['object_id']}:connection")
        report["full_history_audit"] = {
            "cache_ref": args.full_cache_ref,
            "bar_count": 71149,
            "fixed_seed_cores_valid": True,
            "closed_body_end_rule_valid": True,
            "strict_retest_break_valid": True,
            "adjacent_retest_confirmation_valid": True,
            "ordered_connection_units_valid": True,
            "scan_restart_and_seed_non_reuse_valid": True,
            "seed_role_overlap_valid": True,
            "same_stream_core_relations_valid": True,
            "same_stream_body_non_overlap": True,
            "stream_center_counts": full_stream_counts,
            "unit_explanation_table": [
                {
                    **line,
                    "unit_kind": kind,
                    "unit_index": index,
                    "start_local_time": _iso_local(line["start_time"]),
                    "end_local_time": _iso_local(line["end_time"]),
                    "roles": full_roles.get(line["object_id"], []),
                }
                for kind, current in ordered_lines.items()
                for index, line in enumerate(current)
            ],
            "centers": full_centers,
            "connections": full_connections,
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(args.output)
    print(json.dumps({"status": "passed", "report": str(args.output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
