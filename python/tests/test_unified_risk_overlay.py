from __future__ import annotations

import json
import threading
from dataclasses import replace
from datetime import date
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import tvbt.backtest as backtest_module
from tvbt.backtest import run_backtest
from tvbt.risk.unified_overlay import (
    MarketObservation,
    OrderIntent,
    PortfolioSnapshot,
    RiskConfig,
    RiskContext,
    RiskState,
    definition,
    evaluate_order_intent,
    evaluate_portfolio_kill_switch,
)
from tvbt.storage.path_guard import PathGuard
from tvbt.strategy import StrategyBar, StrategyRun
from tvbt.strategy import definition as strategy_definition

REVISION = "sha256:" + "1" * 64
OTHER_REVISION = "sha256:" + "2" * 64
MARKET_REVISION = "sha256:" + "3" * 64


def parameters(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "leverage_allowed": False,
        "leverage_approval_id": "",
        "max_position_weight_ppm": 200_000,
        "max_sector_weight_ppm": 300_000,
        "max_order_loss_weight_ppm": 1_000_000,
        "stress_loss_per_contract_i64": 100,
        "max_daily_loss_ppm": 20_000,
        "max_strategy_drawdown_ppm": 150_000,
        "max_order_participation_ppm": 1_000_000,
        "max_stale_bars": 0,
        "max_data_gap_bars": 0,
        "max_open_signal_age_bars": 3,
        "event_risk_max_position_weight_ppm": 100_000,
        "kill_switch_on_data_revision": True,
    }
    values.update(overrides)
    return values


def context(
    *,
    legal: list[str] | None = None,
    handled: list[str] | None = None,
    observations: list[dict[str, object]] | None = None,
) -> RiskContext:
    return RiskContext.from_payload(
        {
            "market_state_revision": MARKET_REVISION,
            "sector_id": "metals",
            "legal_future_branches": legal or [],
            "handled_future_branches": handled or [],
            "observations": observations or [],
        },
        REVISION,
    )


def observation(**overrides: object) -> MarketObservation:
    values: dict[str, object] = {
        "effective_from_bar_index": 0,
        "available_at_bar_index": 0,
        "data_revision": REVISION,
        "trading_status": "normal",
        "stale_bars": 0,
        "data_gap_bars": 0,
        "event_risk_active": False,
    }
    values.update(overrides)
    return MarketObservation(**values)  # type: ignore[arg-type]


def snapshot(**overrides: object) -> PortfolioSnapshot:
    values: dict[str, object] = {
        "bar_index": 10,
        "equity_i64": 10_000,
        "day_start_equity_i64": 10_000,
        "peak_equity_i64": 10_000,
        "position_side": None,
        "position_quantity": 0,
    }
    values.update(overrides)
    return PortfolioSnapshot(**values)  # type: ignore[arg-type]


def intent(**overrides: object) -> OrderIntent:
    values: dict[str, object] = {
        "signal_id": "signal-1",
        "action": "open_long",
        "requested_quantity": 5,
        "signal_available_at_bar_index": 9,
        "requested_execution_bar_index": 10,
    }
    values.update(overrides)
    return OrderIntent(**values)  # type: ignore[arg-type]


def evaluate(
    *,
    config: RiskConfig | None = None,
    risk_context: RiskContext | None = None,
    state: RiskState | None = None,
    portfolio: PortfolioSnapshot | None = None,
    market: MarketObservation | None = None,
    order: OrderIntent | None = None,
    bar_index: int = 10,
    volume: int | None = 100,
):
    return evaluate_order_intent(
        config or RiskConfig.from_parameters(parameters()),
        risk_context or context(),
        state or RiskState(),
        portfolio or snapshot(),
        market or observation(),
        order or intent(),
        evaluated_at_bar_index=bar_index,
        price_i64=1_000,
        price_scale=100,
        contract_multiplier=1,
        money_scale=100,
        volume=volume,
    )


