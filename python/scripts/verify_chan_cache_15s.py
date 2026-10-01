"""Run and verify a full-history 15S Chan cache for an explicit normalized revision.

The cache key matches Go's Calculation CacheKey JSON payload. Existing complete
caches are verified, never overwritten; incomplete paths fail closed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import threading
from collections import Counter
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from tvbt import ENGINE_VERSION
from tvbt.calculation import calculate
from tvbt.chan.algorithm import definition
from tvbt.storage.path_guard import PathGuard


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _go_cache_key(data_revision: str, algorithm: dict[str, str], parameters: dict[str, Any]) -> str:
    payload = {
        "data_revision": data_revision,
        "algorithm_kind": algorithm["kind"],
        "algorithm_id": algorithm["algorithm_id"],
        "algorithm_version": algorithm["algorithm_version"],
        "source_hash": algorithm["source_hash"],
        "parameters": parameters,
        "calculation_mode": "causal_events",
        "engine_version": ENGINE_VERSION,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--revision-dir", required=True)
    args = parser.parse_args()

    root = args.data_root.resolve(strict=True)
    guard = PathGuard(root)
    dataset_prefix = Path("normalized") / args.dataset_id / args.revision_dir
    meta_ref = (dataset_prefix / "meta.json").as_posix()
    bars_ref = (dataset_prefix / "bars.parquet").as_posix()
    meta = json.loads(guard.resolve(meta_ref).read_text(encoding="utf-8"))
    if meta["dataset_id"] != args.dataset_id:
        raise ValueError("dataset ID differs from normalized metadata")
    data_revision = str(meta["data_revision"])
    if not data_revision.startswith("sha256:") or not data_revision[7:].startswith(
        args.revision_dir
    ):
        raise ValueError("revision directory differs from normalized metadata")
    if not guard.resolve(bars_ref).is_file():
        raise FileNotFoundError(bars_ref)

    published = definition()
    algorithm = {
        key: str(published[key])
        for key in ("kind", "algorithm_id", "algorithm_version", "source_hash")
    }
    parameters = {
        name: rule["default"] for name, rule in published["parameter_schema"]["properties"].items()
    }
    cache_key = _go_cache_key(data_revision, algorithm, parameters)
    output_ref = "cache/chan/" + cache_key[7:]
    output = guard.resolve(output_ref)
    if output.exists() and not (output / "_SUCCESS").is_file():
        raise ValueError("incomplete cache exists; refusing to overwrite it")
    payload = {
        "request_id": "verify-15s",
        "trace_id": "verify-15s",
        "job_id": "verify-15s",
        "cache_key": cache_key,
        "dataset": {
            "dataset_id": args.dataset_id,
            "data_revision": data_revision,
            "bars_path": bars_ref,
            "meta_path": meta_ref,
        },
        "algorithm": algorithm,
        "parameters": parameters,
        "calculation_mode": "causal_events",
        "output_path": output_ref,
    }
    created = not output.exists()
    if created:
        returned = calculate(payload, guard, threading.Event())
        if returned != output_ref:
            raise AssertionError("calculation returned a different cache path")

    if not (output / "_SUCCESS").is_file():
        raise AssertionError("cache has no _SUCCESS marker")
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    if (
        manifest["cache_key"] != cache_key
        or manifest["data_revision"] != data_revision
        or manifest["algorithm"] != algorithm
        or manifest["parameters"] != parameters
        or manifest["calculation_mode"] != "causal_events"
        or manifest["coverage"]["bar_count"] != meta["coverage"]["bar_count"]
    ):
        raise AssertionError("cache manifest identity or full-history coverage differs")
    for name, entry in manifest["files"].items():
        path = output / entry["path"]
        if not path.is_file() or _sha256(path) != entry["sha256"]:
            raise AssertionError(f"cache file checksum mismatch: {name}")
        if pq.ParquetFile(path).metadata.num_rows != entry["row_count"]:
            raise AssertionError(f"cache row count mismatch: {name}")

    divergences = pq.read_table(
        output / "divergences.parquet",
        columns=[
            "divergence_profile",
            "object_id",
            "status",
            "signal_type",
            "relative_dir",
            "a_object_id",
            "b_object_id",
            "comparison_current_object_id",
            "a_center_id",
            "b_center_id",
            "macd_area_reference",
            "macd_area_current",
            "strength_profile",
            "strength_relation",
            "strength_trigger",
            "price_displacement_reference_i64",
            "price_displacement_current_i64",
            "observed_intervals_reference",
            "observed_intervals_current",
            "baseline_span_reference_i64",
            "baseline_span_current_i64",
            "baseline_span_below_80pct",
            "new_extreme_satisfied",
            "reference_center_ordinal",
            "bar_index",
            "known_at_bar_index",
            "c_contains_type3",
            "c_meets_sublevel",
            "c_sublevel_profile",
            "c_sublevel_center_ids",
            "c_type3_departure_id",
            "c_type3_retest_id",
            "c_proof_known_at_bar_index",
        ],
    ).to_pylist()
    segments = {
        row["object_id"]: row
        for row in pq.read_table(
            output / "segments.parquet",
            columns=["object_id", "start_bar_index", "end_bar_index", "direction"],
        ).to_pylist()
    }
    centers = {
        row["object_id"]: row
        for row in pq.read_table(
            output / "local_centers.parquet",
            columns=["object_id", "relative_dir", "unit_kind"],
        ).to_pylist()
    }
    for signal in divergences:
        if signal["known_at_bar_index"] < signal["bar_index"]:
            raise AssertionError("divergence is known before its theoretical bar")
        if signal["status"] != "invalidated":
            if signal["strength_profile"] != "price_displacement_speed_v1":
                raise AssertionError("active divergence is not backed by price-time strength")
            if any(
                signal[name] is None or signal[name] <= 0
                for name in (
                    "price_displacement_reference_i64",
                    "price_displacement_current_i64",
                    "observed_intervals_reference",
                    "observed_intervals_current",
                )
            ):
                raise AssertionError("active divergence lacks positive price-time measurements")
            if signal["strength_trigger"] == "price_time_joint_weakening":
                if signal["strength_relation"] != "weaker":
                    raise AssertionError("price-time trigger contradicts strength relation")
            elif signal["strength_trigger"] == "baseline_span_below_80pct":
                reference_span = signal["baseline_span_reference_i64"]
                current_span = signal["baseline_span_current_i64"]
                if (
                    reference_span is None
                    or current_span is None
                    or reference_span <= 0
                    or current_span <= 0
                    or 5 * current_span >= 4 * reference_span
                    or signal["baseline_span_below_80pct"] is not True
                ):
                    raise AssertionError("80% trigger lacks strict comparable price spans")
            else:
                raise AssertionError("active divergence has no valid price-time trigger")
        if signal["divergence_profile"] == "segment_trend_candidate" and (
            signal["c_contains_type3"] is True and signal["c_meets_sublevel"] is True
        ):
            raise AssertionError("fully proved trend was left as a candidate")
        if signal["divergence_profile"] == "segment_trend_candidate":
            expected_direction = {
                "top_divergence": "UP",
                "bottom_divergence": "DOWN",
            }.get(signal["signal_type"])
            if expected_direction is None or signal["relative_dir"] != expected_direction:
                raise AssertionError("trend candidate contradicts its center migration direction")
            if signal["status"] != "invalidated":
                a = segments[signal["a_object_id"]]
                b = segments[signal["b_object_id"]]
                c = segments[signal["comparison_current_object_id"]]
                direction = "up" if expected_direction == "UP" else "down"
                if not (
                    a["direction"] == b["direction"] == c["direction"] == direction
                    and a["start_bar_index"] < b["start_bar_index"] < c["start_bar_index"]
                    and centers[signal["a_center_id"]]["unit_kind"] == "SEGMENT"
                    and centers[signal["b_center_id"]]["unit_kind"] == "SEGMENT"
                    and centers[signal["b_center_id"]]["relative_dir"] == expected_direction
                    and signal["new_extreme_satisfied"] is True
                ):
                    raise AssertionError("trend candidate lacks its a/A/b/B/c structural evidence")
        if (
            signal["divergence_profile"] == "external_range"
            and signal["status"] == "confirmed"
            and signal["reference_center_ordinal"] != 1
        ):
            raise AssertionError("multi-center range candidate was upgraded without a boundary")
        if signal["divergence_profile"] == "standard_trend" and (
            signal["c_contains_type3"] is not True
            or signal["c_meets_sublevel"] is not True
            or signal["c_sublevel_profile"] != "bi_two_confirmed_centers_type3_v1"
            or len(signal["c_sublevel_center_ids"] or []) < 2
            or signal["c_type3_departure_id"] is None
            or signal["c_type3_retest_id"] is None
            or signal["c_proof_known_at_bar_index"] is None
            or signal["c_proof_known_at_bar_index"] > signal["known_at_bar_index"]
        ):
            raise AssertionError("standard trend divergence lacks internal structural proof")

    print(
        json.dumps(
            {
                "created": created,
                "cache_key": cache_key,
                "data_revision": data_revision,
                "algorithm": algorithm,
                "coverage": manifest["coverage"],
                "counts": manifest["counts"],
                "divergence_profiles": dict(
                    Counter(signal["divergence_profile"] for signal in divergences)
                ),
                "strength_triggers": dict(
                    Counter(
                        signal["strength_trigger"]
                        for signal in divergences
                        if signal["status"] != "invalidated"
                    )
                ),
                "macd_not_weaker_but_price_time_triggered": sum(
                    signal["status"] != "invalidated"
                    and signal["macd_area_reference"] is not None
                    and signal["macd_area_current"] is not None
                    and signal["macd_area_current"] >= signal["macd_area_reference"]
                    for signal in divergences
                ),
                "strict_80pct_examples": [
                    {
                        "object_id": signal["object_id"],
                        "bar_index": signal["bar_index"],
                        "status": signal["status"],
                        "profile": signal["divergence_profile"],
                        "reference_span_i64": signal["baseline_span_reference_i64"],
                        "current_span_i64": signal["baseline_span_current_i64"],
                        "strength_relation": signal["strength_relation"],
                        "a_start": segments[signal["a_object_id"]]["start_bar_index"],
                        "c_end": segments[signal["comparison_current_object_id"]]["end_bar_index"],
                    }
                    for signal in divergences
                    if signal["status"] != "invalidated"
                    and signal["strength_trigger"] == "baseline_span_below_80pct"
                ][:5],
                "trend_candidate_examples": [
                    {
                        "object_id": signal["object_id"],
                        "bar_index": signal["bar_index"],
                        "c_contains_type3": signal["c_contains_type3"],
                        "c_meets_sublevel": signal["c_meets_sublevel"],
                        "a_start": segments[signal["a_object_id"]]["start_bar_index"],
                        "c_end": segments[signal["comparison_current_object_id"]]["end_bar_index"],
                    }
                    for signal in divergences
                    if signal["status"] == "candidate"
                    and signal["divergence_profile"] == "segment_trend_candidate"
                ][:5],
                "status": "verified",
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
