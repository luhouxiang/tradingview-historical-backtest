from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pyarrow.parquet as pq


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: validate_replay_cache.py <cache-directory>")
    directory = Path(sys.argv[1]).resolve(strict=True)
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    events_path = directory / "events.parquet"
    digest = hashlib.sha256(events_path.read_bytes()).hexdigest()
    if manifest["events_sha256"] != "sha256:" + digest:
        raise ValueError("events Parquet checksum mismatch")
    events = pq.read_table(events_path).to_pylist()
    if manifest["event_count"] != len(events):
        raise ValueError("event count does not match manifest")
    if [event["event_seq"] for event in events] != list(range(1, len(events) + 1)):
        raise ValueError("event_seq is not contiguous")
    if any(
        events[index]["known_at_bar_index"] > events[index + 1]["known_at_bar_index"]
        for index in range(len(events) - 1)
    ):
        raise ValueError("known_at_bar_index moved backwards")
    if events and events[-1]["known_at_bar_index"] > manifest["range"]["to_bar_index"]:
        raise ValueError("replay cache contains future events")
    print(json.dumps({"event_count": len(events)}, separators=(",", ":")))


if __name__ == "__main__":
    main()