def test_definition_is_discoverable_risk_filter_with_explicit_hard_limits() -> None:
    value = definition()
    assert value["kind"] == "risk_filter"
    assert value["algorithm_id"] == "unified_risk_execution_overlay"
    assert value["algorithm_version"] == "1.0.0"
    assert value["source_hash"].startswith("sha256:")
    assert set(value["parameter_schema"]["required"]) == set(
        value["parameter_schema"]["properties"]
    )


def test_leverage_override_requires_separate_approval_and_event_cap_is_stricter() -> None:
    with pytest.raises(ValueError, match="separate approval"):
        RiskConfig.from_parameters(parameters(leverage_allowed=True))
    with pytest.raises(ValueError, match="event risk"):
        RiskConfig.from_parameters(parameters(event_risk_max_position_weight_ppm=300_000))
    approved = RiskConfig.from_parameters(
        parameters(leverage_allowed=True, leverage_approval_id="approval-42")
    )
    assert approved.leverage_allowed is True


def test_execution_never_precedes_signal_availability() -> None:
    decision = evaluate(
        order=intent(signal_available_at_bar_index=10, requested_execution_bar_index=9)
    )
    assert decision.decision_type == "blocked_decision"
    assert decision.reason_code == "LOOKAHEAD_EXECUTION"


def test_unhandled_legal_branch_blocks_participation() -> None:
    decision = evaluate(
        risk_context=context(legal=["center_continue", "S3_downtrend"], handled=["center_continue"])
    )
    assert decision.decision_type == "blocked_decision"
    assert decision.reason_code == "UNHANDLED_LEGAL_BRANCH"


def test_position_cap_reduces_but_never_increases_requested_intent() -> None:
    reduced = evaluate()
    assert reduced.decision_type == "reduced_order_intent"
    assert reduced.approved_quantity == 2
    assert reduced.approved_quantity <= intent().requested_quantity
    assert reduced.reason_code == "POSITION_WEIGHT_REDUCED"
    approved = evaluate(order=intent(requested_quantity=1))
    assert approved.decision_type == "approved_order_intent"
    assert approved.approved_quantity == 1


def test_no_leverage_and_event_risk_caps_are_hard_fixed_point_limits() -> None:
    no_leverage = RiskConfig.from_parameters(
        parameters(
            max_position_weight_ppm=5_000_000,
            max_sector_weight_ppm=5_000_000,
            event_risk_max_position_weight_ppm=5_000_000,
        )
    )
    decision = evaluate(config=no_leverage, order=intent(requested_quantity=20))
    assert decision.approved_quantity == 10
    assert decision.reason_code == "NO_LEVERAGE_REDUCED"

    event_limited = evaluate(market=observation(event_risk_active=True))
    assert event_limited.approved_quantity == 1
    assert event_limited.reason_code == "EVENT_RISK_WEIGHT_REDUCED"


def test_sector_stress_loss_and_participation_caps_reduce_independently() -> None:
    sector_limited = evaluate(
        config=RiskConfig.from_parameters(
            parameters(
                max_position_weight_ppm=5_000_000,
                max_sector_weight_ppm=100_000,
                event_risk_max_position_weight_ppm=100_000,
            )
        )
    )
    assert sector_limited.approved_quantity == 1
    assert sector_limited.reason_code == "SECTOR_WEIGHT_REDUCED"

    loss_limited = evaluate(
        config=RiskConfig.from_parameters(
            parameters(
                max_position_weight_ppm=5_000_000,
                max_sector_weight_ppm=5_000_000,
                event_risk_max_position_weight_ppm=5_000_000,
                max_order_loss_weight_ppm=20_000,
                stress_loss_per_contract_i64=100,
            )
        )
    )
    assert loss_limited.approved_quantity == 2
    assert loss_limited.reason_code == "ORDER_LOSS_REDUCED"

    participation_limited = evaluate(
        config=RiskConfig.from_parameters(
            parameters(
                max_position_weight_ppm=5_000_000,
                max_sector_weight_ppm=5_000_000,
                event_risk_max_position_weight_ppm=5_000_000,
                max_order_participation_ppm=200_000,
            )
        ),
        volume=10,
    )
    assert participation_limited.approved_quantity == 2
    assert participation_limited.reason_code == "ORDER_PARTICIPATION_REDUCED"


