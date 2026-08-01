from __future__ import annotations

import json
import sys
from pathlib import Path

import pyarrow.parquet as pq

PYTHON_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PYTHON_ROOT / "src"))

from tvbt.chan.checkpoint import load_checkpoint  # noqa: E402


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: validate_chan_cache.py <cache-directory>")
    directory = Path(sys.argv[1]).resolve(strict=True)
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    events = pq.read_table(directory / "events.parquet").to_pylist()
    if [event["event_seq"] for event in events] != list(range(1, len(events) + 1)):
        raise ValueError("event_seq is not contiguous from one")
    previous_known = -1
    revisions: dict[tuple[str, str], int] = {}
    for event in events:
        known = event["known_at_bar_index"]
        if known < previous_known:
            raise ValueError("known_at_bar_index moved backwards")
        previous_known = known
        key = (event["object_type"], event["object_id"])
        if event["object_revision"] != revisions.get(key, 0) + 1:
            raise ValueError(f"object revision is not contiguous for {key}")
        revisions[key] = event["object_revision"]
        json.loads(event["payload_json"])
    checkpoints = sorted((directory / "checkpoints").glob("*.bin"), key=lambda item: int(item.stem))
    for checkpoint in checkpoints:
        bar_index, _ = load_checkpoint(
            checkpoint.read_bytes(), manifest["algorithm"]["algorithm_version"]
        )
        if bar_index != int(checkpoint.stem):
            raise ValueError("checkpoint filename does not match payload bar_index")
    result = {"event_count": len(events), "checkpoint_count": len(checkpoints)}
    print(json.dumps(result, separators=(",", ":")))


if __name__ == "__main__":
    main()
