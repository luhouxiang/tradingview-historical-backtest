from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from tvbt.backtest import _attribute_trades
from tvbt.comparison import _tier_results, run_comparison
from tvbt.storage.path_guard import PathGuard
from tvbt.strategy import _run_strategy_chan, definitions


def test_algorithm_catalog_marks_only_formal_108_strategies_eligible() -> None:
    values = definitions()
    eligible = [value for value in values if value.get("comparison_eligible")]
    assert len(eligible) == 13
    assert all(value["research_role"] == "formal_strategy" for value in eligible)
    assert all(value["strategy_family"] for value in eligible)
    assert not next(value for value in values if value["algorithm_id"] == "ma20_retest_short")[
        "comparison_eligible"
    ]
    assert not any(
        value.get("comparison_eligible")
        for value in values
        if value["algorithm_id"].startswith("aux_")
    )


def test_shared_chan_runtime_is_computed_once(monkeypatch: Any, tmp_path: Path) -> None:
    calls = 0

    def fake_run_chan(*args: Any, **kwargs: Any) -> tuple[Any, Any, Any]:
        nonlocal calls
        calls += 1
        return SimpleNamespace(emitter=SimpleNamespace(events=[])), [], {}

    monkeypatch.setattr("tvbt.strategy.run_chan", fake_run_chan)
    payload: dict[str, Any] = {"_shared_chan_cache": {}}
    chan_payload = {
        "dataset": {"dataset_id": "TEST.5m", "data_revision": "sha256:" + "1" * 64},
        "algorithm": {},
        "parameters": {"checkpoint_interval": 1024},
    }
    guard = PathGuard(tmp_path)
    first = _run_strategy_chan(payload, chan_payload, guard, threading.Event(), last_bar_index=10)
    chan_payload["parameters"]["checkpoint_interval"] = 2048
    second = _run_strategy_chan(payload, chan_payload, guard, threading.Event(), last_bar_index=10)
    assert first is second
    assert calls == 1