def test_suspension_and_zero_volume_retry_only_until_signal_age_limit() -> None:
    suspended = evaluate(market=observation(trading_status="suspended"))
    assert suspended.retryable is True
    assert suspended.scheduled_execution_bar_index == 11
    expired = evaluate(
        market=observation(trading_status="suspended"),
        order=intent(signal_available_at_bar_index=6, requested_execution_bar_index=7),
    )
    assert expired.retryable is False
    assert expired.reason_code == "OPEN_SIGNAL_AGE_LIMIT"
    no_volume = evaluate(volume=0)
    assert no_volume.reason_code == "ORDER_PARTICIPATION_BLOCKED"
    assert no_volume.retryable is True


def test_price_limits_are_direction_aware() -> None:
    limit_up_buy = evaluate(market=observation(trading_status="limit_up"))
    assert limit_up_buy.reason_code == "PRICE_LIMIT_UP_BLOCKED"
    limit_up_sell = evaluate(
        market=observation(trading_status="limit_up"),
        order=intent(action="open_short", requested_quantity=1),
    )
    assert limit_up_sell.decision_type == "approved_order_intent"

    limit_down_sell = evaluate(
        market=observation(trading_status="limit_down"),
        order=intent(action="open_short"),
    )
    assert limit_down_sell.reason_code == "PRICE_LIMIT_DOWN_BLOCKED"
    limit_down_buy = evaluate(
        market=observation(trading_status="limit_down"),
        order=intent(requested_quantity=1),
    )
    assert limit_down_buy.decision_type == "approved_order_intent"


def test_liquidity_reduction_turns_partial_close_into_reduce_action() -> None:
    decision = evaluate(
        config=RiskConfig.from_parameters(parameters(max_order_participation_ppm=200_000)),
        portfolio=snapshot(position_side="long", position_quantity=5),
        order=intent(action="close_long", requested_quantity=1),
        volume=10,
    )
    assert decision.decision_type == "reduced_order_intent"
    assert decision.approved_action == "reduce_long"
    assert decision.approved_quantity == 2
    assert decision.retryable is True
    assert decision.scheduled_execution_bar_index == 11


def test_kill_switch_unhandled_branch_and_open_signal_age_do_not_block_exit() -> None:
    state = RiskState(
        kill_switch_active=True,
        kill_switch_reason="MAX_DAILY_LOSS_BREACH",
        kill_switch_bar_index=8,
    )
    decision = evaluate(
        risk_context=context(legal=["unknown_future"], handled=[]),
        state=state,
        portfolio=snapshot(position_side="long", position_quantity=5),
        order=intent(
            action="close_long",
            requested_quantity=1,
            signal_available_at_bar_index=0,
            requested_execution_bar_index=1,
        ),
    )
    assert decision.decision_type == "approved_order_intent"
    assert decision.approved_action == "close_long"
    assert decision.approved_quantity == 5


@pytest.mark.parametrize(
    ("portfolio", "market", "expected"),
    [
        (snapshot(equity_i64=9_800), observation(), "MAX_DAILY_LOSS_BREACH"),
        (
            snapshot(equity_i64=8_500, day_start_equity_i64=8_500),
            observation(),
            "MAX_STRATEGY_DRAWDOWN_BREACH",
        ),
        (snapshot(), observation(stale_bars=1), "STALE_MARKET_DATA_LIMIT"),
        (snapshot(), observation(data_gap_bars=1), "MARKET_DATA_GAP_LIMIT"),
        (
            snapshot(),
            observation(data_revision=OTHER_REVISION),
            "DATA_REVISION_CHANGED",
        ),
    ],
)
def test_hard_breaches_activate_persistent_kill_switch(
    portfolio: PortfolioSnapshot,
    market: MarketObservation,
    expected: str,
) -> None:
    config = RiskConfig.from_parameters(parameters())
    state = RiskState()
    assert evaluate_portfolio_kill_switch(config, context(), state, portfolio, market) == expected
    assert state.kill_switch_active is True
    assert state.kill_switch_reason == expected
    assert evaluate_portfolio_kill_switch(config, context(), state, portfolio, market) is None
    blocked = evaluate(config=config, state=state, portfolio=portfolio, market=market)
    assert blocked.decision_type == "blocked_decision"


