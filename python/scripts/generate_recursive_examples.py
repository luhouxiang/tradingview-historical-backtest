"""Regenerate the declared synthetic fixed-decomposition contract fixtures."""
# ruff: noqa: E402 -- this standalone developer tool bootstraps the declared source roots.

from __future__ import annotations

import json
import sys
from pathlib import Path

python_root = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(python_root / "src"), str(python_root / "tests")]

from test_recursive_association import PRICES_TREND, standard_prices
from test_recursive_decomposition import PRICES, calculate, lines_for

from tvbt.chan.engine import ChanEngine, _signal_payload
from tvbt.chan.recursive_signals import recursive_signals


def main() -> None:
    directory = python_root.parent / "contracts" / "examples"
    runtime, snapshot = calculate(PRICES)
    divergences, points = recursive_signals(
        runtime, lines_for(PRICES), snapshot, 211, {i: i for i in range(212)}
    )
    response_path = directory / "chan-recursive-calculation-results.json"
    response = json.loads(response_path.read_text(encoding="utf-8"))
    response["algorithm"]["algorithm_version"] = ChanEngine.algorithm_version
    objects = response["objects"]
    for key, values in snapshot.items():
        objects["recursive_" + key] = values
    for key, values in (("divergences", divergences), ("trade_points", points)):
        objects[key] = [
            {
                **_signal_payload(signal),
                "object_id": object_id,
                "object_revision": 1,
                "known_at_bar_index": signal.known_at_bar_index,
            }
            for object_id, signal in values
        ]
    response["coverage"]["returned_count"] = sum(len(value) for value in objects.values())
    for path, value in (
        (directory / "recursive-structure.json", snapshot),
        (response_path, response),
    ):
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _, association = calculate(PRICES_TREND)
    (directory / "recursive-association-structure.json").write_text(
        json.dumps(association, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    prices = standard_prices()
    runtime, standard = calculate(prices)
    cursor = (len(prices) - 1) * 10 + 1
    divergences, points = recursive_signals(
        runtime, lines_for(prices), standard, cursor, {i: i for i in range(cursor + 1)}
    )
    for name, values in standard.items():
        response["objects"]["recursive_" + name] = values
    for name, values in (("divergences", divergences), ("trade_points", points)):
        response["objects"][name] = [
            {
                **_signal_payload(signal),
                "object_id": object_id,
                "object_revision": 1,
                "known_at_bar_index": signal.known_at_bar_index,
            }
            for object_id, signal in values
        ]
    response["coverage"]["last_bar_index"] = cursor
    response["coverage"]["returned_count"] = sum(
        len(value) for value in response["objects"].values()
    )
    (directory / "recursive-standard-calculation-results.json").write_text(
        json.dumps(response, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