def test_all_formal_strategies_finish_one_batch_with_one_chan_calculation(
    monkeypatch: Any, tmp_path: Path
) -> None:
    guard = PathGuard(tmp_path)
    dataset = tmp_path / "normalized" / "TEST.RESEARCH.5m" / "revision"
    dataset.mkdir(parents=True)
    closes = [
        100 + ((index % 20) - 10) * (1 if (index // 20) % 2 == 0 else -1) for index in range(120)
    ]
    pq.write_table(
        pa.table(
            {
                "bar_index": pa.array(range(120), type=pa.int64()),
                "timestamp_utc": pa.array(
                    (index * 300_000 for index in range(120)), type=pa.int64()
                ),
                "trading_day": pa.array(["2026-01-05"] * 120, type=pa.string()),
                "open_i64": pa.array(closes, type=pa.int64()),
                "high_i64": pa.array((value + 2 for value in closes), type=pa.int64()),
                "low_i64": pa.array((value - 2 for value in closes), type=pa.int64()),
                "close_i64": pa.array(closes, type=pa.int64()),
                "volume": pa.array((100 for _ in closes), type=pa.int64()),
            }
        ),
        dataset / "bars.parquet",
    )
    (dataset / "meta.json").write_text(
        '{"price":{"price_scale":1,"tick_size_i64":1}}', encoding="utf-8"
    )
    formal = [value for value in definitions() if value.get("comparison_eligible")]
    source_run_chan = __import__("tvbt.strategy", fromlist=["run_chan"]).run_chan
    chan_calls = 0

    def counted_run_chan(*args: Any, **kwargs: Any) -> tuple[Any, Any, Any]:
        nonlocal chan_calls
        chan_calls += 1
        return source_run_chan(*args, **kwargs)

    monkeypatch.setattr("tvbt.strategy.run_chan", counted_run_chan)
    strategies = []
    for index, definition in enumerate(formal):
        parameters = {
            name: rule["default"]
            for name, rule in definition["parameter_schema"]["properties"].items()
            if "default" in rule
        }
        strategies.append(
            {
                "strategy": {
                    key: definition[key]
                    for key in ("kind", "algorithm_id", "algorithm_version", "source_hash")
                },
                "parameters": parameters,
                "name": definition["name"],
                "strategy_family": definition["strategy_family"],
                "run_id": f"run-formal-{index}",
                "run_signature": "sha256:" + f"{index + 1:064x}",
            }
        )
    result_ref = run_comparison(
        {
            "contract_version": "1.0.0",
            "request_id": "request-all-formal",
            "trace_id": "trace-all-formal",
            "comparison_id": "comparison-all-formal",
            "dataset": {
                "dataset_id": "TEST.RESEARCH.5m",
                "data_revision": "sha256:" + "1" * 64,
                "bars_path": "normalized/TEST.RESEARCH.5m/revision/bars.parquet",
                "meta_path": "normalized/TEST.RESEARCH.5m/revision/meta.json",
            },
            "strategies": strategies,
            "range": {"warmup_from_bar_index": 0, "from_bar_index": 0, "to_bar_index": 119},
            "execution": {
                "signal_timing": "bar_close",
                "fill_timing": "next_bar_open",
                "commission": {"mode": "fixed_per_contract", "amount_i64": 0, "money_scale": 100},
                "slippage": {"mode": "ticks", "value": 1},
                "contract_multiplier": 1,
                "margin_ratio": 0.1,
                "intrabar_conflict_rule": "worst_case",
            },
            "capital": {"initial_cash_i64": 100_000_000, "currency": "CNY", "money_scale": 100},
            "random_seed": 20260822,
            "minimum_trade_count": 20,
            "output_path": "comparisons/comparison-all-formal",
        },
        guard,
        threading.Event(),
    )
    results = json.loads((guard.resolve(result_ref) / "results.json").read_text(encoding="utf-8"))
    assert len(results) == 13
    assert all(value["status"] == "completed" for value in results)
    assert chan_calls == 1


def test_comparison_commits_successes_and_isolates_strategy_failure(
    monkeypatch: Any, tmp_path: Path
) -> None:
    guard = PathGuard(tmp_path)
    shared_cache_ids: list[int] = []

    def fake_backtest(
        child: dict[str, Any], guard_value: PathGuard, cancelled: threading.Event
    ) -> str:
        shared_cache_ids.append(id(child["_shared_chan_cache"]))
        if child["algorithm"]["algorithm_id"] == "bad":
            raise ValueError("expected strategy failure")
        ref = str(child["output_path"])
        directory = guard_value.resolve(ref)
        directory.mkdir(parents=True)
        (directory / "summary.json").write_text(
            json.dumps(
                {
                    "total_return": 0.1,
                    "annualized_return": None,
                    "max_drawdown": 0.02,
                    "sharpe": 1.2,
                    "trade_count": 4,
                    "win_rate": 0.5,
                    "average_win_i64": 10,
                    "average_loss_i64": -5,
                    "profit_loss_ratio": 2,
                    "profit_factor": 2,
                    "expectancy_i64": 2.5,
                    "total_commission_i64": 0,
                    "total_slippage_i64": 0,
                    "risk_approved_count": 0,
                    "risk_reduced_count": 0,
                    "risk_blocked_count": 0,
                    "risk_kill_switch_count": 0,
                }
            ),
            encoding="utf-8",
        )
        (directory / "_SUCCESS").write_bytes(b"")
        return ref

    monkeypatch.setattr("tvbt.comparison.run_backtest", fake_backtest)
    revision = "sha256:" + "1" * 64
    signature = "sha256:" + "2" * 64
    payload = {
        "contract_version": "1.0.0",
        "request_id": "request-1",
        "trace_id": "trace-1",
        "comparison_id": "comparison-1",
        "dataset": {
            "dataset_id": "TEST.5m",
            "data_revision": revision,
            "bars_path": "normalized/TEST/bars.parquet",
            "meta_path": "normalized/TEST/meta.json",
        },
        "strategies": [
            {
                "strategy": {"algorithm_id": "good"},
                "parameters": {},
                "name": "Good",
                "strategy_family": "test",
                "run_id": "run-good",
                "run_signature": signature,
            },
            {
                "strategy": {"algorithm_id": "bad"},
                "parameters": {},
                "name": "Bad",
                "strategy_family": "test",
                "run_id": "run-bad",
                "run_signature": signature,
            },
        ],
        "range": {"warmup_from_bar_index": 0, "from_bar_index": 0, "to_bar_index": 10},
        "execution": {},
        "capital": {},
        "random_seed": 7,
        "minimum_trade_count": 20,
        "output_path": "comparisons/comparison-1",
    }
    progress: list[tuple[float, dict[str, Any]]] = []
    result_ref = run_comparison(
        payload,
        guard,
        threading.Event(),
        lambda value, detail: progress.append((value, detail)),
    )
    assert result_ref == "comparisons/comparison-1"
    output = guard.resolve(result_ref)
    assert (output / "_SUCCESS").is_file()
    results = json.loads((output / "results.json").read_text(encoding="utf-8"))
    assert [value["status"] for value in results] == ["completed", "failed"]
    assert results[0]["summary"]["run_id"] == "run-good"
    manifest = json.loads((output / "comparison.json").read_text(encoding="utf-8"))
    assert manifest["completed_count"] == 1
    assert manifest["failed_count"] == 1
    assert len(set(shared_cache_ids)) == 1
    assert progress[-1][1]["current_algorithm_id"] is None


def test_result_tiers_and_pareto_are_deterministic() -> None:
    def item(name: str, returned: float, drawdown: float, trades: int) -> dict[str, Any]:
        return {
            "algorithm_id": name,
            "status": "completed",
            "summary": {
                "total_return": returned,
                "max_drawdown": drawdown,
                "trade_count": trades,
            },
        }

    results = [
        item("none", 0, 0, 0),
        item("loss", -0.1, 0.2, 30),
        item("small", 0.2, 0.1, 2),
        item("dominated", 0.1, 0.2, 30),
        item("frontier", 0.2, 0.1, 30),
        item("same", 0.2, 0.1, 30),
    ]
    _tier_results(results, 20)
    assert [value["tier"] for value in results[:4]] == [
        "no_trades",
        "loss_making",
        "profitable_low_sample",
        "profitable_candidate",
    ]
    assert results[3]["pareto"] is False
    assert results[4]["tier"] == results[5]["tier"] == "pareto_candidate"


def test_trade_attribution_is_prefix_invariant() -> None:
    trade = {
        "entry_signal_id": "sig-B3",
        "entry_signal_known_at_bar_index": 5,
        "entry_price_i64": 110,
    }
    signals = [
        {
            "signal_id": "sig-B3",
            "known_at_bar_index": 5,
            "reason_code": "STANDARD_B3",
            "action": "open_long",
        }
    ]
    prefix = [
        {
            "known_at_bar_index": 4,
            "object_type": "level_center",
            "object_id": "center-1",
            "object_revision": 1,
            "payload_json": json.dumps({"zd_i64": 90, "zg_i64": 100, "phase": "migrating_up"}),
        }
    ]
    first = dict(trade)
    _attribute_trades([first], signals, prefix)
    second = dict(trade)
    _attribute_trades(
        [second],
        signals,
        [
            *prefix,
            {
                "known_at_bar_index": 8,
                "object_type": "level_center",
                "object_id": "center-1",
                "object_revision": 2,
                "payload_json": json.dumps({"zd_i64": 105, "zg_i64": 120}),
            },
        ],
    )
    assert first == second
    assert first["trigger_category"] == "B3"
    assert first["price_vs_center"] == "above"