def test_future_market_observation_does_not_change_earlier_prefix() -> None:
    base = context()
    extended = context(
        observations=[
            {
                "effective_from_bar_index": 20,
                "available_at_bar_index": 20,
                "data_revision": REVISION,
                "trading_status": "limit_up",
                "stale_bars": 0,
                "data_gap_bars": 0,
                "event_risk_active": True,
            }
        ]
    )
    assert base.observation_at(10) == extended.observation_at(10)
    assert evaluate(risk_context=base) == evaluate(
        risk_context=extended, market=extended.observation_at(10)
    )
    assert (
        replace(extended.observation_at(20), effective_from_bar_index=0).trading_status
        == "limit_up"
    )


def test_formal_run_applies_overlay_persists_audit_and_publishes_chart_events(
    tmp_path: Path,
) -> None:
    guard = PathGuard(tmp_path)
    dataset_dir = tmp_path / "normalized" / "risk-sample"
    dataset_dir.mkdir(parents=True)
    closes = [100, 100, 80, 90, 70, 70, 120, 120]
    table = pa.table(
        {
            "bar_index": pa.array(range(8), type=pa.int64()),
            "trading_day": pa.array([date(2026, 8, 21)] * 8, type=pa.date32()),
            "timestamp_utc": pa.array(
                [1_700_000_000_000 + index * 300_000 for index in range(8)],
                type=pa.int64(),
            ),
            "source_hhmm": pa.array([900 + index * 5 for index in range(8)], type=pa.int32()),
            "open_i64": pa.array([100, 100, 100, 90, 90, 70, 120, 120], type=pa.int64()),
            "high_i64": pa.array([101, 101, 101, 100, 91, 71, 121, 121], type=pa.int64()),
            "low_i64": pa.array([99, 99, 79, 89, 69, 69, 119, 119], type=pa.int64()),
            "close_i64": pa.array(closes, type=pa.int64()),
            "volume": pa.array([100] * 8, type=pa.int64()),
        }
    )
    bars_path = dataset_dir / "bars.parquet"
    pq.write_table(table, bars_path)
    meta_path = dataset_dir / "meta.json"
    meta_path.write_text(
        json.dumps({"price": {"price_scale": 100, "tick_size_i64": 1}}),
        encoding="utf-8",
    )
    risk_definition = definition()
    risk_parameters = parameters(
        max_position_weight_ppm=1_000_000,
        max_sector_weight_ppm=1_000_000,
        event_risk_max_position_weight_ppm=1_000_000,
    )
    strategy = strategy_definition()
    payload = {
        "contract_version": "1.0.0",
        "request_id": "request-risk",
        "trace_id": "trace-risk",
        "job_id": "job-risk",
        "run_id": "run-risk",
        "run_signature": "sha256:" + "a" * 64,
        "dataset": {
            "dataset_id": "TEST.RISK.5m",
            "data_revision": REVISION,
            "bars_path": guard.relative(bars_path),
            "meta_path": guard.relative(meta_path),
        },
        "algorithm": {
            key: strategy[key]
            for key in ("kind", "algorithm_id", "algorithm_version", "source_hash")
        },
        "parameters": {
            "ma_period": 2,
            "touch_tolerance_ticks": 0,
            "max_retest_bars": 4,
        },
        "risk_overlay": {
            "algorithm": {
                key: risk_definition[key]
                for key in ("kind", "algorithm_id", "algorithm_version", "source_hash")
            },
            "parameters": risk_parameters,
            "context": {
                "market_state_revision": MARKET_REVISION,
                "sector_id": "metals",
                "legal_future_branches": ["retest_reclaimed", "retest_failed"],
                "handled_future_branches": ["retest_reclaimed", "retest_failed"],
                "observations": [],
            },
        },
        "range": {
            "warmup_from_bar_index": 0,
            "from_bar_index": 0,
            "to_bar_index": 7,
        },
        "execution": {
            "semantic_version": "1.0.0",
            "signal_timing": "bar_close",
            "fill_timing": "next_bar_open",
            "commission": {
                "mode": "fixed_per_contract",
                "amount_i64": 0,
                "money_scale": 100,
            },
            "slippage": {"mode": "ticks", "value": 0},
            "contract_multiplier": 1,
            "contract_multiplier_source": "instrument_config",
            "margin_ratio": 1,
            "intrabar_conflict_rule": "worst_case",
            "stress_scenario_id": "baseline",
            "cost_multiplier": 1.0,
            "additional_slippage_ticks": 0.0,
            "additional_delay_bars": 0,
            "fill_mode": "unlimited",
        },
        "capital": {
            "initial_cash_i64": 1_000_000,
            "currency": "CNY",
            "money_scale": 100,
        },
        "random_seed": 7,
        "output_path": "runs/run-risk",
    }
    result_ref = run_backtest(payload, guard, threading.Event())
    run_dir = guard.resolve(result_ref)
    decisions = pq.read_table(run_dir / "risk_decisions.parquet").to_pylist()
    assert [row["decision_type"] for row in decisions] == [
        "approved_order_intent",
        "approved_order_intent",
    ]
    assert all(row["approved_quantity"] == 1 for row in decisions)
    assert pq.read_table(run_dir / "orders.parquet").num_rows == 2
    assert pq.read_table(run_dir / "fills.parquet").num_rows == 2
    chart_events = pq.read_table(run_dir / "chart_events.parquet").to_pylist()
    assert sum(row["object_type"] == "risk_decision" for row in chart_events) == 2
    manifest = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert manifest["risk_overlay"]["algorithm"]["algorithm_id"] == "unified_risk_execution_overlay"
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["risk_approved_count"] == 2
    assert summary["risk_reduced_count"] == 0
    assert summary["risk_blocked_count"] == 0
    assert summary["risk_kill_switch_count"] == 0


