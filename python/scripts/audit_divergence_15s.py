"""Read-only causal audit of milestone 15S on an explicit TXT or Parquet file."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from itertools import pairwise
from pathlib import Path

import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from tvbt.chan.engine import ChanEngine, RawBar


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--bars", type=int, default=30_000)
    args = parser.parse_args()
    if args.bars < 1 or not args.source.is_file():
        parser.error("source must be a file and --bars must be positive")

    runtime = ChanEngine()
    data_revision: str | None = None
    if args.source.suffix.lower() == ".parquet":
        source = pq.ParquetFile(args.source)
        data_revision = (source.schema_arrow.metadata or {}).get(b"data_revision", b"").decode()
        count = 0
        for batch in source.iter_batches(batch_size=1024):
            for row in batch.to_pylist():
                if count >= args.bars:
                    break
                runtime.update(
                    RawBar(
                        row["bar_index"],
                        row["timestamp_utc"],
                        row["open_i64"],
                        row["high_i64"],
                        row["low_i64"],
                        row["close_i64"],
                    )
                )
                count += 1
            if count >= args.bars:
                break
    else:
        for index, raw in enumerate(args.source.read_text(encoding="gb18030").splitlines()[2:]):
            if index >= args.bars or raw.startswith("#"):
                break
            fields = [value.strip() for value in raw.split(",")]
            if len(fields) < 6:
                parser.error(f"malformed bar at source line {index + 3}")
            runtime.update(
                RawBar(
                    index,
                    1_700_000_000_000 + index * 300_000,
                    int(fields[2]),
                    int(fields[3]),
                    int(fields[4]),
                    int(fields[5]),
                )
            )

    rows = runtime.result_rows()
    centers = [value for value in rows["local_centers"] if value["unit_kind"] == "SEGMENT"]
    divergences = rows["divergences"]
    segment_positions = {line.object_id: index for index, line in enumerate(runtime._segment_lines)}

    def alternative_envelope(
        center: dict[str, object], previous: dict[str, object] | None
    ) -> tuple[int, int]:
        """Compare body ranges without a first seed shared with the prior exit."""
        seed_ids = center["seed_ids"]
        assert isinstance(seed_ids, list) and seed_ids
        start = segment_positions[str(seed_ids[0])]
        body_end = int(
            center["body_end_bar_index"]
            if center["body_end_bar_index"] is not None
            else center["observed_end_bar_index"]
        )
        stop = start
        while (
            stop < len(runtime._segment_lines)
            and runtime._segment_lines[stop].end.bar_index <= body_end
        ):
            stop += 1
        if previous is not None and previous["exit_id"] == seed_ids[0]:
            start += 1
        components = runtime._segment_lines[start:stop]
        assert components, "every center must retain at least two comparison components"
        return (
            min(int(line.range_low_i64) for line in components),
            max(int(line.range_high_i64) for line in components),
        )

    alternative_envelopes = [
        alternative_envelope(center, centers[index - 1] if index else None)
        for index, center in enumerate(centers)
    ]
    alternative_relations: list[str] = ["UNKNOWN"]
    changed_examples: list[dict[str, object]] = []
    for index in range(1, len(centers)):
        previous, current = centers[index - 1], centers[index]
        previous_dd, previous_gg = alternative_envelopes[index - 1]
        current_dd, current_gg = alternative_envelopes[index]
        relation = (
            "UP"
            if current["core_relation"] == "CORE_ABOVE" and current_dd > previous_gg
            else "DOWN"
            if current["core_relation"] == "CORE_BELOW" and current_gg < previous_dd
            else "OVERLAP"
        )
        alternative_relations.append(relation)
        if relation != current["relative_dir"] and len(changed_examples) < 10:
            changed_examples.append(
                {
                    "previous": previous["object_id"],
                    "current": current["object_id"],
                    "from": current["relative_dir"],
                    "to": relation,
                    "previous_comparison_envelope": [previous_dd, previous_gg],
                    "current_comparison_envelope": [current_dd, current_gg],
                    "shared_departure": previous["exit_id"] == current["seed_ids"][0],
                }
            )
    summary = {
        "algorithm_version": runtime.algorithm_version,
        "data_revision": data_revision,
        "bars": len(runtime.raw_bars),
        "segments": len(rows["segments"]),
        "segment_centers": len(centers),
        "relative_dir_counts": dict(Counter(value["relative_dir"] for value in centers)),
        "exclude_shared_first_seed_audit": {
            "relative_dir_counts": dict(Counter(alternative_relations)),
            "changed_examples": changed_examples,
        },
        "core_relation_counts": dict(Counter(value["core_relation"] for value in centers)),
        "core_separated_examples": [
            {
                "previous": prior["object_id"],
                "current": current["object_id"],
                "core_relation": current["core_relation"],
                "relative_dir": current["relative_dir"],
                "previous_core": [prior["zd_i64"], prior["zg_i64"]],
                "current_core": [current["zd_i64"], current["zg_i64"]],
                "previous_body": [prior["dd_i64"], prior["gg_i64"]],
                "current_body": [current["dd_i64"], current["gg_i64"]],
                "shared_departure": prior["exit_id"] in current["seed_ids"],
            }
            for prior, current in pairwise(centers)
            if current["core_relation"] in {"CORE_ABOVE", "CORE_BELOW"}
        ][:10],
        "divergence_profile_counts": dict(
            Counter(value["divergence_profile"] for value in divergences)
        ),
        "divergence_current_status_counts": dict(Counter(value["status"] for value in divergences)),
        "divergence_event_status_counts": dict(
            Counter(
                json.loads(event.payload_json).get("status")
                for event in runtime.emitter.events
                if event.object_type == "divergence" and event.operation == "upsert"
            )
        ),
        "trend_examples": [
            {
                "bar_index": value["bar_index"],
                "signal_type": value["signal_type"],
                "status": value["status"],
                "ratio": value["macd_area_ratio"],
                "a_id": value["a_object_id"],
                "b_id": value["b_object_id"],
                "A_id": value["a_center_id"],
                "B_id": value["b_center_id"],
            }
            for value in divergences
            if value["divergence_profile"] == "segment_trend_candidate"
        ][:10],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
