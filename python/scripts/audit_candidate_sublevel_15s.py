"""Audit possible BI-level evidence inside saved segment-trend candidates.

This is a read-only research aid. It does not classify a standard divergence or
equate a BI-level pattern with the original text's sublevel movement.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cache", type=Path)
    args = parser.parse_args()
    cache = args.cache.resolve(strict=True)
    if not (cache / "_SUCCESS").is_file():
        raise ValueError("cache is incomplete")
    divergences = pq.read_table(cache / "divergences.parquet").to_pylist()
    centers = {
        row["object_id"]: row for row in pq.read_table(cache / "local_centers.parquet").to_pylist()
    }
    segments = {
        row["object_id"]: row for row in pq.read_table(cache / "segments.parquet").to_pylist()
    }
    bis = pq.read_table(cache / "bi.parquet").to_pylist()
    report: list[dict[str, Any]] = []
    for candidate in divergences:
        if (
            candidate["divergence_profile"] != "segment_trend_candidate"
            or candidate["status"] != "candidate"
        ):
            continue
        center = centers[candidate["b_center_id"]]
        current = segments[candidate["comparison_current_object_id"]]
        lower = int(current["start_bar_index"])
        upper = int(current["end_bar_index"])
        known_at = int(candidate["known_at_bar_index"])
        internal = sorted(
            (
                bi
                for bi in bis
                if bi["status"] == "confirmed"
                and lower <= bi["start_bar_index"]
                and bi["end_bar_index"] <= upper
                and bi["known_at_bar_index"] <= known_at
            ),
            key=lambda bi: (bi["start_bar_index"], bi["end_bar_index"]),
        )
        internal_centers = [
            value["object_id"]
            for value in centers.values()
            if value["unit_kind"] == "BI"
            and value["status"] == "CLOSED"
            and value["body_start_bar_index"] is not None
            and value["body_end_bar_index"] is not None
            and lower <= value["body_start_bar_index"]
            and value["body_end_bar_index"] <= upper
            and value["known_at_bar_index"] <= known_at
        ]
        upward = candidate["relative_dir"] == "UP"
        boundary = int(center["zg_i64"] if upward else center["zd_i64"])
        departure: dict[str, Any] | None = None
        retest: dict[str, Any] | None = None
        for index, bi in enumerate(internal[:-1]):
            if bi["direction"] != ("up" if upward else "down"):
                continue
            breaks = bi["range_high_i64"] > boundary if upward else bi["range_low_i64"] < boundary
            if not breaks:
                continue
            departure = bi
            follower = internal[index + 1]
            if follower["start_bar_index"] == bi["end_bar_index"] and follower["direction"] == (
                "down" if upward else "up"
            ):
                holds = (
                    follower["range_low_i64"] >= boundary
                    if upward
                    else follower["range_high_i64"] <= boundary
                )
                if holds:
                    retest = follower
            break
        report.append(
            {
                "divergence_id": candidate["object_id"],
                "direction": candidate["relative_dir"],
                "c_bar_range": [lower, upper],
                "b_core_boundary_i64": boundary,
                "confirmed_internal_bi_count": len(internal),
                "internal_bi_center_ids": internal_centers,
                "first_boundary_break_bi_id": None if departure is None else departure["object_id"],
                "first_retest_holding_boundary_bi_id": None
                if retest is None
                else retest["object_id"],
                "research_only": True,
            }
        )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