def test_formal_run_executes_partial_exit_and_retries_remaining_quantity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    guard = PathGuard(tmp_path)
    dataset_dir = tmp_path / "normalized" / "risk-exit-retry"
    dataset_dir.mkdir(parents=True)
    timestamps = [1_700_100_000_000 + index * 300_000 for index in range(4)]
    pq.write_table(
        pa.table(
            {
                "bar_index": pa.array(range(4), type=pa.int64()),
                "trading_day": pa.array([date(2026, 8, 21)] * 4, type=pa.date32()),
                "timestamp_utc": pa.array(timestamps, type=pa.int64()),
                "source_hhmm": pa.array([900, 905, 910, 915], type=pa.int32()),
                "open_i64": pa.array([100] * 4, type=pa.int64()),
                "high_i64": pa.array([101] * 4, type=pa.int64()),
                "low_i64": pa.array([99] * 4, type=pa.int64()),
                "close_i64": pa.array([100] * 4, type=pa.int64()),
                "volume": pa.array([10, 10, 2, 3], type=pa.int64()),
            }
        ),
        dataset_dir / "bars.parquet",
    )
    (dataset_dir / "meta.json").write_text(
        json.dumps({"price": {"price_scale": 100, "tick_size_i64": 1}}),
        encoding="utf-8",
    )
    strategy_run = StrategyRun(
        bars=[StrategyBar(index, timestamps[index], 100, 101, 99, 100) for index in range(4)],
        indicator_values=[],
        strategy_states=[],
        stage_signals=[],
        trade_signals=[
            {
                "signal_id": "SIG-OPEN-5",
                "parent_stage_signal_id": None,
                "known_at_bar_index": 0,
                "side": "long",
                "action": "open_long",
                "quantity": 5,
                "price_i64": 100,
                "reason_code": "TEST_OPEN",
                "object_revision": 1,
            },
            {
                "signal_id": "SIG-CLOSE-ALL",
                "parent_stage_signal_id": None,
                "known_at_bar_index": 1,
                "side": "short",
                "action": "close_long",
                "quantity": 1,
                "price_i64": 100,
                "reason_code": "TEST_CLOSE",
                "object_revision": 1,
            },
        ],
        chart_events=[],
        events=[],
    )
    monkeypatch.setattr(backtest_module, "run_strategy", lambda *_args, **_kwargs: strategy_run)
    risk_definition = definition()
    strategy = strategy_definition()
    payload = {
        "contract_version": "1.0.0",
        "request_id": "request-risk-exit",
        "trace_id": "trace-risk-exit",
        "job_id": "job-risk-exit",
        "run_id": "run-risk-exit",
        "run_signature": "sha256:" + "b" * 64,
        "dataset": {
            "dataset_id": "TEST.RISK.EXIT.5m",
            "data_revision": REVISION,
            "bars_path": guard.relative(dataset_dir / "bars.parquet"),
            "meta_path": guard.relative(dataset_dir / "meta.json"),
        },
        "algorithm": {
            key: strategy[key]
            for key in ("kind", "algorithm_id", "algorithm_version", "source_hash")
        },
        "parameters": {
            "ma_period": 2,
            "touch_tolerance_ticks": 0,
            "max_retest_bars": 4,
        },
        "risk_overlay": {
            "algorithm": {
                key: risk_definition[key]
                for key in ("kind", "algorithm_id", "algorithm_version", "source_hash")
            },
            "parameters": parameters(),
            "context": {
                "market_state_revision": MARKET_REVISION,
                "sector_id": "metals",
                "legal_future_branches": [],
                "handled_future_branches": [],
                "observations": [],
            },
        },
        "range": {"warmup_from_bar_index": 0, "from_bar_index": 0, "to_bar_index": 3},
        "execution": {
            "semantic_version": "1.0.0",
            "signal_timing": "bar_close",
            "fill_timing": "next_bar_open",
            "commission": {"mode": "fixed_per_contract", "amount_i64": 0, "money_scale": 100},
            "slippage": {"mode": "ticks", "value": 0},
            "contract_multiplier": 1,
            "contract_multiplier_source": "instrument_config",
            "margin_ratio": 1,
            "intrabar_conflict_rule": "worst_case",
            "stress_scenario_id": "baseline",
            "cost_multiplier": 1.0,
            "additional_slippage_ticks": 0.0,
            "additional_delay_bars": 0,
            "fill_mode": "unlimited",
        },
        "capital": {"initial_cash_i64": 1_000_000, "currency": "CNY", "money_scale": 100},
        "random_seed": 7,
        "output_path": "runs/run-risk-exit",
    }
    run_dir = guard.resolve(run_backtest(payload, guard, threading.Event()))
    orders = pq.read_table(run_dir / "orders.parquet").to_pylist()
    assert [(row["action"], row["quantity"]) for row in orders] == [
        ("open_long", 5),
        ("reduce_long", 2),
        ("close_long", 3),
    ]
    assert len({row["order_id"] for row in orders}) == 3
    fills = pq.read_table(run_dir / "fills.parquet").to_pylist()
    assert len({row["fill_id"] for row in fills}) == 3
    positions = pq.read_table(run_dir / "positions.parquet").to_pylist()
    assert positions[-1]["side"] == "flat"
    decisions = pq.read_table(run_dir / "risk_decisions.parquet").to_pylist()
    assert [row["decision_type"] for row in decisions] == [
        "approved_order_intent",
        "reduced_order_intent",
        "approved_order_intent",
    ]
