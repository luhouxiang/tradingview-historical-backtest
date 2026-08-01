from __future__ import annotations

import json
import sys
from pathlib import Path

import pyarrow.parquet as pq


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: validate_backtest_run.py <run-directory>")
    directory = Path(sys.argv[1]).resolve(strict=True)
    manifest = json.loads((directory / "run.json").read_text(encoding="utf-8"))
    signals = pq.read_table(directory / "trade_signals.parquet").to_pylist()
    orders = pq.read_table(directory / "orders.parquet").to_pylist()
    fills = pq.read_table(directory / "fills.parquet").to_pylist()
    signal_by_id = {value["signal_id"]: value for value in signals}
    if manifest["execution"]["fill_timing"] == "next_bar_open":
        for order in orders:
            if order["status"] == "filled":
                if order["fill_bar_index"] != order["created_at_bar_index"] + 1:
                    raise ValueError("filled order did not execute at next bar open")
            elif order["reason_code"] == "NO_NEXT_BAR" and order["fill_bar_index"] is not None:
                raise ValueError("last-bar rejection unexpectedly has a fill bar")
    if any(order["signal_id"] not in signal_by_id for order in orders):
        raise ValueError("order does not reference a strategy signal")
    if len(fills) != sum(order["status"] == "filled" for order in orders):
        raise ValueError("fill count does not match filled orders")
    print(
        json.dumps(
            {
                "trade_signal_count": len(signals),
                "order_count": len(orders),
                "fill_count": len(fills),
            },
            separators=(",", ":"),
        )
    )


if __name__ == "__main__":
    main()
