from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, cast

import pyarrow.parquet as pq

from tvbt.auxiliary.boll_bardo import (
    BollBardoConfig,
    classify_boll_bardo,
    compute_boll_series,
    derive_bardo_contexts,
)
from tvbt.auxiliary.boll_bardo import definition as boll_bardo_definition
from tvbt.auxiliary.daily_30m import (
    Daily30mBar,
    Daily30mConfig,
    classify_daily_30m_sessions,
)
from tvbt.auxiliary.daily_30m import definition as daily_30m_definition
from tvbt.auxiliary.ma_kiss import (
    MaKissConfig,
    classify_ma_kisses,
    compute_ma_kiss_series,
)
from tvbt.auxiliary.ma_kiss import definition as auxiliary_ma_kiss_definition
from tvbt.auxiliary.ma_sector_rotation import (
    DivergenceUpdate,
    EventOperation,
    MaSectorRotationConfig,
    RankingBar,
    RankingContext,
    RankingInstrument,
    classify_ma_sector_rotation,
)
from tvbt.auxiliary.ma_sector_rotation import definition as ma_sector_rotation_definition
from tvbt.auxiliary.macd_zero_axis import (
    MacdZeroAxisConfig,
    classify_macd_directional_regimes,
    classify_macd_zero_axis,
    compute_macd_zero_axis_series,
)
from tvbt.auxiliary.macd_zero_axis import definition as macd_zero_axis_definition
from tvbt.auxiliary.price_gap import classify_price_gaps
from tvbt.auxiliary.price_gap import definition as price_gap_definition
from tvbt.auxiliary.single_instrument_ma import (
    SingleMaConfig,
    classify_single_instrument_ma,
    compute_ma_ladder,
)
from tvbt.auxiliary.single_instrument_ma import definition as single_instrument_ma_definition
from tvbt.chan.algorithm import definition as chan_definition
from tvbt.chan.algorithm import run_chan
from tvbt.risk import unified_risk_overlay_definition
from tvbt.storage.path_guard import PathGuard


def _run_strategy_chan(
    payload: dict[str, Any],
    chan_payload: dict[str, Any],
    guard: PathGuard,
    cancelled: threading.Event,
    *,
    last_bar_index: int | None,
) -> tuple[Any, Any, Any]:
    """Reuse one completed causal Chan runtime inside a comparison job.

    The cache is an in-process implementation detail inserted by the comparison
    runner. It never crosses JSON or enters a formal run manifest.
    """
    cache = payload.get("_shared_chan_cache")
    dataset = chan_payload["dataset"]
    key = (
        str(dataset["dataset_id"]),
        str(dataset["data_revision"]),
        last_bar_index,
    )
    if isinstance(cache, dict) and key in cache:
        return cast(tuple[Any, Any, Any], cache[key])
    result = run_chan(
        chan_payload,
        guard,
        cancelled,
        last_bar_index=last_bar_index,
        write_checkpoints=False,
    )
    if isinstance(cache, dict):
        cache[key] = result
    return result


def _source_hash() -> str:
    digest = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    return "sha256:" + digest


def definition() -> dict[str, Any]:
    return {
        "kind": "strategy",
        "algorithm_id": "ma20_retest_short",
        "algorithm_version": "1.0.0",
        "source_hash": _source_hash(),
        "name": "MA20 Retest Failure Short",
        "input_schema": "bars.v1",
        "parameter_schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "ma_period": {"type": "integer", "minimum": 2, "maximum": 500, "default": 20},
                "touch_tolerance_ticks": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 100,
                    "default": 1,
                },
                "max_retest_bars": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 500,
                    "default": 20,
                },
            },
            "required": ["ma_period", "touch_tolerance_ticks", "max_retest_bars"],
        },
        "outputs": [
            {
                "name": kind,
                "display_name": label,
                "pane": "main",
                "series_type": "semantic_objects",
                "object_type": kind,
            }
            for kind, label in (
                ("strategy_state", "策略状态"),
                ("stage_signal", "阶段信号"),
                ("trade_signal", "交易信号"),
                ("chart_event", "图表事件"),
            )
        ],
        "warmup": {"kind": "formula", "expression": "ma_period"},
        "causal": True,
    }


def fixed_level_centre_definition() -> dict[str, Any]:
    digest = hashlib.sha256()
    digest.update(Path(__file__).read_bytes())
    digest.update(chan_definition()["source_hash"].encode())
    return {
        "kind": "strategy",
        "algorithm_id": "fixed_level_centre_decision_tree",
        "algorithm_version": "1.2.0",
        "source_hash": "sha256:" + digest.hexdigest(),
        "name": "固定级别中枢决策树",
        "input_schema": "bars.v1",
        "parameter_schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "checkpoint_interval": {
                    "type": "integer",
                    "minimum": 64,
                    "maximum": 100_000,
                    "default": 1024,
                },
                "allow_long": {"type": "boolean", "default": True},
                "allow_short": {"type": "boolean", "default": True},
            },
            "required": ["checkpoint_interval", "allow_long", "allow_short"],
        },
        "outputs": [
            {
                "name": kind,
                "display_name": label,
                "pane": "main",
                "series_type": "semantic_objects",
                "object_type": kind,
            }
            for kind, label in (
                ("strategy_state", "中枢决策状态"),
                ("stage_signal", "状态转换"),
                ("trade_signal", "交易信号"),
                ("chart_event", "策略图表事件"),
            )
        ],
        "warmup": {"kind": "formula", "expression": "full-history causal Chan state"},
        "causal": True,
    }


def _chan_strategy_definition(
    algorithm_id: str,
    name: str,
    output_prefix: str,
    *,
    algorithm_version: str = "1.0.0",
    dependency_source_hashes: tuple[str, ...] = (),
) -> dict[str, Any]:
    digest = hashlib.sha256()
    digest.update(Path(__file__).read_bytes())
    digest.update(chan_definition()["source_hash"].encode())
    digest.update(algorithm_id.encode())
    for source_hash in dependency_source_hashes:
        digest.update(source_hash.encode())
    return {
        "kind": "strategy",
        "algorithm_id": algorithm_id,
        "algorithm_version": algorithm_version,
        "source_hash": "sha256:" + digest.hexdigest(),
        "name": name,
        "input_schema": "bars.v1",
        "parameter_schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "checkpoint_interval": {
                    "type": "integer",
                    "minimum": 64,
                    "maximum": 100_000,
                    "default": 1024,
                }
            },
            "required": ["checkpoint_interval"],
        },
        "outputs": [
            {
                "name": kind,
                "display_name": label,
                "pane": "main",
                "series_type": "semantic_objects",
                "object_type": kind,
            }
            for kind, label in (
                ("strategy_state", f"{output_prefix}状态"),
                ("stage_signal", f"{output_prefix}阶段"),
                ("trade_signal", f"{output_prefix}交易信号"),
                ("chart_event", f"{output_prefix}图表事件"),
            )
        ],
        "warmup": {"kind": "formula", "expression": "full-history causal Chan state"},
        "causal": True,
    }


def downtrend_reversal_definition() -> dict[str, Any]:
    return _chan_strategy_definition(
        "downtrend_reversal_only",
        "下跌趋势一买反转",
        "一买反转",
        algorithm_version="1.1.0",
    )


def trend_divergence_reversal_definition() -> dict[str, Any]:
    return _chan_strategy_definition(
        "trend_divergence_reversal",
        "趋势背驰双向反转",
        "趋势背驰反转",
        algorithm_version="1.1.0",
    )


def consolidation_reversion_definition() -> dict[str, Any]:
    return _chan_strategy_definition(
        "consolidation_divergence_centre_reversion",
        "盘整背驰中枢回归",
        "中枢回归",
        algorithm_version="1.3.0",
    )


def third_point_migration_definition() -> dict[str, Any]:
    return _chan_strategy_definition(
        "third_buy_centre_migration_hold",
        "三买三卖中枢迁移持有",
        "中枢迁移持有",
        algorithm_version="1.2.0",
    )


def first_centre_rotation_definition() -> dict[str, Any]:
    return _chan_strategy_definition(
        "first_centre_B3_rotation",
        "首中枢三买三卖轮动",
        "首中枢轮动",
        algorithm_version="1.3.0",
    )


def _macd_third_point_definition(
    algorithm_id: str,
    name: str,
    output_prefix: str,
) -> dict[str, Any]:
    result = _chan_strategy_definition(
        algorithm_id,
        name,
        output_prefix,
        algorithm_version="1.0.0",
        dependency_source_hashes=(macd_zero_axis_definition()["source_hash"],),
    )
    macd_properties = macd_zero_axis_definition()["parameter_schema"]["properties"]
    result["parameter_schema"] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "checkpoint_interval": {
                "type": "integer",
                "minimum": 64,
                "maximum": 100_000,
                "default": 1024,
            },
            **macd_properties,
        },
        "required": ["checkpoint_interval", *macd_properties.keys()],
    }
    result["warmup"] = {
        "kind": "formula",
        "expression": "full-history causal Chan state; MACD slow_period + signal_period - 2",
    }
    return result


def third_point_migration_macd_definition() -> dict[str, Any]:
    return _macd_third_point_definition(
        "third_point_migration_macd_regime",
        "三买三卖中枢迁移·MACD方向确认",
        "中枢迁移·MACD",
    )


def first_centre_b3_macd_definition() -> dict[str, Any]:
    return _macd_third_point_definition(
        "first_centre_B3_macd_regime",
        "首中枢三买三卖轮动·MACD方向确认",
        "首中枢轮动·MACD",
    )


def second_buy_only_definition() -> dict[str, Any]:
    result = _chan_strategy_definition(
        "second_buy_only",
        "只做第二类买点",
        "二买",
        algorithm_version="1.1.0",
    )
    result["parameter_schema"] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "checkpoint_interval": {
                "type": "integer",
                "minimum": 64,
                "maximum": 100_000,
                "default": 1024,
            },
            "allow_strongest": {"type": "boolean", "default": True},
            "allow_normal": {"type": "boolean", "default": True},
            "allow_weakest": {"type": "boolean", "default": True},
            "strongest_quantity": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "default": 2,
            },
            "normal_quantity": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "default": 2,
            },
            "weakest_quantity": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "default": 1,
            },
        },
        "required": [
            "checkpoint_interval",
            "allow_strongest",
            "allow_normal",
            "allow_weakest",
            "strongest_quantity",
            "normal_quantity",
            "weakest_quantity",
        ],
    }
    return result


def third_buy_only_definition() -> dict[str, Any]:
    result = _chan_strategy_definition(
        "third_buy_only",
        "只做第三类买点",
        "三买",
        algorithm_version="1.1.0",
    )
    result["parameter_schema"] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "checkpoint_interval": {
                "type": "integer",
                "minimum": 64,
                "maximum": 100_000,
                "default": 1024,
            },
            "allow_late_center": {"type": "boolean", "default": True},
            "first_center_quantity": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "default": 2,
            },
            "late_center_quantity": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "default": 1,
            },
            "minimum_entry_volume": {
                "type": "integer",
                "minimum": 0,
                "maximum": 9_223_372_036_854_775_807,
                "default": 0,
            },
        },
        "required": [
            "checkpoint_interval",
            "allow_late_center",
            "first_center_quantity",
            "late_center_quantity",
            "minimum_entry_volume",
        ],
    }
    return result


def centre_oscillation_spread_definition() -> dict[str, Any]:
    result = _chan_strategy_definition(
        "centre_oscillation_spread",
        "中枢震荡差价",
        "中枢震荡",
    )
    result["algorithm_version"] = "1.1.0"
    quantity = {"type": "integer", "minimum": 1, "maximum": 100}
    non_negative_i64 = {
        "type": "integer",
        "minimum": 0,
        "maximum": 9_223_372_036_854_775_807,
    }
    result["parameter_schema"] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "checkpoint_interval": {
                "type": "integer",
                "minimum": 64,
                "maximum": 100_000,
                "default": 1024,
            },
            "allow_long": {"type": "boolean", "default": True},
            "allow_short": {"type": "boolean", "default": True},
            "strong_quantity": {**quantity, "default": 2},
            "neutral_quantity": {**quantity, "default": 1},
            "weak_quantity": {**quantity, "default": 1},
            "estimated_round_trip_cost_i64": {**non_negative_i64, "default": 0},
            "minimum_net_range_i64": {**non_negative_i64, "default": 1},
            "fast_execution_available": {"type": "boolean", "default": False},
            "max_entries_per_center": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "default": 4,
            },
        },
        "required": [
            "checkpoint_interval",
            "allow_long",
            "allow_short",
            "strong_quantity",
            "neutral_quantity",
            "weak_quantity",
            "estimated_round_trip_cost_i64",
            "minimum_net_range_i64",
            "fast_execution_available",
            "max_entries_per_center",
        ],
    }
    return result


def same_level_decomposition_program_definition() -> dict[str, Any]:
    result = _chan_strategy_definition(
        "same_level_decomposition_program",
        "同级别分解机械程序",
        "同级分解",
        algorithm_version="1.1.0",
    )
    result["parameter_schema"] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "checkpoint_interval": {
                "type": "integer",
                "minimum": 64,
                "maximum": 100_000,
                "default": 1024,
            },
            "odd_direction_is_down": {"type": "boolean", "default": True},
            "allow_long": {"type": "boolean", "default": True},
            "allow_short": {"type": "boolean", "default": True},
            "operation_quantity": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "default": 1,
            },
        },
        "required": [
            "checkpoint_interval",
            "odd_direction_is_down",
            "allow_long",
            "allow_short",
            "operation_quantity",
        ],
    }
    return result


def three_level_complete_classification_definition() -> dict[str, Any]:
    result = _chan_strategy_definition(
        "three_level_complete_classification",
        "三层级完全分类",
        "三层分类",
    )
    result["parameter_schema"] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "checkpoint_interval": {
                "type": "integer",
                "minimum": 64,
                "maximum": 100_000,
                "default": 1024,
            },
            "level_graph_profile_id": {
                "type": "integer",
                "minimum": 1,
                "maximum": 1,
                "default": 1,
            },
            "allow_long": {"type": "boolean", "default": True},
            "allow_short": {"type": "boolean", "default": True},
            "operation_quantity": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "default": 1,
            },
            "can_handle_mid_third_point": {"type": "boolean", "default": True},
            "can_handle_mid_center_continue": {"type": "boolean", "default": True},
            "can_handle_high_change_candidate": {"type": "boolean", "default": True},
        },
        "required": [
            "checkpoint_interval",
            "level_graph_profile_id",
            "allow_long",
            "allow_short",
            "operation_quantity",
            "can_handle_mid_third_point",
            "can_handle_mid_center_continue",
            "can_handle_high_change_candidate",
        ],
    }
    return result


def target_level_rebound_segmented_operation_definition() -> dict[str, Any]:
    result = _chan_strategy_definition(
        "target_level_rebound_segmented_operation",
        "目标级别反弹/回调分段操作",
        "反弹回调分段",
    )
    non_negative_i64 = {
        "type": "integer",
        "minimum": 0,
        "maximum": 9_223_372_036_854_775_807,
    }
    result["parameter_schema"] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "checkpoint_interval": {
                "type": "integer",
                "minimum": 64,
                "maximum": 100_000,
                "default": 1024,
            },
            "level_graph_profile_id": {
                "type": "integer",
                "minimum": 1,
                "maximum": 1,
                "default": 1,
            },
            "allow_long": {"type": "boolean", "default": True},
            "allow_short": {"type": "boolean", "default": True},
            "operation_quantity": {
                "type": "integer",
                "minimum": 2,
                "maximum": 100,
                "default": 2,
            },
            "partial_take_profit_quantity": {
                "type": "integer",
                "minimum": 1,
                "maximum": 99,
                "default": 1,
            },
            "estimated_round_trip_cost_i64": {**non_negative_i64, "default": 0},
            "minimum_net_segment_i64": {**non_negative_i64, "default": 1},
            "execution_available": {"type": "boolean", "default": True},
        },
        "required": [
            "checkpoint_interval",
            "level_graph_profile_id",
            "allow_long",
            "allow_short",
            "operation_quantity",
            "partial_take_profit_quantity",
            "estimated_round_trip_cost_i64",
            "minimum_net_segment_i64",
            "execution_available",
        ],
    }
    return result


def bottom_top_construction_definition() -> dict[str, Any]:
    result = _chan_strategy_definition(
        "bottom_top_construction",
        "底部/顶部构造状态机",
        "底顶构造",
    )
    result["parameter_schema"] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "checkpoint_interval": {
                "type": "integer",
                "minimum": 64,
                "maximum": 100_000,
                "default": 1024,
            },
            "level_graph_profile_id": {
                "type": "integer",
                "minimum": 1,
                "maximum": 1,
                "default": 1,
            },
            "allow_long": {"type": "boolean", "default": True},
            "allow_short": {"type": "boolean", "default": True},
            "operation_quantity": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "default": 1,
            },
            "execution_available": {"type": "boolean", "default": True},
            "coarse_effective_hold_bars": {
                "type": "integer",
                "minimum": 1,
                "maximum": 20,
                "default": 1,
            },
        },
        "required": [
            "checkpoint_interval",
            "level_graph_profile_id",
            "allow_long",
            "allow_short",
            "operation_quantity",
            "execution_available",
            "coarse_effective_hold_bars",
        ],
    }
    return result


def definitions() -> list[dict[str, Any]]:
    values = [
        definition(),
        fixed_level_centre_definition(),
        downtrend_reversal_definition(),
        trend_divergence_reversal_definition(),
        consolidation_reversion_definition(),
        third_point_migration_definition(),
        first_centre_rotation_definition(),
        third_point_migration_macd_definition(),
        first_centre_b3_macd_definition(),
        second_buy_only_definition(),
        third_buy_only_definition(),
        centre_oscillation_spread_definition(),
        same_level_decomposition_program_definition(),
        three_level_complete_classification_definition(),
        target_level_rebound_segmented_operation_definition(),
        bottom_top_construction_definition(),
        auxiliary_ma_kiss_definition(),
        macd_zero_axis_definition(),
        boll_bardo_definition(),
        daily_30m_definition(),
        ma_sector_rotation_definition(),
        price_gap_definition(),
        single_instrument_ma_definition(),
        unified_risk_overlay_definition(),
    ]
    formal: dict[str, tuple[str, list[str]]] = {
        "fixed_level_centre_decision_tree": ("centre_decision", ["ALG-STR-001"]),
        "downtrend_reversal_only": ("trend_reversal", ["ALG-SIG-001"]),
        "trend_divergence_reversal": ("trend_reversal", ["ALG-SIG-001"]),
        "consolidation_divergence_centre_reversion": ("centre_reversion", ["ALG-SIG-002"]),
        "third_buy_centre_migration_hold": ("third_point", ["ALG-STR-003"]),
        "first_centre_B3_rotation": ("third_point", ["ALG-STR-003"]),
        "third_point_migration_macd_regime": (
            "third_point_macd_composite",
            ["ALG-STR-003", "ALG-AUX-002"],
        ),
        "first_centre_B3_macd_regime": (
            "third_point_macd_composite",
            ["ALG-STR-003", "ALG-AUX-002"],
        ),
        "second_buy_only": ("second_point", ["ALG-STR-002"]),
        "third_buy_only": ("third_point", ["ALG-STR-003"]),
        "centre_oscillation_spread": ("centre_oscillation", ["ALG-STR-004"]),
        "same_level_decomposition_program": ("same_level_decomposition", ["ALG-STR-005"]),
        "three_level_complete_classification": ("structure_classification", ["ALG-STR-007"]),
        "target_level_rebound_segmented_operation": ("segmented_operation", ["ALG-STR-008"]),
        "bottom_top_construction": ("bottom_top_construction", ["ALG-STR-009"]),
    }
    for value in values:
        algorithm_id = str(value["algorithm_id"])
        if algorithm_id in formal:
            family, catalog_ids = formal[algorithm_id]
            value.update(
                comparison_eligible=True,
                research_role="formal_strategy",
                strategy_family=family,
                catalog_algorithm_ids=catalog_ids,
            )
        elif value.get("kind") == "risk_filter":
            value.update(
                comparison_eligible=False,
                research_role="risk_filter",
                strategy_family="risk",
                catalog_algorithm_ids=["ALG-RISK-001"],
            )
        elif algorithm_id.startswith("aux_"):
            value.update(
                comparison_eligible=False,
                research_role="auxiliary_non_trading",
                strategy_family="auxiliary",
                catalog_algorithm_ids=[],
            )
        else:
            value.update(
                comparison_eligible=False,
                research_role="example_strategy",
                strategy_family="example",
                catalog_algorithm_ids=[],
            )
    return values


@dataclass(frozen=True)
class StrategyBar:
    bar_index: int
    timestamp_utc: int
    open_i64: int
    high_i64: int
    low_i64: int
    close_i64: int


@dataclass
class StrategyState:
    name: str = "waiting_break"
    stage_signal_id: str | None = None
    stage_started: int | None = None
    previous_close: int | None = None
    previous_ma: float | None = None


@dataclass
class Transition:
    state: StrategyState
    state_changes: list[dict[str, Any]] = field(default_factory=list)
    stage_signals: list[dict[str, Any]] = field(default_factory=list)
    trade_signals: list[dict[str, Any]] = field(default_factory=list)
    chart_events: list[dict[str, Any]] = field(default_factory=list)


class Strategy(Protocol):
    def metadata(self) -> dict[str, Any]: ...
    def initialize(self) -> StrategyState: ...
    def on_bar(
        self, state: StrategyState, bar: StrategyBar, snapshot: dict[str, float]
    ) -> Transition: ...
    def finalize(self, state: StrategyState) -> dict[str, Any]: ...


class MA20RetestShort:
    def __init__(self, parameters: dict[str, Any], tick_size_i64: int) -> None:
        self.period = int(parameters["ma_period"])
        self.tolerance = int(parameters["touch_tolerance_ticks"]) * max(1, tick_size_i64)
        self.max_retest = int(parameters["max_retest_bars"])

    def metadata(self) -> dict[str, Any]:
        return definition()

    def initialize(self) -> StrategyState:
        return StrategyState()

    def on_bar(
        self, state: StrategyState, bar: StrategyBar, snapshot: dict[str, float]
    ) -> Transition:
        ma = snapshot["ma"]
        previous_name = state.name
        reason: str | None = None
        stage_signals: list[dict[str, Any]] = []
        trade_signals: list[dict[str, Any]] = []
        if (
            state.name == "waiting_break"
            and state.previous_close is not None
            and state.previous_ma is not None
            and state.previous_close >= state.previous_ma
            and bar.close_i64 < ma
        ):
            state.name = "waiting_retest"
            state.stage_started = bar.bar_index
            state.stage_signal_id = f"STAGE-{bar.bar_index}-BREAK-DOWN"
            reason = "CLOSE_BROKE_BELOW_MA"
            stage_signals.append(
                {
                    "stage_signal_id": state.stage_signal_id,
                    "known_at_bar_index": bar.bar_index,
                    "stage": "break_down",
                    "status": "opened",
                    "price_i64": bar.close_i64,
                    "reason_code": reason,
                }
            )
        elif state.name in {"waiting_retest", "waiting_retest_failure"}:
            age = bar.bar_index - int(state.stage_started or bar.bar_index)
            if age > self.max_retest:
                state.name = "waiting_break"
                reason = "RETEST_TIMEOUT"
                stage_signals.append(self._close_stage(state, bar, "expired", reason))
            elif state.name == "waiting_retest" and bar.high_i64 >= ma - self.tolerance:
                state.name = "waiting_retest_failure"
                reason = "PRICE_TOUCHED_MA"
                stage_signals.append(
                    {
                        "stage_signal_id": state.stage_signal_id,
                        "known_at_bar_index": bar.bar_index,
                        "stage": "retest",
                        "status": "updated",
                        "price_i64": bar.high_i64,
                        "reason_code": reason,
                    }
                )
            elif state.name == "waiting_retest_failure" and bar.close_i64 > ma:
                state.name = "waiting_break"
                reason = "RETEST_RECLAIMED_MA"
                stage_signals.append(self._close_stage(state, bar, "invalidated", reason))
            elif (
                state.name == "waiting_retest_failure"
                and bar.close_i64 < bar.open_i64
                and bar.close_i64 < ma
            ):
                state.name = "short_open"
                reason = "RETEST_FAILED_BREAK_OPEN"
                signal_id = f"SIG-{bar.bar_index}-SHORT-OPEN"
                trade_signals.append(
                    {
                        "signal_id": signal_id,
                        "parent_stage_signal_id": state.stage_signal_id,
                        "known_at_bar_index": bar.bar_index,
                        "side": "short",
                        "action": "open_short",
                        "price_i64": bar.close_i64,
                        "reason_code": reason,
                    }
                )
                stage_signals.append(self._close_stage(state, bar, "completed", reason))
        elif state.name == "short_open" and bar.close_i64 > ma:
            state.name = "waiting_break"
            reason = "CLOSE_RECOVERED_ABOVE_MA"
            trade_signals.append(
                {
                    "signal_id": f"SIG-{bar.bar_index}-SHORT-CLOSE",
                    "parent_stage_signal_id": None,
                    "known_at_bar_index": bar.bar_index,
                    "side": "long",
                    "action": "close_short",
                    "price_i64": bar.close_i64,
                    "reason_code": reason,
                }
            )
        changes: list[dict[str, Any]] = []
        if state.name != previous_name:
            changes.append(
                {
                    "known_at_bar_index": bar.bar_index,
                    "state_from": previous_name,
                    "state_to": state.name,
                    "reason_code": reason,
                }
            )
        state.previous_close = bar.close_i64
        state.previous_ma = ma
        chart_events = [
            {
                "event_id": signal["signal_id"],
                "known_at_bar_index": bar.bar_index,
                "bar_index": bar.bar_index,
                "timestamp_utc": bar.timestamp_utc,
                "price_i64": signal["price_i64"],
                "event_type": signal["action"],
                "reason_code": signal["reason_code"],
            }
            for signal in trade_signals
        ]
        return Transition(state, changes, stage_signals, trade_signals, chart_events)

    @staticmethod
    def _close_stage(
        state: StrategyState, bar: StrategyBar, status: str, reason: str
    ) -> dict[str, Any]:
        return {
            "stage_signal_id": state.stage_signal_id,
            "known_at_bar_index": bar.bar_index,
            "stage": "retest",
            "status": status,
            "price_i64": bar.close_i64,
            "reason_code": reason,
        }

    def finalize(self, state: StrategyState) -> dict[str, Any]:
        return {"final_state": state.name}


@dataclass
class StrategyRun:
    bars: list[StrategyBar]
    indicator_values: list[dict[str, Any]]
    strategy_states: list[dict[str, Any]]
    stage_signals: list[dict[str, Any]]
    trade_signals: list[dict[str, Any]]
    chart_events: list[dict[str, Any]]
    events: list[dict[str, Any]]


def run_strategy(
    payload: dict[str, Any],
    guard: PathGuard,
    cancelled: threading.Event,
    *,
    last_bar_index: int | None = None,
) -> StrategyRun:
    dataset = payload.get("dataset")
    algorithm = payload.get("algorithm")
    parameters = payload.get("parameters")
    if (
        not isinstance(dataset, dict)
        or not isinstance(algorithm, dict)
        or not isinstance(parameters, dict)
    ):
        raise ValueError("dataset, algorithm and parameters are required")
    expected = next(
        (
            value
            for value in definitions()
            if value["algorithm_id"] == algorithm.get("algorithm_id")
        ),
        None,
    )
    if expected is None:
        raise ValueError("strategy algorithm_id is not supported")
    for key in ("kind", "algorithm_id", "algorithm_version", "source_hash"):
        if algorithm.get(key) != expected[key]:
            raise ValueError(f"strategy {key} does not match engine definition")
    if algorithm["algorithm_id"] == "fixed_level_centre_decision_tree":
        return _run_fixed_level_centre(payload, guard, cancelled, last_bar_index=last_bar_index)
    if algorithm["algorithm_id"] in {
        "downtrend_reversal_only",
        "trend_divergence_reversal",
    }:
        return _run_trend_reversal(
            payload,
            guard,
            cancelled,
            last_bar_index=last_bar_index,
            long_only=algorithm["algorithm_id"] == "downtrend_reversal_only",
        )
    if algorithm["algorithm_id"] == "consolidation_divergence_centre_reversion":
        return _run_consolidation_reversion(
            payload, guard, cancelled, last_bar_index=last_bar_index
        )
    if algorithm["algorithm_id"] == "third_buy_centre_migration_hold":
        return _run_third_point_migration_hold(
            payload, guard, cancelled, last_bar_index=last_bar_index
        )
    if algorithm["algorithm_id"] == "first_centre_B3_rotation":
        return _run_third_point_migration_hold(
            payload,
            guard,
            cancelled,
            last_bar_index=last_bar_index,
            first_only=True,
        )
    if algorithm["algorithm_id"] == "third_point_migration_macd_regime":
        return _run_third_point_migration_hold(
            payload,
            guard,
            cancelled,
            last_bar_index=last_bar_index,
            macd_gate=True,
        )
    if algorithm["algorithm_id"] == "first_centre_B3_macd_regime":
        return _run_third_point_migration_hold(
            payload,
            guard,
            cancelled,
            last_bar_index=last_bar_index,
            first_only=True,
            macd_gate=True,
        )
    if algorithm["algorithm_id"] == "second_buy_only":
        return _run_second_buy_only(payload, guard, cancelled, last_bar_index=last_bar_index)
    if algorithm["algorithm_id"] == "third_buy_only":
        return _run_third_buy_only(payload, guard, cancelled, last_bar_index=last_bar_index)
    if algorithm["algorithm_id"] == "centre_oscillation_spread":
        return _run_centre_oscillation_spread(
            payload, guard, cancelled, last_bar_index=last_bar_index
        )
    if algorithm["algorithm_id"] == "same_level_decomposition_program":
        return _run_same_level_decomposition_program(
            payload, guard, cancelled, last_bar_index=last_bar_index
        )
    if algorithm["algorithm_id"] == "three_level_complete_classification":
        return _run_three_level_complete_classification(
            payload, guard, cancelled, last_bar_index=last_bar_index
        )
    if algorithm["algorithm_id"] == "target_level_rebound_segmented_operation":
        return _run_target_level_rebound_segmented_operation(
            payload, guard, cancelled, last_bar_index=last_bar_index
        )
    if algorithm["algorithm_id"] == "bottom_top_construction":
        return _run_bottom_top_construction(
            payload, guard, cancelled, last_bar_index=last_bar_index
        )
    if algorithm["algorithm_id"] == "aux_ma_kiss_legacy":
        return _run_auxiliary_ma_kiss(payload, guard, cancelled, last_bar_index=last_bar_index)
    if algorithm["algorithm_id"] == "aux_macd_zero_axis_defense":
        return _run_auxiliary_macd_zero_axis(
            payload, guard, cancelled, last_bar_index=last_bar_index
        )
    if algorithm["algorithm_id"] == "aux_boll_bardo_warning":
        return _run_auxiliary_boll_bardo(payload, guard, cancelled, last_bar_index=last_bar_index)
    if algorithm["algorithm_id"] == "aux_daily_30m_classification":
        return _run_auxiliary_daily_30m(payload, guard, cancelled, last_bar_index=last_bar_index)
    if algorithm["algorithm_id"] == "aux_ma_sector_rotation":
        return _run_auxiliary_ma_sector_rotation(
            payload, guard, cancelled, last_bar_index=last_bar_index
        )
    if algorithm["algorithm_id"] == "aux_price_gap_lifecycle":
        return _run_auxiliary_price_gap(payload, guard, cancelled, last_bar_index=last_bar_index)
    if algorithm["algorithm_id"] == "aux_single_instrument_ma_observation":
        return _run_auxiliary_single_instrument_ma(
            payload, guard, cancelled, last_bar_index=last_bar_index
        )
    meta = json.loads(guard.resolve(str(dataset["meta_path"])).read_text(encoding="utf-8"))
    tick_size = int(meta["price"].get("tick_size_i64") or 1)
    table = pq.read_table(
        guard.resolve(str(dataset["bars_path"])),
        columns=["bar_index", "timestamp_utc", "open_i64", "high_i64", "low_i64", "close_i64"],
    ).to_pydict()
    strategy: Strategy = MA20RetestShort(parameters, tick_size)
    state = strategy.initialize()
    period = int(parameters["ma_period"])
    closes: list[int] = []
    bars: list[StrategyBar] = []
    indicator_values: list[dict[str, Any]] = []
    states: list[dict[str, Any]] = []
    stages: list[dict[str, Any]] = []
    signals: list[dict[str, Any]] = []
    chart_events: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    revisions: dict[tuple[str, str], int] = {}
    for position, value in enumerate(table["bar_index"]):
        bar_index = int(value)
        if last_bar_index is not None and bar_index > last_bar_index:
            break
        if position % 256 == 0 and cancelled.is_set():
            raise InterruptedError("strategy execution cancelled")
        bar = StrategyBar(
            bar_index,
            int(table["timestamp_utc"][position]),
            int(table["open_i64"][position]),
            int(table["high_i64"][position]),
            int(table["low_i64"][position]),
            int(table["close_i64"][position]),
        )
        bars.append(bar)
        closes.append(bar.close_i64)
        ma = sum(closes[-period:]) / period if len(closes) >= period else None
        indicator_values.append({"bar_index": bar_index, "ma": ma})
        if ma is None:
            state.previous_close = bar.close_i64
            continue
        transition = strategy.on_bar(state, bar, {"ma": ma})
        for object_type, id_field, rows in (
            ("strategy_state", "", transition.state_changes),
            ("stage_signal", "stage_signal_id", transition.stage_signals),
            ("trade_signal", "signal_id", transition.trade_signals),
            ("chart_event", "event_id", transition.chart_events),
        ):
            for row in rows:
                rows_target = {
                    "strategy_state": states,
                    "stage_signal": stages,
                    "trade_signal": signals,
                    "chart_event": chart_events,
                }[object_type]
                object_id = str(row.get(id_field) or f"{object_type}-{bar_index}")
                revision_key = (object_type, object_id)
                revision = revisions.get(revision_key, 0) + 1
                revisions[revision_key] = revision
                rows_target.append({**row, "object_revision": revision})
                payload_value = {**row, "object_id": object_id, "object_revision": revision}
                events.append(
                    {
                        "event_seq": len(events) + 1,
                        "known_at_bar_index": bar_index,
                        "object_type": object_type,
                        "object_id": object_id,
                        "operation": "upsert",
                        "object_revision": revision,
                        "payload_json": json.dumps(payload_value, separators=(",", ":")),
                    }
                )
    strategy.finalize(state)
    return StrategyRun(bars, indicator_values, states, stages, signals, chart_events, events)


def _run_fixed_level_centre(
    payload: dict[str, Any],
    guard: PathGuard,
    cancelled: threading.Event,
    *,
    last_bar_index: int | None,
) -> StrategyRun:
    dataset = payload["dataset"]
    parameters = payload["parameters"]
    chan = chan_definition()
    chan_payload = {
        "dataset": dataset,
        "algorithm": {
            key: chan[key] for key in ("kind", "algorithm_id", "algorithm_version", "source_hash")
        },
        "parameters": {"checkpoint_interval": int(parameters["checkpoint_interval"])},
    }
    runtime, _, _ = _run_strategy_chan(
        payload, chan_payload, guard, cancelled, last_bar_index=last_bar_index
    )
    table = pq.read_table(
        guard.resolve(str(dataset["bars_path"])),
        columns=["bar_index", "timestamp_utc", "open_i64", "high_i64", "low_i64", "close_i64"],
    ).to_pydict()
    bars: list[StrategyBar] = []
    for row_position, value in enumerate(table["bar_index"]):
        bar_index = int(value)
        if last_bar_index is not None and bar_index > last_bar_index:
            break
        bars.append(
            StrategyBar(
                bar_index,
                int(table["timestamp_utc"][row_position]),
                int(table["open_i64"][row_position]),
                int(table["high_i64"][row_position]),
                int(table["low_i64"][row_position]),
                int(table["close_i64"][row_position]),
            )
        )
    events_by_bar: dict[int, list[Any]] = {}
    for event in runtime.emitter.events:
        events_by_bar.setdefault(event.known_at_bar_index, []).append(event)

    centers: dict[str, dict[str, Any]] = {}
    third_points: dict[str, dict[str, Any]] = {}
    current_state = "waiting_for_centre"
    position_side = "flat"
    states: list[dict[str, Any]] = []
    stages: list[dict[str, Any]] = []
    signals: list[dict[str, Any]] = []
    chart_events: list[dict[str, Any]] = []
    causal_events: list[dict[str, Any]] = []
    revisions: dict[tuple[str, str], int] = {}

    def publish(object_type: str, object_id: str, row: dict[str, Any], bar_index: int) -> None:
        revision_key = (object_type, object_id)
        revision = revisions.get(revision_key, 0) + 1
        revisions[revision_key] = revision
        target = {
            "strategy_state": states,
            "stage_signal": stages,
            "trade_signal": signals,
            "chart_event": chart_events,
        }[object_type]
        target.append({**row, "object_revision": revision})
        event_payload = {**row, "object_id": object_id, "object_revision": revision}
        causal_events.append(
            {
                "event_seq": len(causal_events) + 1,
                "known_at_bar_index": bar_index,
                "object_type": object_type,
                "object_id": object_id,
                "operation": "upsert",
                "object_revision": revision,
                "payload_json": json.dumps(
                    event_payload, ensure_ascii=False, separators=(",", ":")
                ),
            }
        )

    def trade(bar: StrategyBar, action: str, reason: str, center_id: str) -> None:
        nonlocal position_side
        signal_id = f"CHAN-FIXED-{bar.bar_index}-{action}-{center_id[-8:]}"
        side = "long" if action in {"open_long", "close_short"} else "short"
        publish(
            "trade_signal",
            signal_id,
            {
                "signal_id": signal_id,
                "parent_stage_signal_id": f"CHAN-STATE-{bar.bar_index}-{center_id[-8:]}",
                "known_at_bar_index": bar.bar_index,
                "timestamp_utc": bar.timestamp_utc,
                "side": side,
                "action": action,
                "price_i64": bar.close_i64,
                "reason_code": reason,
            },
            bar.bar_index,
        )
        event_id = f"EVENT-{signal_id}"
        publish(
            "chart_event",
            event_id,
            {
                "event_id": event_id,
                "known_at_bar_index": bar.bar_index,
                "bar_index": bar.bar_index,
                "timestamp_utc": bar.timestamp_utc,
                "price_i64": bar.close_i64,
                "event_type": action,
                "reason_code": reason,
            },
            bar.bar_index,
        )
        position_side = {
            "open_long": "long",
            "open_short": "short",
            "close_long": "flat",
            "close_short": "flat",
        }[action]

    for bar in bars:
        if bar.bar_index % 256 == 0 and cancelled.is_set():
            raise InterruptedError("strategy execution cancelled")
        for event in events_by_bar.get(bar.bar_index, []):
            if event.object_type not in {"segment_zhongshu", "trade_point"}:
                continue
            target = centers if event.object_type == "segment_zhongshu" else third_points
            if event.operation == "delete":
                target.pop(event.object_id, None)
                continue
            value = json.loads(event.payload_json)
            if event.object_type == "trade_point" and value.get("signal_type") not in {
                "buy_3",
                "sell_3",
            }:
                continue
            target[event.object_id] = value
        confirmed_centers = [
            value
            for value in centers.values()
            if value.get("confirmed") and int(value["known_at_bar_index"]) <= bar.bar_index
        ]
        if not confirmed_centers:
            continue
        center = max(
            confirmed_centers,
            key=lambda value: (int(value["start_bar_index"]), int(value["known_at_bar_index"])),
        )
        center_id = str(center["object_id"])
        related_types = {
            str(value["signal_type"])
            for value in third_points.values()
            if value.get("reference_object_id") == center_id
            and int(value["known_at_bar_index"]) <= bar.bar_index
        }
        if bar.close_i64 > int(center["zg_i64"]):
            next_state = "above_with_B3" if "buy_3" in related_types else "above_without_B3"
        elif bar.close_i64 < int(center["zd_i64"]):
            next_state = "below_with_S3" if "sell_3" in related_types else "below_without_S3"
        else:
            next_state = "inside"
        if next_state == current_state:
            continue
        previous = current_state
        current_state = next_state
        reason = f"CENTRE_STATE_{next_state.upper()}"
        state_id = f"strategy_state-{bar.bar_index}"
        publish(
            "strategy_state",
            state_id,
            {
                "known_at_bar_index": bar.bar_index,
                "timestamp_utc": bar.timestamp_utc,
                "state_from": previous,
                "state_to": next_state,
                "price_i64": bar.close_i64,
                "reason_code": reason,
            },
            bar.bar_index,
        )
        stage_id = f"CHAN-STATE-{bar.bar_index}-{center_id[-8:]}"
        publish(
            "stage_signal",
            stage_id,
            {
                "stage_signal_id": stage_id,
                "known_at_bar_index": bar.bar_index,
                "timestamp_utc": bar.timestamp_utc,
                "stage": next_state,
                "status": "confirmed",
                "price_i64": bar.close_i64,
                "reason_code": reason,
            },
            bar.bar_index,
        )
        if position_side == "long" and next_state in {
            "inside",
            "below_without_S3",
            "below_with_S3",
        }:
            trade(bar, "close_long", "CENTRE_LONG_INVALIDATED", center_id)
        elif position_side == "short" and next_state in {
            "inside",
            "above_without_B3",
            "above_with_B3",
        }:
            trade(bar, "close_short", "CENTRE_SHORT_INVALIDATED", center_id)
        if (
            next_state == "above_with_B3"
            and bool(parameters["allow_long"])
            and position_side == "flat"
        ):
            trade(bar, "open_long", "CONFIRMED_B3_ABOVE_CENTRE", center_id)
        elif (
            next_state == "below_with_S3"
            and bool(parameters["allow_short"])
            and position_side == "flat"
        ):
            trade(bar, "open_short", "CONFIRMED_S3_BELOW_CENTRE", center_id)

    indicator_values = [{"bar_index": bar.bar_index, "ma": None} for bar in bars]
    return StrategyRun(bars, indicator_values, states, stages, signals, chart_events, causal_events)


def _run_trend_reversal(
    payload: dict[str, Any],
    guard: PathGuard,
    cancelled: threading.Event,
    *,
    last_bar_index: int | None,
    long_only: bool,
) -> StrategyRun:
    dataset = payload["dataset"]
    parameters = payload["parameters"]
    chan = chan_definition()
    chan_payload = {
        "dataset": dataset,
        "algorithm": {
            key: chan[key] for key in ("kind", "algorithm_id", "algorithm_version", "source_hash")
        },
        "parameters": {"checkpoint_interval": int(parameters["checkpoint_interval"])},
    }
    runtime, _, _ = _run_strategy_chan(
        payload, chan_payload, guard, cancelled, last_bar_index=last_bar_index
    )
    table = pq.read_table(
        guard.resolve(str(dataset["bars_path"])),
        columns=["bar_index", "timestamp_utc", "open_i64", "high_i64", "low_i64", "close_i64"],
    ).to_pydict()
    bars: list[StrategyBar] = []
    for row_position, value in enumerate(table["bar_index"]):
        bar_index = int(value)
        if last_bar_index is not None and bar_index > last_bar_index:
            break
        bars.append(
            StrategyBar(
                bar_index,
                int(table["timestamp_utc"][row_position]),
                int(table["open_i64"][row_position]),
                int(table["high_i64"][row_position]),
                int(table["low_i64"][row_position]),
                int(table["close_i64"][row_position]),
            )
        )

    events_by_bar: dict[int, list[Any]] = {}
    for event in runtime.emitter.events:
        if event.object_type == "trade_point" and event.operation == "upsert":
            events_by_bar.setdefault(event.known_at_bar_index, []).append(event)

    strategy_id = "DOWNREV" if long_only else "TRENDREV"
    current_state = "waiting_B1" if long_only else "waiting_trend_divergence"
    position_side = "flat"
    states: list[dict[str, Any]] = []
    stages: list[dict[str, Any]] = []
    signals: list[dict[str, Any]] = []
    chart_events: list[dict[str, Any]] = []
    causal_events: list[dict[str, Any]] = []
    revisions: dict[tuple[str, str], int] = {}

    def publish(object_type: str, object_id: str, row: dict[str, Any], bar_index: int) -> None:
        revision_key = (object_type, object_id)
        revision = revisions.get(revision_key, 0) + 1
        revisions[revision_key] = revision
        target = {
            "strategy_state": states,
            "stage_signal": stages,
            "trade_signal": signals,
            "chart_event": chart_events,
        }[object_type]
        target.append({**row, "object_revision": revision})
        event_payload = {**row, "object_id": object_id, "object_revision": revision}
        causal_events.append(
            {
                "event_seq": len(causal_events) + 1,
                "known_at_bar_index": bar_index,
                "object_type": object_type,
                "object_id": object_id,
                "operation": "upsert",
                "object_revision": revision,
                "payload_json": json.dumps(
                    event_payload, ensure_ascii=False, separators=(",", ":")
                ),
            }
        )

    def trade(bar: StrategyBar, action: str, reason: str, source_id: str) -> None:
        nonlocal position_side
        signal_id = f"CHAN-{strategy_id}-{bar.bar_index}-{action}-{source_id[-8:]}"
        side = "long" if action in {"open_long", "close_short"} else "short"
        parent_id = f"CHAN-{strategy_id}-STAGE-{bar.bar_index}-{source_id[-8:]}"
        publish(
            "trade_signal",
            signal_id,
            {
                "signal_id": signal_id,
                "parent_stage_signal_id": parent_id,
                "known_at_bar_index": bar.bar_index,
                "timestamp_utc": bar.timestamp_utc,
                "side": side,
                "action": action,
                "price_i64": bar.close_i64,
                "reason_code": reason,
                "reference_object_id": source_id,
            },
            bar.bar_index,
        )
        event_id = f"EVENT-{signal_id}"
        publish(
            "chart_event",
            event_id,
            {
                "event_id": event_id,
                "known_at_bar_index": bar.bar_index,
                "bar_index": bar.bar_index,
                "timestamp_utc": bar.timestamp_utc,
                "price_i64": bar.close_i64,
                "event_type": action,
                "reason_code": reason,
                "reference_object_id": source_id,
            },
            bar.bar_index,
        )
        position_side = {
            "open_long": "long",
            "open_short": "short",
            "close_long": "flat",
            "close_short": "flat",
        }[action]

    for bar in bars:
        if bar.bar_index % 256 == 0 and cancelled.is_set():
            raise InterruptedError("strategy execution cancelled")
        for event in events_by_bar.get(bar.bar_index, []):
            point = json.loads(event.payload_json)
            signal_type = str(point.get("signal_type"))
            if (
                point.get("confirmed", True) is not True
                or point.get("signal_class") != "standard"
                or signal_type not in {"buy_1", "sell_1"}
            ):
                continue
            if long_only and signal_type == "sell_1" and position_side == "flat":
                continue

            source_id = event.object_id
            next_state = "long_after_B1" if signal_type == "buy_1" else "short_after_S1"
            reason = "CONFIRMED_TREND_B1" if signal_type == "buy_1" else "CONFIRMED_TREND_S1"
            previous_state = current_state
            current_state = next_state
            state_id = f"strategy_state-{strategy_id}-{bar.bar_index}-{source_id[-8:]}"
            publish(
                "strategy_state",
                state_id,
                {
                    "known_at_bar_index": bar.bar_index,
                    "timestamp_utc": bar.timestamp_utc,
                    "state_from": previous_state,
                    "state_to": next_state,
                    "price_i64": bar.close_i64,
                    "reason_code": reason,
                    "reference_object_id": source_id,
                },
                bar.bar_index,
            )
            stage_id = f"CHAN-{strategy_id}-STAGE-{bar.bar_index}-{source_id[-8:]}"
            publish(
                "stage_signal",
                stage_id,
                {
                    "stage_signal_id": stage_id,
                    "known_at_bar_index": bar.bar_index,
                    "timestamp_utc": bar.timestamp_utc,
                    "stage": next_state,
                    "status": "confirmed",
                    "price_i64": bar.close_i64,
                    "reason_code": reason,
                    "reference_object_id": source_id,
                },
                bar.bar_index,
            )

            if signal_type == "buy_1":
                if position_side == "short":
                    trade(bar, "close_short", "OPPOSITE_CONFIRMED_B1", source_id)
                if position_side == "flat":
                    trade(bar, "open_long", reason, source_id)
            else:
                if position_side == "long":
                    trade(bar, "close_long", "OPPOSITE_CONFIRMED_S1", source_id)
                if not long_only and position_side == "flat":
                    trade(bar, "open_short", reason, source_id)

    indicator_values = [{"bar_index": bar.bar_index, "ma": None} for bar in bars]
    return StrategyRun(bars, indicator_values, states, stages, signals, chart_events, causal_events)


def _run_consolidation_reversion(
    payload: dict[str, Any],
    guard: PathGuard,
    cancelled: threading.Event,
    *,
    last_bar_index: int | None,
) -> StrategyRun:
    dataset = payload["dataset"]
    parameters = payload["parameters"]
    chan = chan_definition()
    chan_payload = {
        "dataset": dataset,
        "algorithm": {
            key: chan[key] for key in ("kind", "algorithm_id", "algorithm_version", "source_hash")
        },
        "parameters": {"checkpoint_interval": int(parameters["checkpoint_interval"])},
    }
    runtime, _, _ = _run_strategy_chan(
        payload, chan_payload, guard, cancelled, last_bar_index=last_bar_index
    )
    table = pq.read_table(
        guard.resolve(str(dataset["bars_path"])),
        columns=["bar_index", "timestamp_utc", "open_i64", "high_i64", "low_i64", "close_i64"],
    ).to_pydict()
    bars = [
        StrategyBar(
            int(value),
            int(table["timestamp_utc"][row_position]),
            int(table["open_i64"][row_position]),
            int(table["high_i64"][row_position]),
            int(table["low_i64"][row_position]),
            int(table["close_i64"][row_position]),
        )
        for row_position, value in enumerate(table["bar_index"])
        if last_bar_index is None or int(value) <= last_bar_index
    ]
    events_by_bar: dict[int, list[Any]] = {}
    for event in runtime.emitter.events:
        if event.object_type in {"segment_zhongshu", "divergence", "trade_point"}:
            events_by_bar.setdefault(event.known_at_bar_index, []).append(event)

    centers: dict[str, dict[str, Any]] = {}
    divergences: dict[str, dict[str, Any]] = {}
    current_state = "waiting_consolidation_divergence"
    position_side = "flat"
    active_center_id: str | None = None
    opened_at: int | None = None
    states: list[dict[str, Any]] = []
    stages: list[dict[str, Any]] = []
    signals: list[dict[str, Any]] = []
    chart_events: list[dict[str, Any]] = []
    causal_events: list[dict[str, Any]] = []
    revisions: dict[tuple[str, str], int] = {}

    def publish(object_type: str, object_id: str, row: dict[str, Any], bar_index: int) -> None:
        revision_key = (object_type, object_id)
        revision = revisions.get(revision_key, 0) + 1
        revisions[revision_key] = revision
        {
            "strategy_state": states,
            "stage_signal": stages,
            "trade_signal": signals,
            "chart_event": chart_events,
        }[object_type].append({**row, "object_revision": revision})
        event_payload = {**row, "object_id": object_id, "object_revision": revision}
        causal_events.append(
            {
                "event_seq": len(causal_events) + 1,
                "known_at_bar_index": bar_index,
                "object_type": object_type,
                "object_id": object_id,
                "operation": "upsert",
                "object_revision": revision,
                "payload_json": json.dumps(
                    event_payload, ensure_ascii=False, separators=(",", ":")
                ),
            }
        )

    def transition(
        bar: StrategyBar, next_state: str, reason: str, reference_object_id: str
    ) -> None:
        nonlocal current_state
        previous_state = current_state
        current_state = next_state
        suffix = reference_object_id[-8:]
        state_id = f"strategy_state-CONSREV-{bar.bar_index}-{suffix}"
        common = {
            "known_at_bar_index": bar.bar_index,
            "timestamp_utc": bar.timestamp_utc,
            "price_i64": bar.close_i64,
            "reason_code": reason,
            "reference_object_id": reference_object_id,
        }
        publish(
            "strategy_state",
            state_id,
            {**common, "state_from": previous_state, "state_to": next_state},
            bar.bar_index,
        )
        stage_id = f"CHAN-CONSREV-STAGE-{bar.bar_index}-{suffix}"
        publish(
            "stage_signal",
            stage_id,
            {**common, "stage_signal_id": stage_id, "stage": next_state, "status": "confirmed"},
            bar.bar_index,
        )

    def trade(bar: StrategyBar, action: str, reason: str, source_id: str) -> None:
        nonlocal position_side
        signal_id = f"CHAN-CONSREV-{bar.bar_index}-{action}-{source_id[-8:]}"
        side = "long" if action in {"open_long", "close_short"} else "short"
        common = {
            "known_at_bar_index": bar.bar_index,
            "timestamp_utc": bar.timestamp_utc,
            "price_i64": bar.close_i64,
            "reason_code": reason,
            "reference_object_id": source_id,
        }
        publish(
            "trade_signal",
            signal_id,
            {
                **common,
                "signal_id": signal_id,
                "parent_stage_signal_id": f"CHAN-CONSREV-STAGE-{bar.bar_index}-{source_id[-8:]}",
                "side": side,
                "action": action,
            },
            bar.bar_index,
        )
        event_id = f"EVENT-{signal_id}"
        publish(
            "chart_event",
            event_id,
            {
                **common,
                "event_id": event_id,
                "bar_index": bar.bar_index,
                "event_type": action,
            },
            bar.bar_index,
        )
        position_side = {
            "open_long": "long",
            "open_short": "short",
            "close_long": "flat",
            "close_short": "flat",
        }[action]

    for bar in bars:
        if bar.bar_index % 256 == 0 and cancelled.is_set():
            raise InterruptedError("strategy execution cancelled")
        new_points: list[tuple[Any, dict[str, Any]]] = []
        third_points: list[tuple[Any, dict[str, Any]]] = []
        for event in events_by_bar.get(bar.bar_index, []):
            target = centers if event.object_type == "segment_zhongshu" else divergences
            if event.operation == "delete":
                if event.object_type in {"segment_zhongshu", "divergence"}:
                    target.pop(event.object_id, None)
                continue
            value = json.loads(event.payload_json)
            if event.object_type == "segment_zhongshu":
                centers[event.object_id] = value
            elif event.object_type == "divergence":
                divergences[event.object_id] = value
            elif value.get("signal_type") in {"class_buy_1", "class_sell_1"}:
                new_points.append((event, value))
            elif value.get("signal_type") in {"buy_3", "sell_3"}:
                third_points.append((event, value))

        converted = False
        for event, point in third_points:
            if active_center_id is None or point.get("reference_object_id") != active_center_id:
                continue
            signal_type = str(point["signal_type"])
            if position_side == "long" and signal_type == "sell_3":
                transition(bar, "converted_to_S3", "REVERSION_FAILED_CONFIRMED_S3", event.object_id)
                trade(bar, "close_long", "REVERSION_FAILED_CONFIRMED_S3", event.object_id)
                trade(bar, "open_short", "SWITCH_TO_CONFIRMED_S3", event.object_id)
                converted = True
            elif position_side == "short" and signal_type == "buy_3":
                transition(bar, "converted_to_B3", "REVERSION_FAILED_CONFIRMED_B3", event.object_id)
                trade(bar, "close_short", "REVERSION_FAILED_CONFIRMED_B3", event.object_id)
                trade(bar, "open_long", "SWITCH_TO_CONFIRMED_B3", event.object_id)
                converted = True

        if (
            not converted
            and position_side != "flat"
            and active_center_id in centers
            and opened_at is not None
            and bar.bar_index > opened_at
        ):
            center = centers[active_center_id]
            if int(center["zd_i64"]) <= bar.close_i64 <= int(center["zg_i64"]):
                transition(bar, "returned_to_centre", "PRICE_RETURNED_TO_CENTRE", active_center_id)
                action = "close_long" if position_side == "long" else "close_short"
                trade(bar, action, "CENTRE_REVERSION_TARGET_REACHED", active_center_id)
                active_center_id = None
                opened_at = None

        if position_side != "flat":
            continue
        for event, point in new_points:
            divergence = divergences.get(str(point.get("reference_object_id")))
            if not divergence or divergence.get("divergence_kind") != "consolidation":
                continue
            center_id = str(divergence.get("reference_object_id") or "")
            if center_id not in centers:
                continue
            signal_type = str(point["signal_type"])
            next_state = (
                "reverting_up_to_centre"
                if signal_type == "class_buy_1"
                else "reverting_down_to_centre"
            )
            reason = (
                "CONFIRMED_CONSOLIDATION_BOTTOM_DIVERGENCE"
                if signal_type == "class_buy_1"
                else "CONFIRMED_CONSOLIDATION_TOP_DIVERGENCE"
            )
            transition(bar, next_state, reason, event.object_id)
            trade(
                bar,
                "open_long" if signal_type == "class_buy_1" else "open_short",
                reason,
                event.object_id,
            )
            active_center_id = center_id
            opened_at = bar.bar_index
            break

    indicator_values = [{"bar_index": bar.bar_index, "ma": None} for bar in bars]
    return StrategyRun(bars, indicator_values, states, stages, signals, chart_events, causal_events)


def _run_third_point_migration_hold(
    payload: dict[str, Any],
    guard: PathGuard,
    cancelled: threading.Event,
    *,
    last_bar_index: int | None,
    first_only: bool = False,
    macd_gate: bool = False,
) -> StrategyRun:
    dataset = payload["dataset"]
    parameters = payload["parameters"]
    chan = chan_definition()
    chan_payload = {
        "dataset": dataset,
        "algorithm": {
            key: chan[key] for key in ("kind", "algorithm_id", "algorithm_version", "source_hash")
        },
        "parameters": {"checkpoint_interval": int(parameters["checkpoint_interval"])},
    }
    runtime, _, _ = _run_strategy_chan(
        payload, chan_payload, guard, cancelled, last_bar_index=last_bar_index
    )
    table = pq.read_table(
        guard.resolve(str(dataset["bars_path"])),
        columns=["bar_index", "timestamp_utc", "open_i64", "high_i64", "low_i64", "close_i64"],
    ).to_pydict()
    bars = [
        StrategyBar(
            int(value),
            int(table["timestamp_utc"][row_position]),
            int(table["open_i64"][row_position]),
            int(table["high_i64"][row_position]),
            int(table["low_i64"][row_position]),
            int(table["close_i64"][row_position]),
        )
        for row_position, value in enumerate(table["bar_index"])
        if last_bar_index is None or int(value) <= last_bar_index
    ]
    macd_series = None
    macd_regimes_by_bar: dict[int, Any] = {}
    bar_positions = {bar.bar_index: position for position, bar in enumerate(bars)}
    macd_config: MacdZeroAxisConfig | None = None
    if macd_gate:
        meta = json.loads(guard.resolve(str(dataset["meta_path"])).read_text(encoding="utf-8"))
        tick_size_i64 = int(meta["price"].get("tick_size_i64") or 1)
        dataset_timeframe = str(meta.get("timeframe") or "")
        macd_config = MacdZeroAxisConfig.from_parameters(
            parameters, tick_size_i64, dataset_timeframe
        )
        macd_series = compute_macd_zero_axis_series([bar.close_i64 for bar in bars], macd_config)
        macd_regimes_by_bar = {
            bar.bar_index: regime
            for bar, regime in zip(
                bars,
                classify_macd_directional_regimes(macd_series, macd_config),
                strict=True,
            )
        }
    events_by_bar: dict[int, list[Any]] = {}
    for event in runtime.emitter.events:
        if event.object_type in {"segment_zhongshu", "trade_point"}:
            events_by_bar.setdefault(event.known_at_bar_index, []).append(event)

    position_side = "flat"
    current_state = "waiting_strict_third_point"
    origin_center_id: str | None = None
    cycle_used = {"buy": False, "sell": False}
    strategy_tag = (
        "ROTATEMACD"
        if first_only and macd_gate
        else "MIGHOLDMACD"
        if macd_gate
        else "ROTATE"
        if first_only
        else "MIGHOLD"
    )
    states: list[dict[str, Any]] = []
    stages: list[dict[str, Any]] = []
    signals: list[dict[str, Any]] = []
    chart_events: list[dict[str, Any]] = []
    causal_events: list[dict[str, Any]] = []
    revisions: dict[tuple[str, str], int] = {}

    def publish(object_type: str, object_id: str, row: dict[str, Any], bar_index: int) -> None:
        revision_key = (object_type, object_id)
        revision = revisions.get(revision_key, 0) + 1
        revisions[revision_key] = revision
        {
            "strategy_state": states,
            "stage_signal": stages,
            "trade_signal": signals,
            "chart_event": chart_events,
        }[object_type].append({**row, "object_revision": revision})
        event_payload = {**row, "object_id": object_id, "object_revision": revision}
        causal_events.append(
            {
                "event_seq": len(causal_events) + 1,
                "known_at_bar_index": bar_index,
                "object_type": object_type,
                "object_id": object_id,
                "operation": "upsert",
                "object_revision": revision,
                "payload_json": json.dumps(
                    event_payload, ensure_ascii=False, separators=(",", ":")
                ),
            }
        )

    def transition(
        bar: StrategyBar,
        next_state: str,
        reason: str,
        reference_object_id: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        nonlocal current_state
        previous_state = current_state
        current_state = next_state
        suffix = reference_object_id[-8:]
        common = {
            "known_at_bar_index": bar.bar_index,
            "timestamp_utc": bar.timestamp_utc,
            "price_i64": bar.close_i64,
            "reason_code": reason,
            "reference_object_id": reference_object_id,
            **(details or {}),
        }
        state_id = f"strategy_state-{strategy_tag}-{bar.bar_index}-{suffix}"
        publish(
            "strategy_state",
            state_id,
            {**common, "state_from": previous_state, "state_to": next_state},
            bar.bar_index,
        )
        stage_id = f"CHAN-{strategy_tag}-STAGE-{bar.bar_index}-{suffix}"
        publish(
            "stage_signal",
            stage_id,
            {**common, "stage_signal_id": stage_id, "stage": next_state, "status": "confirmed"},
            bar.bar_index,
        )

    def trade(
        bar: StrategyBar,
        action: str,
        reason: str,
        source_id: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        nonlocal position_side
        signal_id = f"CHAN-{strategy_tag}-{bar.bar_index}-{action}-{source_id[-8:]}"
        side = "long" if action in {"open_long", "close_short"} else "short"
        common = {
            "known_at_bar_index": bar.bar_index,
            "timestamp_utc": bar.timestamp_utc,
            "price_i64": bar.close_i64,
            "reason_code": reason,
            "reference_object_id": source_id,
            **(details or {}),
        }
        publish(
            "trade_signal",
            signal_id,
            {
                **common,
                "signal_id": signal_id,
                "parent_stage_signal_id": (
                    f"CHAN-{strategy_tag}-STAGE-{bar.bar_index}-{source_id[-8:]}"
                ),
                "side": side,
                "action": action,
            },
            bar.bar_index,
        )
        publish(
            "chart_event",
            f"EVENT-{signal_id}",
            {
                **common,
                "event_id": f"EVENT-{signal_id}",
                "bar_index": bar.bar_index,
                "event_type": action,
            },
            bar.bar_index,
        )
        position_side = {
            "open_long": "long",
            "open_short": "short",
            "close_long": "flat",
            "close_short": "flat",
        }[action]

    for bar in bars:
        if bar.bar_index % 256 == 0 and cancelled.is_set():
            raise InterruptedError("strategy execution cancelled")
        new_centers: list[Any] = []
        points: list[tuple[Any, dict[str, Any]]] = []
        for event in events_by_bar.get(bar.bar_index, []):
            if event.operation != "upsert":
                continue
            value = json.loads(event.payload_json)
            if event.object_type == "segment_zhongshu" and value.get("confirmed"):
                new_centers.append(event)
            elif event.object_type == "trade_point" and value.get("confirmed", True) is True:
                points.append((event, value))

        if position_side != "flat":
            exit_event: Any | None = next(
                (event for event in new_centers if event.object_id != origin_center_id), None
            )
            exit_reason = "NEW_SAME_LEVEL_CENTRE_CONFIRMED" if exit_event else ""
            if exit_event is None:
                expected = "sell_1" if position_side == "long" else "buy_1"
                expected_class = "class_sell_1" if position_side == "long" else "class_buy_1"
                matched = next(
                    (
                        (event, point)
                        for event, point in points
                        if (
                            point.get("signal_type") == expected
                            and point.get("signal_class") == "standard"
                        )
                        or (first_only and point.get("signal_type") == expected_class)
                    ),
                    None,
                )
                if matched is not None:
                    exit_event, matched_point = matched
                    exit_reason = (
                        "CONSOLIDATION_DIVERGENCE_CONFIRMED"
                        if str(matched_point.get("signal_type")).startswith("class_")
                        else "SAME_LEVEL_TREND_DIVERGENCE_CONFIRMED"
                    )
            if exit_event is not None:
                transition(bar, "migration_hold_exited", exit_reason, exit_event.object_id)
                trade(
                    bar,
                    "close_long" if position_side == "long" else "close_short",
                    exit_reason,
                    exit_event.object_id,
                )
                origin_center_id = None

        if position_side != "flat":
            continue
        entry = next(
            (
                (event, point)
                for event, point in points
                if point.get("signal_type") in {"buy_3", "sell_3"}
                and point.get("signal_class") == "standard"
            ),
            None,
        )
        if entry is None:
            continue
        event, point = entry
        signal_type = str(point["signal_type"])
        direction = "buy" if signal_type == "buy_3" else "sell"
        opposite = "sell" if direction == "buy" else "buy"
        cycle_used[opposite] = False
        if first_only and cycle_used[direction]:
            transition(
                bar,
                f"later_centre_{signal_type.upper()}_filtered",
                "LATER_CENTRE_THIRD_POINT_FILTERED",
                event.object_id,
            )
            continue
        cycle_used[direction] = True
        macd_details: dict[str, Any] = {}
        if macd_gate:
            assert macd_config is not None and macd_series is not None
            regime = macd_regimes_by_bar[bar.bar_index]
            position = bar_positions[bar.bar_index]
            expected_regime = "bullish" if signal_type == "buy_3" else "bearish"
            macd_details = {
                "catalog_algorithm_ids": ["ALG-STR-003", "ALG-AUX-002"],
                "structural_signal_type": signal_type,
                "structural_signal_class": "standard",
                "macd_role": "entry_filter_only",
                "macd_regime": regime.direction,
                "macd_diff": macd_series.diff[position],
                "macd_dea": macd_series.dea[position],
                "macd_bullish_count": regime.bullish_count,
                "macd_bearish_count": regime.bearish_count,
                "macd_zero_axis_buffer_i64": macd_config.zero_axis_buffer_i64,
            }
            if regime.direction != expected_regime:
                reason = (
                    "B3_MACD_BULL_REGIME_NOT_CONFIRMED"
                    if signal_type == "buy_3"
                    else "S3_MACD_BEAR_REGIME_NOT_CONFIRMED"
                )
                transition(
                    bar,
                    f"macd_regime_{signal_type}_filtered",
                    reason,
                    event.object_id,
                    {**macd_details, "execution_allowed": False},
                )
                continue
        next_state = (
            "holding_upward_migration" if signal_type == "buy_3" else "holding_downward_migration"
        )
        reason = (
            "CONFIRMED_B3_AND_MACD_BULL_REGIME_ENTRY"
            if macd_gate and signal_type == "buy_3"
            else "CONFIRMED_S3_AND_MACD_BEAR_REGIME_ENTRY"
            if macd_gate
            else "CONFIRMED_B3_MIGRATION_ENTRY"
            if signal_type == "buy_3"
            else "CONFIRMED_S3_MIGRATION_ENTRY"
        )
        transition(bar, next_state, reason, event.object_id, macd_details)
        trade(
            bar,
            "open_long" if signal_type == "buy_3" else "open_short",
            reason,
            event.object_id,
            macd_details,
        )
        origin_center_id = str(point.get("reference_object_id") or "")

    indicator_values = [{"bar_index": bar.bar_index, "ma": None} for bar in bars]
    return StrategyRun(bars, indicator_values, states, stages, signals, chart_events, causal_events)


def _run_second_buy_only(
    payload: dict[str, Any],
    guard: PathGuard,
    cancelled: threading.Event,
    *,
    last_bar_index: int | None,
) -> StrategyRun:
    """Run the causal ALG-STR-002 state machine over confirmed Chan objects only."""
    dataset = payload["dataset"]
    parameters = payload["parameters"]
    quantities = {
        strength: int(parameters[f"{strength}_quantity"])
        for strength in ("strongest", "normal", "weakest")
    }
    if any(value < 1 or value > 100 for value in quantities.values()):
        raise ValueError("second-buy quantities must be between 1 and 100")
    if quantities["weakest"] > min(quantities["strongest"], quantities["normal"]):
        raise ValueError("weakest_quantity must not exceed other second-buy quantities")
    allowed = {
        strength: bool(parameters[f"allow_{strength}"])
        for strength in ("strongest", "normal", "weakest")
    }

    chan = chan_definition()
    chan_payload = {
        "dataset": dataset,
        "algorithm": {
            key: chan[key] for key in ("kind", "algorithm_id", "algorithm_version", "source_hash")
        },
        "parameters": {"checkpoint_interval": int(parameters["checkpoint_interval"])},
    }
    runtime, _, _ = _run_strategy_chan(
        payload, chan_payload, guard, cancelled, last_bar_index=last_bar_index
    )
    table = pq.read_table(
        guard.resolve(str(dataset["bars_path"])),
        columns=["bar_index", "timestamp_utc", "open_i64", "high_i64", "low_i64", "close_i64"],
    ).to_pydict()
    bars = [
        StrategyBar(
            int(value),
            int(table["timestamp_utc"][row_position]),
            int(table["open_i64"][row_position]),
            int(table["high_i64"][row_position]),
            int(table["low_i64"][row_position]),
            int(table["close_i64"][row_position]),
        )
        for row_position, value in enumerate(table["bar_index"])
        if last_bar_index is None or int(value) <= last_bar_index
    ]
    events_by_bar: dict[int, list[Any]] = {}
    for event in runtime.emitter.events:
        if event.object_type in {"segment", "divergence", "trade_point"}:
            events_by_bar.setdefault(event.known_at_bar_index, []).append(event)

    active_segments: dict[str, dict[str, Any]] = {}
    active_divergences: dict[str, dict[str, Any]] = {}
    active_points: dict[str, dict[str, Any]] = {}
    current_state = "waiting_standard_B2"
    position_quantity = 0
    current_b2_id: str | None = None
    b2_segment_id: str | None = None
    b2_end_bar_index: int | None = None
    b2_price_i64: int | None = None
    rebound_high_i64: int | None = None
    origin_center_id: str | None = None
    b3_seen = False
    followthrough_segment_id: str | None = None
    states: list[dict[str, Any]] = []
    stages: list[dict[str, Any]] = []
    signals: list[dict[str, Any]] = []
    chart_events: list[dict[str, Any]] = []
    causal_events: list[dict[str, Any]] = []
    revisions: dict[tuple[str, str], int] = {}

    def publish(object_type: str, object_id: str, row: dict[str, Any], bar_index: int) -> None:
        revision_key = (object_type, object_id)
        revision = revisions.get(revision_key, 0) + 1
        revisions[revision_key] = revision
        {
            "strategy_state": states,
            "stage_signal": stages,
            "trade_signal": signals,
            "chart_event": chart_events,
        }[object_type].append({**row, "object_revision": revision})
        event_payload = {**row, "object_id": object_id, "object_revision": revision}
        causal_events.append(
            {
                "event_seq": len(causal_events) + 1,
                "known_at_bar_index": bar_index,
                "object_type": object_type,
                "object_id": object_id,
                "operation": "upsert",
                "object_revision": revision,
                "payload_json": json.dumps(
                    event_payload, ensure_ascii=False, separators=(",", ":")
                ),
            }
        )

    def transition(
        bar: StrategyBar,
        next_state: str,
        reason: str,
        source_id: str,
        *,
        anchor_bar_index: int | None = None,
        price_i64: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> str:
        nonlocal current_state
        previous_state = current_state
        current_state = next_state
        suffix = source_id[-8:]
        common = {
            "known_at_bar_index": bar.bar_index,
            "timestamp_utc": bar.timestamp_utc,
            "bar_index": bar.bar_index if anchor_bar_index is None else anchor_bar_index,
            "price_i64": bar.close_i64 if price_i64 is None else price_i64,
            "reason_code": reason,
            "reference_object_id": source_id,
            **(details or {}),
        }
        state_id = f"strategy_state-B2-{bar.bar_index}-{suffix}"
        publish(
            "strategy_state",
            state_id,
            {**common, "state_from": previous_state, "state_to": next_state},
            bar.bar_index,
        )
        stage_id = f"CHAN-B2-STAGE-{bar.bar_index}-{suffix}"
        publish(
            "stage_signal",
            stage_id,
            {**common, "stage_signal_id": stage_id, "stage": next_state, "status": "confirmed"},
            bar.bar_index,
        )
        return stage_id

    def chart_event(
        bar: StrategyBar,
        event_type: str,
        reason: str,
        source_id: str,
        *,
        anchor_bar_index: int,
        price_i64: int,
        details: dict[str, Any] | None = None,
    ) -> None:
        event_id = f"CHAN-B2-EVENT-{bar.bar_index}-{event_type}-{source_id[-8:]}"
        publish(
            "chart_event",
            event_id,
            {
                "event_id": event_id,
                "known_at_bar_index": bar.bar_index,
                "timestamp_utc": bar.timestamp_utc,
                "bar_index": anchor_bar_index,
                "price_i64": price_i64,
                "event_type": event_type,
                "reason_code": reason,
                "reference_object_id": source_id,
                **(details or {}),
            },
            bar.bar_index,
        )

    def trade(
        bar: StrategyBar,
        action: str,
        quantity: int,
        reason: str,
        source_id: str,
        *,
        anchor_bar_index: int,
        price_i64: int,
        stage_id: str,
        strength: str | None = None,
    ) -> None:
        nonlocal position_quantity
        signal_id = f"CHAN-B2-{bar.bar_index}-{action}-{source_id[-8:]}"
        common = {
            "known_at_bar_index": bar.bar_index,
            "timestamp_utc": bar.timestamp_utc,
            "bar_index": anchor_bar_index,
            "price_i64": price_i64,
            "reason_code": reason,
            "reference_object_id": source_id,
        }
        publish(
            "trade_signal",
            signal_id,
            {
                **common,
                "signal_id": signal_id,
                "parent_stage_signal_id": stage_id,
                "side": (
                    "long"
                    if action in {"open_long", "add_long", "reduce_short", "close_short"}
                    else "short"
                ),
                "action": action,
                "quantity": quantity,
                "strength": strength,
            },
            bar.bar_index,
        )
        chart_event(
            bar,
            action,
            reason,
            source_id,
            anchor_bar_index=anchor_bar_index,
            price_i64=price_i64,
            details={"quantity": quantity, "strength": strength},
        )
        position_quantity = quantity if action == "open_long" else 0

    def confirmed_segment_for(
        point: dict[str, Any], direction: str
    ) -> tuple[str, dict[str, Any]] | None:
        endpoint = int(point.get("bar_index", -1))
        price = int(point.get("price_i64", 0))
        candidates = [
            (object_id, value)
            for object_id, value in active_segments.items()
            if value.get("confirmed")
            and value.get("direction") == direction
            and int(value.get("end_bar_index", -2)) == endpoint
        ]
        exact = [item for item in candidates if int(item[1].get("end_price_i64", 0)) == price]
        values = exact or candidates
        if not values:
            return None
        return max(
            values,
            key=lambda item: (int(item[1].get("start_bar_index", -1)), item[0]),
        )

    def preceding_rebound(segment: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
        start = int(segment["start_bar_index"])
        candidates = [
            (object_id, value)
            for object_id, value in active_segments.items()
            if value.get("confirmed")
            and value.get("direction") == "up"
            and int(value.get("end_bar_index", -1)) == start
        ]
        if not candidates:
            return None
        return max(
            candidates,
            key=lambda item: (int(item[1].get("start_bar_index", -1)), item[0]),
        )

    def first_followthrough() -> tuple[str, dict[str, Any]] | None:
        if b2_end_bar_index is None:
            return None
        candidates = [
            (object_id, value)
            for object_id, value in active_segments.items()
            if value.get("confirmed")
            and value.get("direction") == "up"
            and int(value.get("start_bar_index", -1)) == b2_end_bar_index
            and int(value.get("end_bar_index", -1)) > b2_end_bar_index
        ]
        if not candidates:
            return None
        return min(
            candidates,
            key=lambda item: (int(item[1].get("end_bar_index", 2**63 - 1)), item[0]),
        )

    def standard_point(value: dict[str, Any], *signal_types: str) -> bool:
        return (
            value.get("confirmed") is True
            and value.get("signal_class") == "standard"
            and value.get("signal_type") in signal_types
        )

    def reset_position_context() -> None:
        nonlocal current_b2_id, b2_segment_id, b2_end_bar_index
        nonlocal b2_price_i64, rebound_high_i64, origin_center_id, b3_seen
        nonlocal followthrough_segment_id
        current_b2_id = None
        b2_segment_id = None
        b2_end_bar_index = None
        b2_price_i64 = None
        rebound_high_i64 = None
        origin_center_id = None
        b3_seen = False
        followthrough_segment_id = None

    for bar in bars:
        if bar.bar_index % 256 == 0 and cancelled.is_set():
            raise InterruptedError("strategy execution cancelled")
        new_points: list[tuple[str, dict[str, Any]]] = []
        deleted_point_ids: set[str] = set()
        for event in events_by_bar.get(bar.bar_index, []):
            target = {
                "segment": active_segments,
                "divergence": active_divergences,
                "trade_point": active_points,
            }[event.object_type]
            if event.operation == "delete":
                target.pop(event.object_id, None)
                if event.object_type == "trade_point":
                    deleted_point_ids.add(event.object_id)
                continue
            value = json.loads(event.payload_json)
            target[event.object_id] = value
            if event.object_type == "trade_point":
                new_points.append((event.object_id, value))

        exited_this_bar = False
        revised_current_b2 = current_b2_id is not None and any(
            object_id == current_b2_id for object_id, _ in new_points
        )
        if position_quantity and (current_b2_id in deleted_point_ids or revised_current_b2):
            source_id = current_b2_id or "revised-B2"
            stage_id = transition(
                bar,
                "exited_B2_source_revision",
                "B2_SOURCE_REVISED",
                source_id,
            )
            trade(
                bar,
                "close_long",
                position_quantity,
                "B2_SOURCE_REVISED",
                source_id,
                anchor_bar_index=bar.bar_index,
                price_i64=bar.close_i64,
                stage_id=stage_id,
            )
            reset_position_context()
            exited_this_bar = True

        opposing = next(
            (
                (object_id, value)
                for object_id, value in new_points
                if standard_point(value, "sell_1", "sell_2", "sell_3")
            ),
            None,
        )
        if position_quantity and opposing is not None:
            object_id, point = opposing
            stage_id = transition(
                bar,
                "exited_standard_sell_point",
                "STANDARD_SELL_POINT_CONFIRMED",
                object_id,
                anchor_bar_index=int(point.get("bar_index", bar.bar_index)),
                price_i64=int(point.get("price_i64", bar.close_i64)),
            )
            trade(
                bar,
                "close_long",
                position_quantity,
                "STANDARD_SELL_POINT_CONFIRMED",
                object_id,
                anchor_bar_index=int(point.get("bar_index", bar.bar_index)),
                price_i64=int(point.get("price_i64", bar.close_i64)),
                stage_id=stage_id,
            )
            reset_position_context()
            exited_this_bar = True

        if position_quantity:
            matching_b3 = next(
                (
                    (object_id, value)
                    for object_id, value in active_points.items()
                    if standard_point(value, "buy_3")
                    and (
                        (
                            origin_center_id is not None
                            and value.get("reference_object_id") == origin_center_id
                        )
                        or (
                            b2_end_bar_index is not None
                            and int(value.get("bar_index", -1)) == b2_end_bar_index
                            and b2_price_i64 is not None
                            and int(value.get("price_i64", 0)) == b2_price_i64
                        )
                    )
                ),
                None,
            )
            b3_seen = matching_b3 is not None

        if position_quantity and followthrough_segment_id is None:
            followthrough = first_followthrough()
            if followthrough is not None:
                followthrough_segment_id, segment = followthrough
                endpoint = int(segment["end_bar_index"])
                endpoint_price = int(segment["end_price_i64"])
                made_new_high = (
                    rebound_high_i64 is not None
                    and max(int(segment["start_price_i64"]), endpoint_price) > rebound_high_i64
                )
                top_divergence = next(
                    (
                        (object_id, value)
                        for object_id, value in active_divergences.items()
                        if value.get("confirmed", True)
                        and value.get("signal_type") == "top_divergence"
                        and int(value.get("bar_index", -1)) == endpoint
                    ),
                    None,
                )
                if not made_new_high:
                    stage_id = transition(
                        bar,
                        "exited_followthrough_failure",
                        "NEXT_UP_FAILED_NEW_HIGH",
                        followthrough_segment_id,
                        anchor_bar_index=endpoint,
                        price_i64=endpoint_price,
                    )
                    trade(
                        bar,
                        "close_long",
                        position_quantity,
                        "NEXT_UP_FAILED_NEW_HIGH",
                        followthrough_segment_id,
                        anchor_bar_index=endpoint,
                        price_i64=endpoint_price,
                        stage_id=stage_id,
                    )
                    reset_position_context()
                    exited_this_bar = True
                elif top_divergence is not None:
                    divergence_id, divergence = top_divergence
                    reason = (
                        "FOLLOWTHROUGH_CONSOLIDATION_DIVERGENCE"
                        if divergence.get("divergence_kind") == "consolidation"
                        else "FOLLOWTHROUGH_TREND_DIVERGENCE"
                    )
                    stage_id = transition(
                        bar,
                        "exited_followthrough_divergence",
                        reason,
                        divergence_id,
                        anchor_bar_index=endpoint,
                        price_i64=endpoint_price,
                    )
                    trade(
                        bar,
                        "close_long",
                        position_quantity,
                        reason,
                        divergence_id,
                        anchor_bar_index=endpoint,
                        price_i64=endpoint_price,
                        stage_id=stage_id,
                    )
                    reset_position_context()
                    exited_this_bar = True
                elif b3_seen:
                    transition(
                        bar,
                        "handed_off_B3_trend",
                        "B3_NONDIVERGENT_HANDOFF",
                        followthrough_segment_id,
                        anchor_bar_index=endpoint,
                        price_i64=endpoint_price,
                    )
                    chart_event(
                        bar,
                        "handoff_to_B3_trend",
                        "B3_NONDIVERGENT_HANDOFF",
                        followthrough_segment_id,
                        anchor_bar_index=endpoint,
                        price_i64=endpoint_price,
                        details={"origin_center_id": origin_center_id},
                    )
                else:
                    transition(
                        bar,
                        "holding_after_followthrough",
                        "NEXT_UP_NEW_HIGH_CONFIRMED",
                        followthrough_segment_id,
                        anchor_bar_index=endpoint,
                        price_i64=endpoint_price,
                    )
                    chart_event(
                        bar,
                        "followthrough_confirmed",
                        "NEXT_UP_NEW_HIGH_CONFIRMED",
                        followthrough_segment_id,
                        anchor_bar_index=endpoint,
                        price_i64=endpoint_price,
                    )

        if position_quantity or exited_this_bar:
            continue
        entries = [
            (object_id, value)
            for object_id, value in new_points
            if standard_point(value, "buy_2")
            and value.get("strength") in {"strongest", "normal", "weakest"}
        ]
        if not entries:
            continue
        strength_priority = {"strongest": 0, "normal": 1, "weakest": 2}
        entry = min(
            entries,
            key=lambda item: (strength_priority[str(item[1]["strength"])], item[0]),
        )
        object_id, point = entry
        strength = str(point["strength"])
        endpoint = int(point.get("bar_index", bar.bar_index))
        point_price = int(point.get("price_i64", bar.close_i64))
        if opposing is not None:
            transition(
                bar,
                f"B2_{strength}_risk_filtered",
                "B2_RISK_FILTER_BLOCKED_BY_SELL_POINT",
                object_id,
                anchor_bar_index=endpoint,
                price_i64=point_price,
                details={"strength": strength},
            )
            continue
        if not allowed[strength]:
            transition(
                bar,
                f"B2_{strength}_filtered",
                "B2_VARIANT_DISABLED",
                object_id,
                anchor_bar_index=endpoint,
                price_i64=point_price,
                details={"strength": strength},
            )
            continue
        b2_segment = confirmed_segment_for(point, "down")
        rebound = None if b2_segment is None else preceding_rebound(b2_segment[1])
        if b2_segment is None or rebound is None:
            transition(
                bar,
                f"B2_{strength}_dependency_filtered",
                "B2_CONFIRMED_SEGMENT_CHAIN_MISSING",
                object_id,
                anchor_bar_index=endpoint,
                price_i64=point_price,
                details={"strength": strength},
            )
            continue
        source_divergence = active_divergences.get(str(point.get("reference_object_id") or ""))
        current_b2_id = object_id
        b2_segment_id, b2_segment_value = b2_segment
        b2_end_bar_index = int(b2_segment_value["end_bar_index"])
        b2_price_i64 = point_price
        rebound_high_i64 = max(int(rebound[1]["start_price_i64"]), int(rebound[1]["end_price_i64"]))
        origin_center_id = (
            None
            if source_divergence is None
            else str(source_divergence.get("reference_object_id") or "") or None
        )
        b3_seen = any(
            standard_point(value, "buy_3")
            and (
                (
                    origin_center_id is not None
                    and value.get("reference_object_id") == origin_center_id
                )
                or (
                    int(value.get("bar_index", -1)) == b2_end_bar_index
                    and int(value.get("price_i64", 0)) == b2_price_i64
                )
            )
            for value in active_points.values()
        )
        reason = {
            "strongest": "CONFIRMED_STRONGEST_B2_ENTRY",
            "normal": "CONFIRMED_NORMAL_B2_ENTRY",
            "weakest": "CONFIRMED_WEAKEST_B2_REDUCED_ENTRY",
        }[strength]
        quantity = quantities[strength]
        stage_id = transition(
            bar,
            f"long_after_B2_{strength}",
            reason,
            object_id,
            anchor_bar_index=endpoint,
            price_i64=point_price,
            details={
                "strength": strength,
                "quantity": quantity,
                "b2_segment_id": b2_segment_id,
                "origin_center_id": origin_center_id,
            },
        )
        trade(
            bar,
            "open_long",
            quantity,
            reason,
            object_id,
            anchor_bar_index=endpoint,
            price_i64=point_price,
            stage_id=stage_id,
            strength=strength,
        )

    indicator_values = [{"bar_index": bar.bar_index, "ma": None} for bar in bars]
    return StrategyRun(bars, indicator_values, states, stages, signals, chart_events, causal_events)


@dataclass(frozen=True)
class _ChanStrategyFeed:
    bars: list[StrategyBar]
    events_by_bar: dict[int, list[Any]]
    volume_by_bar: dict[int, int | None]


def _load_chan_strategy_feed(
    payload: dict[str, Any],
    guard: PathGuard,
    cancelled: threading.Event,
    *,
    last_bar_index: int | None,
    object_types: set[str],
) -> _ChanStrategyFeed:
    dataset = payload["dataset"]
    parameters = payload["parameters"]
    chan = chan_definition()
    chan_payload = {
        "dataset": dataset,
        "algorithm": {
            key: chan[key] for key in ("kind", "algorithm_id", "algorithm_version", "source_hash")
        },
        "parameters": {"checkpoint_interval": int(parameters["checkpoint_interval"])},
    }
    runtime, _, _ = _run_strategy_chan(
        payload, chan_payload, guard, cancelled, last_bar_index=last_bar_index
    )
    path = guard.resolve(str(dataset["bars_path"]))
    columns = ["bar_index", "timestamp_utc", "open_i64", "high_i64", "low_i64", "close_i64"]
    has_volume = "volume" in pq.read_schema(path).names
    if has_volume:
        columns.append("volume")
    table = pq.read_table(path, columns=columns).to_pydict()
    bars: list[StrategyBar] = []
    volume_by_bar: dict[int, int | None] = {}
    for row_position, value in enumerate(table["bar_index"]):
        bar_index = int(value)
        if last_bar_index is not None and bar_index > last_bar_index:
            break
        bars.append(
            StrategyBar(
                bar_index,
                int(table["timestamp_utc"][row_position]),
                int(table["open_i64"][row_position]),
                int(table["high_i64"][row_position]),
                int(table["low_i64"][row_position]),
                int(table["close_i64"][row_position]),
            )
        )
        volume = table["volume"][row_position] if has_volume else None
        volume_by_bar[bar_index] = None if volume is None else int(volume)
    events_by_bar: dict[int, list[Any]] = {}
    for event in runtime.emitter.events:
        if event.object_type in object_types:
            events_by_bar.setdefault(event.known_at_bar_index, []).append(event)
    return _ChanStrategyFeed(bars, events_by_bar, volume_by_bar)


class _CausalStrategyOutput:
    def __init__(self, tag: str, initial_state: str) -> None:
        self.tag = tag
        self.current_state = initial_state
        self.states: list[dict[str, Any]] = []
        self.stages: list[dict[str, Any]] = []
        self.signals: list[dict[str, Any]] = []
        self.chart_events: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self.revisions: dict[tuple[str, str], int] = {}

    def publish(
        self, object_type: str, object_id: str, row: dict[str, Any], bar_index: int
    ) -> None:
        revision_key = (object_type, object_id)
        revision = self.revisions.get(revision_key, 0) + 1
        self.revisions[revision_key] = revision
        {
            "strategy_state": self.states,
            "stage_signal": self.stages,
            "trade_signal": self.signals,
            "chart_event": self.chart_events,
        }[object_type].append({**row, "object_revision": revision})
        payload = {**row, "object_id": object_id, "object_revision": revision}
        self.events.append(
            {
                "event_seq": len(self.events) + 1,
                "known_at_bar_index": bar_index,
                "object_type": object_type,
                "object_id": object_id,
                "operation": "upsert",
                "object_revision": revision,
                "payload_json": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            }
        )

    def delete(self, object_type: str, object_id: str, bar_index: int) -> None:
        """Delete a previously published semantic object at its causal invalidation time."""
        if object_type != "chart_event":
            raise ValueError("causal delete currently supports chart_event objects only")
        self.chart_events[:] = [
            row for row in self.chart_events if row.get("event_id") != object_id
        ]
        revision_key = (object_type, object_id)
        revision = self.revisions.get(revision_key, 0) + 1
        self.revisions[revision_key] = revision
        self.events.append(
            {
                "event_seq": len(self.events) + 1,
                "known_at_bar_index": bar_index,
                "object_type": object_type,
                "object_id": object_id,
                "operation": "delete",
                "object_revision": revision,
                "payload_json": json.dumps(
                    {"object_id": object_id, "object_revision": revision},
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            }
        )

    def transition(
        self,
        bar: StrategyBar,
        next_state: str,
        reason: str,
        source_id: str,
        *,
        anchor_bar_index: int | None = None,
        price_i64: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> str:
        previous_state = self.current_state
        self.current_state = next_state
        suffix = source_id[-8:]
        common = {
            "known_at_bar_index": bar.bar_index,
            "timestamp_utc": bar.timestamp_utc,
            "bar_index": bar.bar_index if anchor_bar_index is None else anchor_bar_index,
            "price_i64": bar.close_i64 if price_i64 is None else price_i64,
            "reason_code": reason,
            "reference_object_id": source_id,
            **(details or {}),
        }
        state_id = f"strategy_state-{self.tag}-{bar.bar_index}-{suffix}"
        self.publish(
            "strategy_state",
            state_id,
            {**common, "state_from": previous_state, "state_to": next_state},
            bar.bar_index,
        )
        stage_id = f"CHAN-{self.tag}-STAGE-{bar.bar_index}-{suffix}"
        self.publish(
            "stage_signal",
            stage_id,
            {**common, "stage_signal_id": stage_id, "stage": next_state, "status": "confirmed"},
            bar.bar_index,
        )
        return stage_id

    def chart_event(
        self,
        bar: StrategyBar,
        event_type: str,
        reason: str,
        source_id: str,
        *,
        anchor_bar_index: int,
        price_i64: int,
        details: dict[str, Any] | None = None,
    ) -> None:
        event_id = f"CHAN-{self.tag}-EVENT-{bar.bar_index}-{event_type}-{source_id[-8:]}"
        self.publish(
            "chart_event",
            event_id,
            {
                "event_id": event_id,
                "known_at_bar_index": bar.bar_index,
                "timestamp_utc": bar.timestamp_utc,
                "bar_index": anchor_bar_index,
                "price_i64": price_i64,
                "event_type": event_type,
                "reason_code": reason,
                "reference_object_id": source_id,
                **(details or {}),
            },
            bar.bar_index,
        )

    def trade(
        self,
        bar: StrategyBar,
        action: str,
        quantity: int,
        reason: str,
        source_id: str,
        *,
        anchor_bar_index: int,
        price_i64: int,
        stage_id: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        signal_id = f"CHAN-{self.tag}-{bar.bar_index}-{action}-{source_id[-8:]}"
        common = {
            "known_at_bar_index": bar.bar_index,
            "timestamp_utc": bar.timestamp_utc,
            "bar_index": anchor_bar_index,
            "price_i64": price_i64,
            "reason_code": reason,
            "reference_object_id": source_id,
            **(details or {}),
        }
        self.publish(
            "trade_signal",
            signal_id,
            {
                **common,
                "signal_id": signal_id,
                "parent_stage_signal_id": stage_id,
                "side": (
                    "long"
                    if action in {"open_long", "add_long", "reduce_short", "close_short"}
                    else "short"
                ),
                "action": action,
                "quantity": quantity,
            },
            bar.bar_index,
        )
        self.chart_event(
            bar,
            action,
            reason,
            source_id,
            anchor_bar_index=anchor_bar_index,
            price_i64=price_i64,
            details={"quantity": quantity, **(details or {})},
        )

    def result(self, bars: list[StrategyBar]) -> StrategyRun:
        indicator_values = [{"bar_index": bar.bar_index, "ma": None} for bar in bars]
        return StrategyRun(
            bars,
            indicator_values,
            self.states,
            self.stages,
            self.signals,
            self.chart_events,
            self.events,
        )


def _run_third_buy_only(
    payload: dict[str, Any],
    guard: PathGuard,
    cancelled: threading.Event,
    *,
    last_bar_index: int | None,
) -> StrategyRun:
    """Run ALG-STR-003 from confirmed B3 and follow-through objects."""
    parameters = payload["parameters"]
    first_quantity = int(parameters["first_center_quantity"])
    late_quantity = int(parameters["late_center_quantity"])
    minimum_volume = int(parameters["minimum_entry_volume"])
    if not 1 <= first_quantity <= 100 or not 1 <= late_quantity <= 100:
        raise ValueError("third-buy quantities must be between 1 and 100")
    if late_quantity > first_quantity:
        raise ValueError("late_center_quantity must not exceed first_center_quantity")
    if minimum_volume < 0:
        raise ValueError("minimum_entry_volume must not be negative")
    allow_late_center = bool(parameters["allow_late_center"])
    feed = _load_chan_strategy_feed(
        payload,
        guard,
        cancelled,
        last_bar_index=last_bar_index,
        object_types={"segment", "segment_zhongshu", "divergence", "trade_point"},
    )
    output = _CausalStrategyOutput("B3", "waiting_standard_B3")
    active_segments: dict[str, dict[str, Any]] = {}
    active_centers: dict[str, dict[str, Any]] = {}
    active_divergences: dict[str, dict[str, Any]] = {}
    active_points: dict[str, dict[str, Any]] = {}
    consumed_b3_ids: set[str] = set()
    held_center_ids: set[str] = set()
    position_quantity = 0
    source_b3_id: str | None = None
    source_center_id: str | None = None
    source_center_end_bar_index: int | None = None
    source_center_zg_i64: int | None = None
    return_segment_id: str | None = None
    return_end_bar_index: int | None = None
    departure_high_i64: int | None = None
    followthrough_segment_id: str | None = None
    followthrough_end_bar_index: int | None = None

    def standard_point(value: dict[str, Any], *signal_types: str) -> bool:
        return (
            value.get("confirmed") is True
            and value.get("signal_class") == "standard"
            and value.get("signal_type") in signal_types
        )

    def segment_for_point(
        point: dict[str, Any], direction: str
    ) -> tuple[str, dict[str, Any]] | None:
        endpoint = int(point.get("bar_index", -1))
        price = int(point.get("price_i64", 0))
        candidates = [
            (object_id, value)
            for object_id, value in active_segments.items()
            if value.get("confirmed") is True
            and value.get("direction") == direction
            and int(value.get("end_bar_index", -2)) == endpoint
        ]
        exact = [item for item in candidates if int(item[1].get("end_price_i64", 0)) == price]
        values = exact or candidates
        if not values:
            return None
        return max(
            values,
            key=lambda item: (int(item[1].get("start_bar_index", -1)), item[0]),
        )

    def preceding_departure(segment: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
        start = int(segment["start_bar_index"])
        candidates = [
            (object_id, value)
            for object_id, value in active_segments.items()
            if value.get("confirmed") is True
            and value.get("direction") == "up"
            and int(value.get("end_bar_index", -1)) == start
        ]
        if not candidates:
            return None
        return max(
            candidates,
            key=lambda item: (int(item[1].get("start_bar_index", -1)), item[0]),
        )

    def center_ordinal(center_id: str) -> int | None:
        centers = [
            (object_id, value)
            for object_id, value in active_centers.items()
            if value.get("confirmed") is True
            and value.get("analysis_level", "segment") == "segment"
            and all(
                name in value for name in ("start_bar_index", "end_bar_index", "dd_i64", "gg_i64")
            )
        ]
        centers.sort(
            key=lambda item: (
                int(item[1]["start_bar_index"]),
                int(item[1]["end_bar_index"]),
                item[0],
            )
        )
        position = next(
            (index for index, (object_id, _) in enumerate(centers) if object_id == center_id),
            None,
        )
        if position is None:
            return None
        ordinal = 1
        for index in range(1, position + 1):
            previous = centers[index - 1][1]
            current = centers[index][1]
            if (
                previous.get("leave_direction") == "up"
                and current.get("leave_direction") == "up"
                and int(current["dd_i64"]) > int(previous["gg_i64"])
            ):
                ordinal += 1
            else:
                ordinal = 1
        return ordinal

    def first_followthrough() -> tuple[str, dict[str, Any]] | None:
        if return_end_bar_index is None:
            return None
        candidates = [
            (object_id, value)
            for object_id, value in active_segments.items()
            if value.get("confirmed") is True
            and value.get("direction") == "up"
            and int(value.get("start_bar_index", -1)) == return_end_bar_index
            and int(value.get("end_bar_index", -1)) > return_end_bar_index
        ]
        if not candidates:
            return None
        return min(
            candidates,
            key=lambda item: (int(item[1].get("end_bar_index", 2**63 - 1)), item[0]),
        )

    def reset_position_context() -> None:
        nonlocal source_b3_id, source_center_id, source_center_end_bar_index
        nonlocal source_center_zg_i64, return_segment_id, return_end_bar_index
        nonlocal departure_high_i64, followthrough_segment_id, followthrough_end_bar_index
        source_b3_id = None
        source_center_id = None
        source_center_end_bar_index = None
        source_center_zg_i64 = None
        return_segment_id = None
        return_end_bar_index = None
        departure_high_i64 = None
        followthrough_segment_id = None
        followthrough_end_bar_index = None
        held_center_ids.clear()

    def close_position(
        bar: StrategyBar,
        next_state: str,
        reason: str,
        source_id: str,
        *,
        anchor_bar_index: int,
        price_i64: int,
    ) -> None:
        nonlocal position_quantity
        stage_id = output.transition(
            bar,
            next_state,
            reason,
            source_id,
            anchor_bar_index=anchor_bar_index,
            price_i64=price_i64,
        )
        output.trade(
            bar,
            "close_long",
            position_quantity,
            reason,
            source_id,
            anchor_bar_index=anchor_bar_index,
            price_i64=price_i64,
            stage_id=stage_id,
        )
        position_quantity = 0
        reset_position_context()

    for bar in feed.bars:
        if bar.bar_index % 256 == 0 and cancelled.is_set():
            raise InterruptedError("strategy execution cancelled")
        new_segments: list[tuple[str, dict[str, Any]]] = []
        new_centers: list[tuple[str, dict[str, Any]]] = []
        new_divergences: list[tuple[str, dict[str, Any]]] = []
        new_points: list[tuple[str, dict[str, Any]]] = []
        deleted: dict[str, set[str]] = {
            "segment": set(),
            "segment_zhongshu": set(),
            "divergence": set(),
            "trade_point": set(),
        }
        targets = {
            "segment": active_segments,
            "segment_zhongshu": active_centers,
            "divergence": active_divergences,
            "trade_point": active_points,
        }
        new_values = {
            "segment": new_segments,
            "segment_zhongshu": new_centers,
            "divergence": new_divergences,
            "trade_point": new_points,
        }
        for event in feed.events_by_bar.get(bar.bar_index, []):
            if event.operation == "delete":
                targets[event.object_type].pop(event.object_id, None)
                deleted[event.object_type].add(event.object_id)
                continue
            value = json.loads(event.payload_json)
            targets[event.object_type][event.object_id] = value
            new_values[event.object_type].append((event.object_id, value))

        had_position = position_quantity > 0
        exited_this_bar = False
        if had_position:
            revised_source_id: str | None = None
            if source_b3_id is not None and (
                source_b3_id in deleted["trade_point"]
                or any(object_id == source_b3_id for object_id, _ in new_points)
            ):
                revised_source_id = source_b3_id
            elif source_center_id is not None and (
                source_center_id in deleted["segment_zhongshu"]
                or any(object_id == source_center_id for object_id, _ in new_centers)
            ):
                revised_source_id = source_center_id
            elif return_segment_id is not None and (
                return_segment_id in deleted["segment"]
                or any(object_id == return_segment_id for object_id, _ in new_segments)
            ):
                revised_source_id = return_segment_id
            elif followthrough_segment_id is not None and (
                followthrough_segment_id in deleted["segment"]
                or any(object_id == followthrough_segment_id for object_id, _ in new_segments)
            ):
                revised_source_id = followthrough_segment_id
            if revised_source_id is not None:
                close_position(
                    bar,
                    "exited_B3_source_revision",
                    "B3_SOURCE_REVISED",
                    revised_source_id,
                    anchor_bar_index=bar.bar_index,
                    price_i64=bar.close_i64,
                )
                exited_this_bar = True

        s3 = next(
            (
                (object_id, value)
                for object_id, value in new_points
                if standard_point(value, "sell_3")
            ),
            None,
        )
        if position_quantity and s3 is not None:
            object_id, point = s3
            close_position(
                bar,
                "exited_on_S3",
                "STANDARD_S3_INVALIDATED_B3_HOLD",
                object_id,
                anchor_bar_index=int(point.get("bar_index", bar.bar_index)),
                price_i64=int(point.get("price_i64", bar.close_i64)),
            )
            exited_this_bar = True

        if position_quantity and followthrough_end_bar_index is not None:
            entered_center = next(
                (
                    (object_id, value)
                    for object_id, value in new_segments
                    if value.get("confirmed") is True
                    and value.get("direction") == "down"
                    and int(value.get("start_bar_index", -1)) >= followthrough_end_bar_index
                    and source_center_zg_i64 is not None
                    and min(
                        int(value.get("start_price_i64", 0)),
                        int(value.get("end_price_i64", 0)),
                    )
                    <= source_center_zg_i64
                ),
                None,
            )
            if entered_center is not None:
                object_id, segment = entered_center
                close_position(
                    bar,
                    "exited_return_into_source_center",
                    "CONFIRMED_RETURN_ENTERED_SOURCE_CENTER",
                    object_id,
                    anchor_bar_index=int(segment["end_bar_index"]),
                    price_i64=int(segment["end_price_i64"]),
                )
                exited_this_bar = True

        trend_divergence = next(
            (
                (object_id, value)
                for object_id, value in new_divergences
                if value.get("confirmed") is True
                and value.get("signal_type") == "top_divergence"
                and value.get("divergence_kind") == "trend"
                and return_end_bar_index is not None
                and int(value.get("bar_index", -1)) > return_end_bar_index
            ),
            None,
        )
        if position_quantity and trend_divergence is not None:
            object_id, divergence = trend_divergence
            close_position(
                bar,
                "exited_trend_divergence",
                "B3_HOLD_TREND_DIVERGENCE_CONFIRMED",
                object_id,
                anchor_bar_index=int(divergence.get("bar_index", bar.bar_index)),
                price_i64=int(divergence.get("price_i64", bar.close_i64)),
            )
            exited_this_bar = True

        if not position_quantity and not exited_this_bar:
            repeated = [
                (object_id, point)
                for object_id, point in new_points
                if standard_point(point, "buy_3") and object_id in consumed_b3_ids
            ]
            for object_id, point in repeated:
                output.transition(
                    bar,
                    "B3_first_return_already_consumed",
                    "B3_FIRST_RETURN_ALREADY_CONSUMED",
                    object_id,
                    anchor_bar_index=int(point.get("bar_index", bar.bar_index)),
                    price_i64=int(point.get("price_i64", bar.close_i64)),
                )
            observed_entries = [
                (object_id, point)
                for object_id, point in new_points
                if standard_point(point, "buy_3") and object_id not in consumed_b3_ids
            ]
            resolved_entries: list[
                tuple[
                    int,
                    str,
                    dict[str, Any],
                    dict[str, Any],
                    tuple[str, dict[str, Any]],
                    tuple[str, dict[str, Any]],
                ]
            ] = []
            for object_id, point in observed_entries:
                consumed_b3_ids.add(object_id)
                center_id = str(point.get("reference_object_id") or "")
                center = active_centers.get(center_id)
                returning = segment_for_point(point, "down")
                departure = None if returning is None else preceding_departure(returning[1])
                ordinal = center_ordinal(center_id)
                valid_center = (
                    center is not None
                    and center.get("confirmed") is True
                    and center.get("analysis_level", "segment") == "segment"
                    and center.get("leave_direction") == "up"
                    and "zg_i64" in center
                    and "end_bar_index" in center
                )
                valid_return = (
                    returning is not None
                    and center is not None
                    and min(
                        int(returning[1]["start_price_i64"]),
                        int(returning[1]["end_price_i64"]),
                    )
                    >= int(center["zg_i64"])
                )
                if not valid_center or not valid_return or departure is None or ordinal is None:
                    output.transition(
                        bar,
                        "B3_dependency_filtered",
                        "B3_CONFIRMED_OBJECT_CHAIN_MISSING",
                        object_id,
                        anchor_bar_index=int(point.get("bar_index", bar.bar_index)),
                        price_i64=int(point.get("price_i64", bar.close_i64)),
                    )
                    continue
                assert center is not None and returning is not None
                resolved_entries.append((ordinal, object_id, point, center, returning, departure))
            resolved_entries.sort(key=lambda item: (item[0], item[1]))
            if resolved_entries:
                ordinal, object_id, point, center, returning, departure = resolved_entries[0]
                for _, skipped_id, skipped_point, _, _, _ in resolved_entries[1:]:
                    output.transition(
                        bar,
                        "B3_concurrent_lower_priority_filtered",
                        "B3_CONCURRENT_LOWER_PRIORITY_FILTERED",
                        skipped_id,
                        anchor_bar_index=int(skipped_point.get("bar_index", bar.bar_index)),
                        price_i64=int(skipped_point.get("price_i64", bar.close_i64)),
                    )
                entry_volume = feed.volume_by_bar.get(bar.bar_index)
                risk_reason = ""
                if s3 is not None:
                    risk_reason = "B3_RISK_FILTER_BLOCKED_BY_S3"
                elif trend_divergence is not None:
                    risk_reason = "B3_RISK_FILTER_BLOCKED_BY_TREND_DIVERGENCE"
                elif ordinal > 1 and not allow_late_center:
                    risk_reason = "LATE_CENTER_B3_DISABLED"
                elif minimum_volume > 0 and (entry_volume is None or entry_volume < minimum_volume):
                    risk_reason = "B3_LIQUIDITY_FILTER_BLOCKED"
                endpoint = int(point.get("bar_index", bar.bar_index))
                point_price = int(point.get("price_i64", bar.close_i64))
                priority = "high" if ordinal == 1 else "penalized"
                if risk_reason:
                    output.transition(
                        bar,
                        "B3_risk_filtered",
                        risk_reason,
                        object_id,
                        anchor_bar_index=endpoint,
                        price_i64=point_price,
                        details={
                            "center_ordinal_in_trend": ordinal,
                            "priority": priority,
                            "entry_volume": entry_volume,
                            "minimum_entry_volume": minimum_volume,
                        },
                    )
                else:
                    quantity = first_quantity if ordinal == 1 else late_quantity
                    reason = (
                        "CONFIRMED_FIRST_CENTER_B3_ENTRY"
                        if ordinal == 1
                        else "CONFIRMED_LATE_CENTER_B3_REDUCED_ENTRY"
                    )
                    source_b3_id = object_id
                    source_center_id = str(point["reference_object_id"])
                    source_center_end_bar_index = int(center["end_bar_index"])
                    source_center_zg_i64 = int(center["zg_i64"])
                    return_segment_id = returning[0]
                    return_end_bar_index = int(returning[1]["end_bar_index"])
                    departure_high_i64 = max(
                        int(departure[1]["start_price_i64"]),
                        int(departure[1]["end_price_i64"]),
                    )
                    position_quantity = quantity
                    stage_id = output.transition(
                        bar,
                        "long_after_first_center_B3"
                        if ordinal == 1
                        else "long_after_late_center_B3",
                        reason,
                        object_id,
                        anchor_bar_index=endpoint,
                        price_i64=point_price,
                        details={
                            "center_ordinal_in_trend": ordinal,
                            "priority": priority,
                            "quantity": quantity,
                            "source_center_id": source_center_id,
                            "return_segment_id": return_segment_id,
                            "entry_volume": entry_volume,
                        },
                    )
                    output.trade(
                        bar,
                        "open_long",
                        quantity,
                        reason,
                        object_id,
                        anchor_bar_index=endpoint,
                        price_i64=point_price,
                        stage_id=stage_id,
                        details={
                            "center_ordinal_in_trend": ordinal,
                            "priority": priority,
                        },
                    )

        if position_quantity and followthrough_segment_id is None:
            followthrough = first_followthrough()
            if followthrough is not None:
                followthrough_segment_id, segment = followthrough
                endpoint = int(segment["end_bar_index"])
                endpoint_price = int(segment["end_price_i64"])
                followthrough_end_bar_index = endpoint
                made_new_high = (
                    departure_high_i64 is not None
                    and max(int(segment["start_price_i64"]), endpoint_price) > departure_high_i64
                )
                followthrough_divergence = next(
                    (
                        (object_id, value)
                        for object_id, value in active_divergences.items()
                        if value.get("confirmed") is True
                        and value.get("signal_type") == "top_divergence"
                        and int(value.get("bar_index", -1)) == endpoint
                    ),
                    None,
                )
                if not made_new_high:
                    close_position(
                        bar,
                        "exited_B3_followthrough_failure",
                        "B3_FOLLOWTHROUGH_FAILED_NEW_HIGH",
                        followthrough_segment_id,
                        anchor_bar_index=endpoint,
                        price_i64=endpoint_price,
                    )
                elif followthrough_divergence is not None:
                    divergence_id, value = followthrough_divergence
                    reason = (
                        "B3_FOLLOWTHROUGH_CONSOLIDATION_DIVERGENCE"
                        if value.get("divergence_kind") == "consolidation"
                        else "B3_FOLLOWTHROUGH_TREND_DIVERGENCE"
                    )
                    close_position(
                        bar,
                        "exited_B3_followthrough_divergence",
                        reason,
                        divergence_id,
                        anchor_bar_index=endpoint,
                        price_i64=endpoint_price,
                    )
                else:
                    output.transition(
                        bar,
                        "holding_after_B3_followthrough",
                        "B3_FOLLOWTHROUGH_NEW_HIGH_CONFIRMED",
                        followthrough_segment_id,
                        anchor_bar_index=endpoint,
                        price_i64=endpoint_price,
                    )
                    output.chart_event(
                        bar,
                        "hold_after_B3",
                        "B3_FOLLOWTHROUGH_NEW_HIGH_CONFIRMED",
                        followthrough_segment_id,
                        anchor_bar_index=endpoint,
                        price_i64=endpoint_price,
                        details={"source_center_id": source_center_id},
                    )

        if position_quantity and followthrough_end_bar_index is not None:
            later_centers = [
                (object_id, center)
                for object_id, center in new_centers
                if object_id != source_center_id
                and object_id not in held_center_ids
                and center.get("confirmed") is True
                and center.get("analysis_level", "segment") == "segment"
                and int(center.get("end_bar_index", -1)) > followthrough_end_bar_index
                and (
                    source_center_end_bar_index is None
                    or int(center.get("start_bar_index", -1)) > source_center_end_bar_index
                )
            ]
            later_centers.sort(
                key=lambda item: (
                    int(item[1].get("start_bar_index", -1)),
                    int(item[1].get("end_bar_index", -1)),
                    item[0],
                )
            )
            for object_id, center in later_centers:
                held_center_ids.add(object_id)
                price = (int(center["zd_i64"]) + int(center["zg_i64"])) // 2
                output.transition(
                    bar,
                    "holding_new_center_without_trend_divergence",
                    "NEW_CENTER_WITHOUT_TREND_DIVERGENCE_HOLD",
                    object_id,
                    anchor_bar_index=int(center["end_bar_index"]),
                    price_i64=price,
                )
                output.chart_event(
                    bar,
                    "hold_new_center",
                    "NEW_CENTER_WITHOUT_TREND_DIVERGENCE_HOLD",
                    object_id,
                    anchor_bar_index=int(center["end_bar_index"]),
                    price_i64=price,
                )

    return output.result(feed.bars)


def _load_auxiliary_bars(
    dataset: dict[str, Any],
    guard: PathGuard,
    cancelled: threading.Event,
    *,
    last_bar_index: int | None,
    cancel_message: str,
) -> list[StrategyBar]:
    path = guard.resolve(str(dataset["bars_path"]))
    table = pq.read_table(
        path,
        columns=[
            "bar_index",
            "timestamp_utc",
            "open_i64",
            "high_i64",
            "low_i64",
            "close_i64",
        ],
    ).to_pydict()
    bars: list[StrategyBar] = []
    for position, value in enumerate(table["bar_index"]):
        bar_index = int(value)
        if last_bar_index is not None and bar_index > last_bar_index:
            break
        if position % 4096 == 0 and cancelled.is_set():
            raise InterruptedError(cancel_message)
        bars.append(
            StrategyBar(
                bar_index=bar_index,
                timestamp_utc=int(table["timestamp_utc"][position]),
                open_i64=int(table["open_i64"][position]),
                high_i64=int(table["high_i64"][position]),
                low_i64=int(table["low_i64"][position]),
                close_i64=int(table["close_i64"][position]),
            )
        )
    return bars


@dataclass(frozen=True)
class _Daily30mFeed:
    strategy_bars: list[StrategyBar]
    profile_bars: list[Daily30mBar]


def _load_daily_30m_feed(
    dataset: dict[str, Any],
    guard: PathGuard,
    cancelled: threading.Event,
    *,
    last_bar_index: int | None,
) -> _Daily30mFeed:
    path = guard.resolve(str(dataset["bars_path"]))
    table = pq.read_table(
        path,
        columns=[
            "bar_index",
            "timestamp_utc",
            "trading_day",
            "source_hhmm",
            "open_i64",
            "high_i64",
            "low_i64",
            "close_i64",
        ],
    ).to_pydict()
    strategy_bars: list[StrategyBar] = []
    profile_bars: list[Daily30mBar] = []
    for position, value in enumerate(table["bar_index"]):
        bar_index = int(value)
        if last_bar_index is not None and bar_index > last_bar_index:
            break
        if position % 4096 == 0 and cancelled.is_set():
            raise InterruptedError("daily 8x30m classification cancelled")
        timestamp_utc = int(table["timestamp_utc"][position])
        open_i64 = int(table["open_i64"][position])
        high_i64 = int(table["high_i64"][position])
        low_i64 = int(table["low_i64"][position])
        close_i64 = int(table["close_i64"][position])
        trading_day_value = table["trading_day"][position]
        source_hhmm_value = table["source_hhmm"][position]
        if trading_day_value is None or source_hhmm_value is None:
            raise ValueError("daily 8x30m profile requires trading_day and source_hhmm")
        strategy_bars.append(
            StrategyBar(
                bar_index=bar_index,
                timestamp_utc=timestamp_utc,
                open_i64=open_i64,
                high_i64=high_i64,
                low_i64=low_i64,
                close_i64=close_i64,
            )
        )
        profile_bars.append(
            Daily30mBar(
                bar_index=bar_index,
                timestamp_utc=timestamp_utc,
                trading_day=str(trading_day_value),
                source_hhmm=int(source_hhmm_value),
                open_i64=open_i64,
                high_i64=high_i64,
                low_i64=low_i64,
                close_i64=close_i64,
            )
        )
    return _Daily30mFeed(strategy_bars, profile_bars)


def _run_auxiliary_ma_kiss(
    payload: dict[str, Any],
    guard: PathGuard,
    cancelled: threading.Event,
    *,
    last_bar_index: int | None,
) -> StrategyRun:
    """Run ALG-AUX-001 through the causal event adapter, without trade signals."""
    dataset = payload["dataset"]
    parameters = payload["parameters"]
    meta = json.loads(guard.resolve(str(dataset["meta_path"])).read_text(encoding="utf-8"))
    tick_size_i64 = int(meta["price"].get("tick_size_i64") or 1)
    config = MaKissConfig.from_parameters(parameters, tick_size_i64)
    bars = _load_auxiliary_bars(
        dataset,
        guard,
        cancelled,
        last_bar_index=last_bar_index,
        cancel_message="auxiliary MA kiss execution cancelled",
    )
    series = compute_ma_kiss_series([bar.close_i64 for bar in bars], config)
    classified = classify_ma_kisses(bars, series, config)
    if cancelled.is_set():
        raise InterruptedError("auxiliary MA kiss execution cancelled")
    output = _CausalStrategyOutput("AUX-MA", "AUX_WAIT_MA_REGIME")
    for event in classified:
        output.publish(
            "chart_event",
            event.event_id,
            {
                "event_id": event.event_id,
                "known_at_bar_index": event.known_at_bar_index,
                "timestamp_utc": event.timestamp_utc,
                "bar_index": event.bar_index,
                "price_i64": event.price_i64,
                "event_type": event.event_type,
                "reason_code": event.reason_code,
                "reference_object_id": event.reference_object_id,
                **event.details,
            },
            event.known_at_bar_index,
        )
    result = output.result(bars)
    result.indicator_values = [
        {"bar_index": bar.bar_index, "ma": series.short_ma[position]}
        for position, bar in enumerate(bars)
    ]
    return result


def _run_auxiliary_price_gap(
    payload: dict[str, Any],
    guard: PathGuard,
    cancelled: threading.Event,
    *,
    last_bar_index: int | None,
) -> StrategyRun:
    dataset = payload["dataset"]
    bars = _load_auxiliary_bars(
        dataset,
        guard,
        cancelled,
        last_bar_index=last_bar_index,
        cancel_message="auxiliary price gap execution cancelled",
    )
    classified = classify_price_gaps(bars)
    output = _CausalStrategyOutput("AUX-GAP", "AUX_GAP_OBSERVING")
    for event in classified:
        output.publish(
            "chart_event",
            event.event_id,
            {
                "event_id": event.event_id,
                "event_type": event.event_type,
                "bar_index": event.bar_index,
                "timestamp_utc": event.timestamp_utc,
                "known_at_bar_index": event.known_at_bar_index,
                "price_i64": event.price_i64,
                "reason_code": event.reason_code,
                "reference_object_id": event.gap_id,
                **event.details(),
            },
            event.known_at_bar_index,
        )
    result = output.result(bars)
    result.indicator_values = [{"bar_index": bar.bar_index, "ma": None} for bar in bars]
    return result


def _run_auxiliary_single_instrument_ma(
    payload: dict[str, Any],
    guard: PathGuard,
    cancelled: threading.Event,
    *,
    last_bar_index: int | None,
) -> StrategyRun:
    dataset = payload["dataset"]
    config = SingleMaConfig.from_parameters(payload["parameters"])
    bars = _load_auxiliary_bars(
        dataset,
        guard,
        cancelled,
        last_bar_index=last_bar_index,
        cancel_message="single-instrument MA observation cancelled",
    )
    ladder = compute_ma_ladder([bar.close_i64 for bar in bars], config.ma_periods)
    classified = classify_single_instrument_ma(bars, ladder, config)
    output = _CausalStrategyOutput("AUX-MA-SINGLE", "AUX_MA_OBSERVING")
    for event in classified:
        output.publish(
            "chart_event",
            event.event_id,
            {
                "event_id": event.event_id,
                "event_type": event.event_type,
                "bar_index": event.bar_index,
                "timestamp_utc": event.timestamp_utc,
                "known_at_bar_index": event.known_at_bar_index,
                "price_i64": event.price_i64,
                "reason_code": event.event_type.upper(),
                "reference_object_id": None,
                **event.details,
            },
            event.known_at_bar_index,
        )
    result = output.result(bars)
    result.indicator_values = [
        {"bar_index": bar.bar_index, "ma": ladder[0][position]} for position, bar in enumerate(bars)
    ]
    return result


def _run_auxiliary_macd_zero_axis(
    payload: dict[str, Any],
    guard: PathGuard,
    cancelled: threading.Event,
    *,
    last_bar_index: int | None,
) -> StrategyRun:
    """Run ALG-AUX-002 as a visible risk gate with no executable signals."""
    dataset = payload["dataset"]
    parameters = payload["parameters"]
    meta = json.loads(guard.resolve(str(dataset["meta_path"])).read_text(encoding="utf-8"))
    tick_size_i64 = int(meta["price"].get("tick_size_i64") or 1)
    dataset_timeframe = str(meta.get("timeframe") or "")
    config = MacdZeroAxisConfig.from_parameters(parameters, tick_size_i64, dataset_timeframe)
    bars = _load_auxiliary_bars(
        dataset,
        guard,
        cancelled,
        last_bar_index=last_bar_index,
        cancel_message="auxiliary MACD zero-axis execution cancelled",
    )
    series = compute_macd_zero_axis_series([bar.close_i64 for bar in bars], config)
    classified = classify_macd_zero_axis(bars, series, config)
    if cancelled.is_set():
        raise InterruptedError("auxiliary MACD zero-axis execution cancelled")
    output = _CausalStrategyOutput("AUX-MACD", "AUX_MACD_RISK_ON")
    for event in classified:
        output.publish(
            "chart_event",
            event.event_id,
            {
                "event_id": event.event_id,
                "known_at_bar_index": event.known_at_bar_index,
                "timestamp_utc": event.timestamp_utc,
                "bar_index": event.bar_index,
                "price_i64": event.price_i64,
                "event_type": event.event_type,
                "reason_code": event.reason_code,
                "reference_object_id": None,
                **event.details,
            },
            event.known_at_bar_index,
        )
    result = output.result(bars)
    result.indicator_values = [{"bar_index": bar.bar_index, "ma": None} for bar in bars]
    return result


def _run_auxiliary_boll_bardo(
    payload: dict[str, Any],
    guard: PathGuard,
    cancelled: threading.Event,
    *,
    last_bar_index: int | None,
) -> StrategyRun:
    """Run ALG-AUX-003 from registered BOLL and confirmed preceding Chan facts."""
    dataset = payload["dataset"]
    parameters = payload["parameters"]
    meta = json.loads(guard.resolve(str(dataset["meta_path"])).read_text(encoding="utf-8"))
    tick_size_i64 = int(meta["price"].get("tick_size_i64") or 1)
    dataset_timeframe = str(meta.get("timeframe") or "")
    config = BollBardoConfig.from_parameters(parameters, tick_size_i64, dataset_timeframe)
    feed = _load_chan_strategy_feed(
        payload,
        guard,
        cancelled,
        last_bar_index=last_bar_index,
        object_types={"segment_zhongshu", "movement_state", "divergence", "trade_point"},
    )
    structural_events = [event for values in feed.events_by_bar.values() for event in values]
    contexts = derive_bardo_contexts(feed.bars, structural_events)
    series = compute_boll_series([bar.close_i64 for bar in feed.bars], config)
    classified = classify_boll_bardo(feed.bars, series, contexts, config)
    if cancelled.is_set():
        raise InterruptedError("auxiliary BOLL BARDO execution cancelled")
    output = _CausalStrategyOutput("AUX-BOLL", "AUX_BOLL_OBSERVING")
    for event in classified:
        output.publish(
            "chart_event",
            event.event_id,
            {
                "event_id": event.event_id,
                "known_at_bar_index": event.known_at_bar_index,
                "timestamp_utc": event.timestamp_utc,
                "bar_index": event.bar_index,
                "price_i64": event.price_i64,
                "event_type": event.event_type,
                "reason_code": event.reason_code,
                "reference_object_id": event.reference_object_id,
                **event.details,
            },
            event.known_at_bar_index,
        )
    result = output.result(feed.bars)
    result.indicator_values = [
        {"bar_index": bar.bar_index, "ma": series.middle[position]}
        for position, bar in enumerate(feed.bars)
    ]
    return result


def _run_auxiliary_daily_30m(
    payload: dict[str, Any],
    guard: PathGuard,
    cancelled: threading.Event,
    *,
    last_bar_index: int | None,
) -> StrategyRun:
    """Run ALG-AUX-005 only for the exact course 8x30-minute market profile."""
    dataset = payload["dataset"]
    parameters = payload["parameters"]
    meta = json.loads(guard.resolve(str(dataset["meta_path"])).read_text(encoding="utf-8"))
    config = Daily30mConfig.from_parameters(
        parameters,
        dataset_timeframe=str(meta.get("timeframe") or ""),
        timestamp_semantics=str(meta.get("source", {}).get("timestamp_semantics") or ""),
        date_semantics=str(meta.get("time", {}).get("date_semantics") or ""),
        timezone=str(meta.get("time", {}).get("timezone") or ""),
    )
    feed = _load_daily_30m_feed(
        dataset,
        guard,
        cancelled,
        last_bar_index=last_bar_index,
    )
    classified = classify_daily_30m_sessions(feed.profile_bars, config)
    if cancelled.is_set():
        raise InterruptedError("daily 8x30m classification cancelled")
    output = _CausalStrategyOutput("AUX-DAILY30M", "HEURISTIC_DAILY_PROFILE_OBSERVING")
    for event in classified:
        if event.operation == "delete":
            output.delete("chart_event", event.event_id, event.known_at_bar_index)
            continue
        output.publish(
            "chart_event",
            event.event_id,
            {
                "event_id": event.event_id,
                "known_at_bar_index": event.known_at_bar_index,
                "timestamp_utc": event.timestamp_utc,
                "bar_index": event.bar_index,
                "price_i64": event.price_i64,
                "event_type": event.event_type,
                "reason_code": event.reason_code,
                "reference_object_id": None,
                **event.details,
            },
            event.known_at_bar_index,
        )
    return output.result(feed.strategy_bars)


def _run_auxiliary_ma_sector_rotation(
    payload: dict[str, Any],
    guard: PathGuard,
    cancelled: threading.Event,
    *,
    last_bar_index: int | None,
) -> StrategyRun:
    """Run ALG-AUX-006 over explicit adjusted daily datasets and point-in-time membership."""
    dataset = payload["dataset"]
    parameters = payload["parameters"]
    raw_context = payload.get("ranking_context")
    raw_datasets = payload.get("ranking_datasets")
    if not isinstance(raw_context, dict) or not isinstance(raw_datasets, list):
        raise ValueError("ranking_context and ranking_datasets are required")
    context = RankingContext.from_payload(raw_context)
    config = MaSectorRotationConfig.from_parameters(parameters)
    refs: dict[str, dict[str, Any]] = {}
    for raw_ref in raw_datasets:
        if not isinstance(raw_ref, dict):
            raise ValueError("ranking dataset references must be objects")
        dataset_id = str(raw_ref.get("dataset_id") or "")
        if not dataset_id or dataset_id in refs:
            raise ValueError("ranking dataset references must have unique dataset_id")
        refs[dataset_id] = raw_ref
    membership_ids = {membership.dataset_id for membership in context.memberships}
    if set(refs) != membership_ids or str(dataset.get("dataset_id")) not in refs:
        raise ValueError("ranking dataset references must exactly match point-in-time memberships")

    for membership in context.memberships:
        ref = refs[membership.dataset_id]
        if ref.get("data_revision") != membership.data_revision:
            raise ValueError("ranking dataset reference revision does not match membership")
        meta = json.loads(guard.resolve(str(ref["meta_path"])).read_text(encoding="utf-8"))
        if (
            meta.get("dataset_id") != membership.dataset_id
            or meta.get("data_revision") != membership.data_revision
        ):
            raise ValueError("ranking dataset metadata identity does not match reference")
        if (
            meta.get("timeframe") != "1d"
            or meta.get("source", {}).get("timestamp_semantics") != "bar_end"
            or meta.get("time", {}).get("date_semantics") != "trading_day"
            or meta.get("time", {}).get("timezone") != "Asia/Shanghai"
        ):
            raise ValueError(
                "ranking datasets require 1d, bar_end, trading_day and Asia/Shanghai metadata"
            )

    anchor_id = str(dataset["dataset_id"])
    anchor_ref = refs[anchor_id]
    anchor_table = _read_ranking_table(anchor_ref, guard, last_bar_index=last_bar_index)
    if not anchor_table["bar_index"]:
        raise ValueError("ranking anchor dataset has no bars")
    end_timestamp = int(anchor_table["timestamp_utc"][-1])
    instruments: list[RankingInstrument] = []
    strategy_bars: list[StrategyBar] = []
    bars_by_dataset_and_index: dict[str, dict[int, RankingBar]] = {}
    for dataset_id in sorted(refs):
        ref = refs[dataset_id]
        table = (
            anchor_table
            if dataset_id == anchor_id
            else _read_ranking_table(ref, guard, last_timestamp_utc=end_timestamp)
        )
        ranking_bars = tuple(
            RankingBar(
                bar_index=int(bar_index),
                timestamp_utc=int(table["timestamp_utc"][position]),
                close_i64=int(table["close_i64"][position]),
                volume=(
                    None if table["volume"][position] is None else int(table["volume"][position])
                ),
            )
            for position, bar_index in enumerate(table["bar_index"])
        )
        instruments.append(RankingInstrument(dataset_id, str(ref["data_revision"]), ranking_bars))
        bars_by_dataset_and_index[dataset_id] = {bar.bar_index: bar for bar in ranking_bars}
        if dataset_id == anchor_id:
            strategy_bars = [
                StrategyBar(
                    int(bar_index),
                    int(table["timestamp_utc"][position]),
                    int(table["open_i64"][position]),
                    int(table["high_i64"][position]),
                    int(table["low_i64"][position]),
                    int(table["close_i64"][position]),
                )
                for position, bar_index in enumerate(table["bar_index"])
            ]
        if cancelled.is_set():
            raise InterruptedError("MA sector rotation loading cancelled")

    divergence_updates: list[DivergenceUpdate] = []
    for instrument in instruments:
        ref = refs[instrument.dataset_id]
        last_member_bar_index = instrument.bars[-1].bar_index if instrument.bars else None
        if last_member_bar_index is None:
            continue
        chan = chan_definition()
        chan_payload = {
            "dataset": ref,
            "algorithm": {
                key: chan[key]
                for key in ("kind", "algorithm_id", "algorithm_version", "source_hash")
            },
            "parameters": {"checkpoint_interval": config.checkpoint_interval},
        }
        runtime, _, _ = _run_strategy_chan(
            payload,
            chan_payload,
            guard,
            cancelled,
            last_bar_index=last_member_bar_index,
        )
        indexed_bars = bars_by_dataset_and_index[instrument.dataset_id]
        for event in runtime.emitter.events:
            if event.object_type != "divergence":
                continue
            known_bar = indexed_bars.get(event.known_at_bar_index)
            if known_bar is None:
                continue
            value = json.loads(event.payload_json) if event.operation == "upsert" else {}
            operation: EventOperation = "delete" if event.operation == "delete" else "upsert"
            divergence_updates.append(
                DivergenceUpdate(
                    dataset_id=instrument.dataset_id,
                    object_id=event.object_id,
                    operation=operation,
                    known_timestamp_utc=known_bar.timestamp_utc,
                    signal_type=value.get("signal_type"),
                    divergence_kind=value.get("divergence_kind"),
                    source_bar_index=value.get("bar_index"),
                    source_timestamp_utc=value.get("time"),
                    source_price_i64=value.get("price_i64"),
                )
            )
        if cancelled.is_set():
            raise InterruptedError("MA sector rotation Chan dependency cancelled")

    classified = classify_ma_sector_rotation(
        anchor_dataset_id=anchor_id,
        instruments=instruments,
        context=context,
        config=config,
        divergence_updates=divergence_updates,
    )
    output = _CausalStrategyOutput("AUX-MA-SECTOR", "HEURISTIC_RANKING_OBSERVING")
    for ranking_event in classified:
        if ranking_event.operation == "delete":
            output.delete("chart_event", ranking_event.event_id, ranking_event.known_at_bar_index)
            continue
        output.publish(
            "chart_event",
            ranking_event.event_id,
            {
                "event_id": ranking_event.event_id,
                "known_at_bar_index": ranking_event.known_at_bar_index,
                "timestamp_utc": ranking_event.timestamp_utc,
                "bar_index": ranking_event.bar_index,
                "price_i64": ranking_event.price_i64,
                "event_type": ranking_event.event_type,
                "reason_code": ranking_event.reason_code,
                "reference_object_id": ranking_event.details.get("source_divergence_object_id"),
                **ranking_event.details,
            },
            ranking_event.known_at_bar_index,
        )
    return output.result(strategy_bars)


def _read_ranking_table(
    dataset: dict[str, Any],
    guard: PathGuard,
    *,
    last_bar_index: int | None = None,
    last_timestamp_utc: int | None = None,
) -> dict[str, list[Any]]:
    path = guard.resolve(str(dataset["bars_path"]))
    schema_names = pq.read_schema(path).names
    if "volume" not in schema_names:
        raise ValueError("ranking datasets require volume for capacity checks")
    columns = [
        "bar_index",
        "timestamp_utc",
        "open_i64",
        "high_i64",
        "low_i64",
        "close_i64",
        "volume",
    ]
    source = pq.read_table(path, columns=columns).to_pydict()
    positions = [
        position
        for position, bar_index in enumerate(source["bar_index"])
        if (last_bar_index is None or int(bar_index) <= last_bar_index)
        and (
            last_timestamp_utc is None
            or int(source["timestamp_utc"][position]) <= last_timestamp_utc
        )
    ]
    return {name: [values[position] for position in positions] for name, values in source.items()}


def _run_centre_oscillation_spread(
    payload: dict[str, Any],
    guard: PathGuard,
    cancelled: threading.Event,
    *,
    last_bar_index: int | None,
) -> StrategyRun:
    """Run ALG-STR-004 from confirmed center, divergence, Zn and third-point events."""
    parameters = payload["parameters"]

    def integer_parameter(name: str, minimum: int, maximum: int) -> int:
        raw = parameters[name]
        if isinstance(raw, bool) or not isinstance(raw, int) or not minimum <= raw <= maximum:
            raise ValueError(f"{name} must be an integer between {minimum} and {maximum}")
        return raw

    strong_quantity = integer_parameter("strong_quantity", 1, 100)
    neutral_quantity = integer_parameter("neutral_quantity", 1, 100)
    weak_quantity = integer_parameter("weak_quantity", 1, 100)
    if not weak_quantity <= neutral_quantity <= strong_quantity:
        raise ValueError("oscillation quantities must satisfy weak <= neutral <= strong")
    estimated_cost = integer_parameter(
        "estimated_round_trip_cost_i64", 0, 9_223_372_036_854_775_807
    )
    minimum_net_range = integer_parameter("minimum_net_range_i64", 0, 9_223_372_036_854_775_807)
    max_entries = integer_parameter("max_entries_per_center", 1, 100)
    allow_long = parameters["allow_long"]
    allow_short = parameters["allow_short"]
    fast_execution = parameters["fast_execution_available"]
    if not all(isinstance(value, bool) for value in (allow_long, allow_short, fast_execution)):
        raise ValueError("oscillation boolean parameters must be booleans")

    feed = _load_chan_strategy_feed(
        payload,
        guard,
        cancelled,
        last_bar_index=last_bar_index,
        object_types={"segment_zhongshu", "center_monitor", "divergence", "trade_point"},
    )
    output = _CausalStrategyOutput("OSC", "waiting_active_center")
    centers: dict[str, dict[str, Any]] = {}
    monitors: dict[str, dict[str, Any]] = {}
    divergences: dict[str, dict[str, Any]] = {}
    points: dict[str, dict[str, Any]] = {}
    terminated_center_ids: set[str] = set()
    consumed_divergence_ids: set[str] = set()
    pending_divergence_ids: set[str] = set()
    entries_by_center: dict[str, int] = {}
    current_center_id: str | None = None
    current_center_core: tuple[int, int] | None = None
    position_side = "flat"
    position_quantity = 0
    source_divergence_id: str | None = None
    source_monitor_id: str | None = None

    def center_core(value: dict[str, Any]) -> tuple[int, int]:
        return int(value["zd_i64"]), int(value["zg_i64"])

    def is_active_center(object_id: str, value: dict[str, Any]) -> bool:
        return (
            object_id not in terminated_center_ids
            and value.get("confirmed") is True
            and value.get("analysis_level", "segment") == "segment"
            and value.get("status", "confirmed") in {"confirmed", "extended"}
            and int(value.get("component_count", 3)) < 9
            and all(
                name in value for name in ("start_bar_index", "end_bar_index", "zd_i64", "zg_i64")
            )
        )

    def latest_active_center() -> tuple[str, dict[str, Any]] | None:
        values = [
            (object_id, value)
            for object_id, value in centers.items()
            if is_active_center(object_id, value)
        ]
        if not values:
            return None
        return max(
            values,
            key=lambda item: (
                int(item[1]["start_bar_index"]),
                int(item[1]["end_bar_index"]),
                item[0],
            ),
        )

    def activate_center(bar: StrategyBar, object_id: str, value: dict[str, Any]) -> None:
        nonlocal current_center_id, current_center_core
        current_center_id = object_id
        current_center_core = center_core(value)
        zd_i64, zg_i64 = current_center_core
        output.transition(
            bar,
            "oscillation_ready",
            "CONFIRMED_ACTIVE_CENTER_READY",
            object_id,
            anchor_bar_index=int(value["end_bar_index"]),
            price_i64=(zd_i64 + zg_i64) // 2,
            details={
                "zd_i64": zd_i64,
                "zg_i64": zg_i64,
                "z_i64": int(value.get("z_i64", (zd_i64 + zg_i64) // 2)),
                "component_count": int(value.get("component_count", 3)),
            },
        )

    def clear_position_context() -> None:
        nonlocal source_divergence_id, source_monitor_id
        source_divergence_id = None
        source_monitor_id = None

    def stop_oscillation(
        bar: StrategyBar,
        next_state: str,
        reason: str,
        source_id: str,
        *,
        anchor_bar_index: int,
        price_i64: int,
        handoff_direction: str | None = None,
    ) -> None:
        nonlocal current_center_id, current_center_core, position_side, position_quantity
        center_id = current_center_id
        stage_id = output.transition(
            bar,
            next_state,
            reason,
            source_id,
            anchor_bar_index=anchor_bar_index,
            price_i64=price_i64,
            details={"source_center_id": center_id},
        )
        if position_side != "flat":
            output.trade(
                bar,
                "close_long" if position_side == "long" else "close_short",
                position_quantity,
                reason,
                source_id,
                anchor_bar_index=anchor_bar_index,
                price_i64=price_i64,
                stage_id=stage_id,
                details={"source_center_id": center_id},
            )
        output.chart_event(
            bar,
            "stop_oscillation",
            reason,
            source_id,
            anchor_bar_index=anchor_bar_index,
            price_i64=price_i64,
            details={"source_center_id": center_id},
        )
        if handoff_direction is not None:
            output.chart_event(
                bar,
                "handoff_to_trend",
                "CONFIRMED_THIRD_POINT_TREND_HANDOFF",
                source_id,
                anchor_bar_index=anchor_bar_index,
                price_i64=price_i64,
                details={
                    "source_center_id": center_id,
                    "handoff_direction": handoff_direction,
                },
            )
        if center_id is not None:
            terminated_center_ids.add(center_id)
        current_center_id = None
        current_center_core = None
        position_side = "flat"
        position_quantity = 0
        clear_position_context()

    for bar in feed.bars:
        if bar.bar_index % 256 == 0 and cancelled.is_set():
            raise InterruptedError("strategy execution cancelled")
        new_centers: list[tuple[str, dict[str, Any]]] = []
        new_monitors: list[tuple[str, dict[str, Any]]] = []
        new_divergences: list[tuple[str, dict[str, Any]]] = []
        new_points: list[tuple[str, dict[str, Any]]] = []
        deleted: dict[str, set[str]] = {
            "segment_zhongshu": set(),
            "center_monitor": set(),
            "divergence": set(),
            "trade_point": set(),
        }
        revised_center_cores: set[str] = set()
        targets = {
            "segment_zhongshu": centers,
            "center_monitor": monitors,
            "divergence": divergences,
            "trade_point": points,
        }
        new_values = {
            "segment_zhongshu": new_centers,
            "center_monitor": new_monitors,
            "divergence": new_divergences,
            "trade_point": new_points,
        }
        for event in feed.events_by_bar.get(bar.bar_index, []):
            target = targets[event.object_type]
            if event.operation == "delete":
                target.pop(event.object_id, None)
                deleted[event.object_type].add(event.object_id)
                pending_divergence_ids.discard(event.object_id)
                continue
            value = json.loads(event.payload_json)
            previous = target.get(event.object_id)
            if (
                event.object_type == "segment_zhongshu"
                and previous is not None
                and all(name in previous and name in value for name in ("zd_i64", "zg_i64"))
                and center_core(previous) != center_core(value)
            ):
                revised_center_cores.add(event.object_id)
            target[event.object_id] = value
            new_values[event.object_type].append((event.object_id, value))

        exited_this_bar = False
        if current_center_id is None:
            candidate = latest_active_center()
            if candidate is not None:
                activate_center(bar, *candidate)

        if current_center_id is not None and position_side != "flat":
            revised_source = next(
                (
                    source_id
                    for source_id, object_type, values in (
                        (source_divergence_id, "divergence", new_divergences),
                        (source_monitor_id, "center_monitor", new_monitors),
                    )
                    if source_id is not None
                    and (
                        source_id in deleted[object_type]
                        or any(object_id == source_id for object_id, _ in values)
                    )
                ),
                None,
            )
            if revised_source is not None:
                stop_oscillation(
                    bar,
                    "oscillation_stopped_by_source_revision",
                    "OSCILLATION_SOURCE_FACT_REVISED",
                    revised_source,
                    anchor_bar_index=bar.bar_index,
                    price_i64=bar.close_i64,
                )
                exited_this_bar = True

        if current_center_id is not None:
            third_points = [
                (object_id, value)
                for object_id, value in new_points
                if value.get("confirmed") is True
                and value.get("signal_class") == "standard"
                and value.get("signal_type") in {"buy_3", "sell_3"}
                and value.get("reference_object_id") == current_center_id
            ]
            third_points.sort(key=lambda item: (int(item[1].get("bar_index", -1)), item[0]))
            if third_points:
                object_id, point = third_points[-1]
                signal_type = str(point["signal_type"])
                stop_oscillation(
                    bar,
                    "oscillation_stopped_by_B3"
                    if signal_type == "buy_3"
                    else "oscillation_stopped_by_S3",
                    "CONFIRMED_B3_STOPPED_OSCILLATION"
                    if signal_type == "buy_3"
                    else "CONFIRMED_S3_STOPPED_OSCILLATION",
                    object_id,
                    anchor_bar_index=int(point.get("bar_index", bar.bar_index)),
                    price_i64=int(point.get("price_i64", bar.close_i64)),
                    handoff_direction="up" if signal_type == "buy_3" else "down",
                )
                exited_this_bar = True

        if current_center_id is not None:
            center = centers.get(current_center_id)
            reason: str | None = None
            source_id = current_center_id
            if current_center_id in deleted["segment_zhongshu"] or center is None:
                reason = "ACTIVE_CENTER_DELETED"
            elif current_center_id in revised_center_cores:
                reason = "ACTIVE_CENTER_CORE_REVISED"
            elif int(center.get("component_count", 3)) >= 9:
                reason = "NINE_COMPONENT_CENTER_PROMOTION_RISK"
            elif center.get("status", "confirmed") == "left":
                reason = "ACTIVE_CENTER_LEFT_WITHOUT_THIRD_POINT"
            elif center.get("confirmed") is not True:
                reason = "ACTIVE_CENTER_CONFIRMATION_REVISED"
            if reason is not None:
                stop_oscillation(
                    bar,
                    "oscillation_stopped_by_center_change",
                    reason,
                    source_id,
                    anchor_bar_index=int(center.get("end_bar_index", bar.bar_index))
                    if center is not None
                    else bar.bar_index,
                    price_i64=(
                        (int(center["zd_i64"]) + int(center["zg_i64"])) // 2
                        if center is not None and "zd_i64" in center and "zg_i64" in center
                        else bar.close_i64
                    ),
                )
                exited_this_bar = True

        candidate = latest_active_center()
        if (
            current_center_id is not None
            and candidate is not None
            and candidate[0] != current_center_id
            and (
                int(candidate[1]["start_bar_index"]),
                int(candidate[1]["end_bar_index"]),
                candidate[0],
            )
            > (
                int(centers[current_center_id]["start_bar_index"]),
                int(centers[current_center_id]["end_bar_index"]),
                current_center_id,
            )
        ):
            stop_oscillation(
                bar,
                "oscillation_stopped_by_new_center",
                "NEW_ACTIVE_CENTER_REPLACED_OSCILLATION_CONTEXT",
                candidate[0],
                anchor_bar_index=int(candidate[1]["end_bar_index"]),
                price_i64=(int(candidate[1]["zd_i64"]) + int(candidate[1]["zg_i64"])) // 2,
            )
            exited_this_bar = True
        if current_center_id is None:
            candidate = latest_active_center()
            if candidate is not None:
                activate_center(bar, *candidate)

        if current_center_id is None or exited_this_bar:
            continue
        center = centers[current_center_id]
        center_start = int(center["start_bar_index"])
        center_end = int(center["end_bar_index"])
        new_divergence_ids = {object_id for object_id, _ in new_divergences}
        candidate_divergence_ids = new_divergence_ids | pending_divergence_ids
        resolved: list[tuple[int, str, dict[str, Any], str, dict[str, Any]]] = []
        for object_id in sorted(candidate_divergence_ids):
            if object_id in consumed_divergence_ids:
                continue
            divergence = divergences.get(object_id)
            if (
                divergence is None
                or divergence.get("confirmed") is not True
                or divergence.get("divergence_kind") != "consolidation"
                or divergence.get("signal_type") not in {"bottom_divergence", "top_divergence"}
                or divergence.get("reference_object_id") != current_center_id
            ):
                pending_divergence_ids.discard(object_id)
                continue
            endpoint = int(divergence.get("bar_index", -1))
            if not center_start <= endpoint <= center_end:
                pending_divergence_ids.discard(object_id)
                consumed_divergence_ids.add(object_id)
                continue
            expected_direction = (
                "down" if divergence["signal_type"] == "bottom_divergence" else "up"
            )
            matching_monitors = [
                (monitor_id, monitor)
                for monitor_id, monitor in monitors.items()
                if monitor.get("confirmed") is True
                and monitor.get("analysis_level", "segment") == "segment"
                and monitor.get("reference_object_id") == current_center_id
                and int(monitor.get("bar_index", -2)) == endpoint
                and monitor.get("component_direction") == expected_direction
            ]
            if not matching_monitors:
                if object_id in new_divergence_ids:
                    output.transition(
                        bar,
                        "oscillation_waiting_Zn_dependency",
                        "CONFIRMED_OSCILLATION_DIVERGENCE_WAITING_ZN",
                        object_id,
                        anchor_bar_index=endpoint,
                        price_i64=int(divergence.get("price_i64", bar.close_i64)),
                    )
                pending_divergence_ids.add(object_id)
                continue
            pending_divergence_ids.discard(object_id)
            consumed_divergence_ids.add(object_id)
            monitor_id, monitor = max(
                matching_monitors,
                key=lambda item: (int(item[1].get("known_at_bar_index", -1)), item[0]),
            )
            resolved.append((endpoint, object_id, divergence, monitor_id, monitor))

        resolved.sort(key=lambda item: (item[0], item[1]))
        if not resolved:
            continue
        for _, object_id, divergence, _, _ in resolved[:-1]:
            output.transition(
                bar,
                "oscillation_stale_concurrent_divergence_filtered",
                "OLDER_CONCURRENT_OSCILLATION_DIVERGENCE_FILTERED",
                object_id,
                anchor_bar_index=int(divergence["bar_index"]),
                price_i64=int(divergence["price_i64"]),
            )
        endpoint, object_id, divergence, monitor_id, monitor = resolved[-1]
        signal_type = str(divergence["signal_type"])
        desired_side = "long" if signal_type == "bottom_divergence" else "short"
        z_i64 = int(monitor.get("z_i64", center.get("z_i64", 0)))
        zn_i64 = int(monitor["zn_i64"])
        z_twice_i64 = int(monitor.get("z_twice_i64", z_i64 * 2))
        zn_twice_i64 = int(monitor.get("zn_twice_i64", zn_i64 * 2))
        if (desired_side == "long" and zn_twice_i64 > z_twice_i64) or (
            desired_side == "short" and zn_twice_i64 < z_twice_i64
        ):
            priority = "strong"
            quantity = strong_quantity
        elif zn_twice_i64 == z_twice_i64:
            priority = "neutral"
            quantity = neutral_quantity
        else:
            priority = "weak"
            quantity = weak_quantity
        zd_i64, zg_i64 = center_core(center)
        center_width = zg_i64 - zd_i64
        net_range = center_width - estimated_cost
        entry_count = entries_by_center.get(current_center_id, 0)
        risk_reason: str | None = None
        if desired_side == "long" and not allow_long:
            risk_reason = "OSCILLATION_LONG_DISABLED"
        elif desired_side == "short" and not allow_short:
            risk_reason = "OSCILLATION_SHORT_DISABLED"
        elif center_width <= estimated_cost or net_range < minimum_net_range:
            risk_reason = "OSCILLATION_RANGE_NOT_ABOVE_COST"
        elif desired_side == "long" and zn_twice_i64 < zd_i64 * 2 and not fast_execution:
            risk_reason = "ZN_BELOW_ZD_WITHOUT_FAST_EXECUTION"
        elif desired_side == "short" and zn_twice_i64 > zg_i64 * 2 and not fast_execution:
            risk_reason = "ZN_ABOVE_ZG_WITHOUT_FAST_EXECUTION"
        elif entry_count >= max_entries:
            risk_reason = "CENTER_TURNOVER_CAP_REACHED"

        anchor_price = int(divergence.get("price_i64", bar.close_i64))
        reason = (
            "CONFIRMED_CENTER_BOTTOM_OSCILLATION_DIVERGENCE"
            if desired_side == "long"
            else "CONFIRMED_CENTER_TOP_OSCILLATION_DIVERGENCE"
        )
        opposite_position = position_side not in {"flat", desired_side}
        same_position = position_side == desired_side
        will_open = not same_position and risk_reason is None
        next_state = (
            f"oscillation_{desired_side}_{priority}"
            if will_open or same_position
            else f"oscillation_flat_{desired_side}_entry_filtered"
        )
        stage_id = output.transition(
            bar,
            next_state,
            reason if risk_reason is None else risk_reason,
            object_id,
            anchor_bar_index=endpoint,
            price_i64=anchor_price,
            details={
                "source_center_id": current_center_id,
                "source_monitor_id": monitor_id,
                "priority": priority,
                "quantity": quantity,
                "z_i64": z_i64,
                "zn_i64": zn_i64,
                "z_twice_i64": z_twice_i64,
                "zn_twice_i64": zn_twice_i64,
                "center_width_i64": center_width,
                "estimated_round_trip_cost_i64": estimated_cost,
                "net_range_i64": net_range,
                "entry_count_before": entry_count,
                "max_entries_per_center": max_entries,
            },
        )
        if opposite_position:
            output.trade(
                bar,
                "close_long" if position_side == "long" else "close_short",
                position_quantity,
                reason,
                object_id,
                anchor_bar_index=endpoint,
                price_i64=anchor_price,
                stage_id=stage_id,
                details={"source_center_id": current_center_id, "priority": priority},
            )
            position_side = "flat"
            position_quantity = 0
            clear_position_context()
        if will_open:
            output.trade(
                bar,
                "open_long" if desired_side == "long" else "open_short",
                quantity,
                reason,
                object_id,
                anchor_bar_index=endpoint,
                price_i64=anchor_price,
                stage_id=stage_id,
                details={
                    "source_center_id": current_center_id,
                    "source_monitor_id": monitor_id,
                    "priority": priority,
                },
            )
            position_side = desired_side
            position_quantity = quantity
            source_divergence_id = object_id
            source_monitor_id = monitor_id
            entries_by_center[current_center_id] = entry_count + 1
        semantic_action = (
            "hold"
            if same_position
            else "enter_or_reverse"
            if will_open or opposite_position
            else "filtered"
        )
        event_type = (
            ("swing_buy" if desired_side == "long" else "swing_sell")
            if semantic_action == "enter_or_reverse"
            else ("swing_buy_hold" if desired_side == "long" else "swing_sell_hold")
            if semantic_action == "hold"
            else ("swing_buy_filtered" if desired_side == "long" else "swing_sell_filtered")
        )
        output.chart_event(
            bar,
            event_type,
            reason if risk_reason is None else risk_reason,
            object_id,
            anchor_bar_index=endpoint,
            price_i64=anchor_price,
            details={
                "source_center_id": current_center_id,
                "source_monitor_id": monitor_id,
                "priority": priority,
                "quantity": quantity,
                "action": semantic_action,
            },
        )

    return output.result(feed.bars)


def _run_same_level_decomposition_program(
    payload: dict[str, Any],
    guard: PathGuard,
    cancelled: threading.Event,
    *,
    last_bar_index: int | None,
) -> StrategyRun:
    """Run ALG-STR-005 from the causal confirmed-segment decomposition feed."""
    parameters = payload["parameters"]
    quantity = parameters["operation_quantity"]
    if isinstance(quantity, bool) or not isinstance(quantity, int) or not 1 <= quantity <= 100:
        raise ValueError("operation_quantity must be an integer between 1 and 100")
    for name in ("odd_direction_is_down", "allow_long", "allow_short"):
        if not isinstance(parameters[name], bool):
            raise ValueError(f"{name} must be a boolean")
    odd_direction = "down" if parameters["odd_direction_is_down"] else "up"
    allow_long = parameters["allow_long"]
    allow_short = parameters["allow_short"]
    feed = _load_chan_strategy_feed(
        payload,
        guard,
        cancelled,
        last_bar_index=last_bar_index,
        object_types={"segment", "segment_zhongshu", "level_center", "divergence"},
    )
    output = _CausalStrategyOutput("SLD", "waiting_same_level_sequence")
    active_segments: dict[str, dict[str, Any]] = {}
    active_centers: dict[str, dict[str, Any]] = {}
    active_level_centers: dict[str, dict[str, Any]] = {}
    active_divergences: dict[str, dict[str, Any]] = {}
    sequence: list[dict[str, Any]] = []
    reversed_comparisons: set[tuple[str, str]] = set()
    promotion_candidate_ids: set[str] = set()
    pending_branch: dict[str, Any] | None = None
    position_side = "flat"
    position_quantity = 0
    position_source_id: str | None = None
    active_level_id = "L0"
    active_promotion_id: str | None = None

    def segment_signature(value: dict[str, Any]) -> tuple[Any, ...]:
        return tuple(
            value.get(name)
            for name in (
                "start_bar_index",
                "end_bar_index",
                "start_price_i64",
                "end_price_i64",
                "direction",
                "confirmed",
                "confirmed_at_bar_index",
            )
        )

    def divergence_signature(value: dict[str, Any]) -> tuple[Any, ...]:
        return tuple(
            value.get(name)
            for name in (
                "bar_index",
                "price_i64",
                "signal_type",
                "divergence_kind",
                "reference_object_id",
                "confirmed",
                "confirmed_at_bar_index",
            )
        )

    def close_position(
        bar: StrategyBar,
        reason: str,
        source_id: str,
        stage_id: str,
        *,
        anchor_bar_index: int,
        price_i64: int,
    ) -> None:
        nonlocal position_side, position_quantity, position_source_id
        if position_side != "flat":
            output.trade(
                bar,
                "close_long" if position_side == "long" else "close_short",
                position_quantity,
                reason,
                source_id,
                anchor_bar_index=anchor_bar_index,
                price_i64=price_i64,
                stage_id=stage_id,
                details={"decomposition_unit": "confirmed_segment"},
            )
        position_side = "flat"
        position_quantity = 0
        position_source_id = None

    def reset_decomposition(
        bar: StrategyBar,
        reason: str,
        source_id: str,
        *,
        anchor_bar_index: int,
        price_i64: int,
    ) -> None:
        nonlocal pending_branch
        stage_id = output.transition(
            bar,
            "same_level_decomposition_reset",
            reason,
            source_id,
            anchor_bar_index=anchor_bar_index,
            price_i64=price_i64,
            details={"decomposition_unit": "confirmed_segment"},
        )
        close_position(
            bar,
            reason,
            source_id,
            stage_id,
            anchor_bar_index=anchor_bar_index,
            price_i64=price_i64,
        )
        output.chart_event(
            bar,
            "same_level_decomposition_reset",
            reason,
            source_id,
            anchor_bar_index=anchor_bar_index,
            price_i64=price_i64,
        )
        sequence.clear()
        pending_branch = None

    def matching_divergence(unit: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
        expected_type = "top_divergence" if unit["direction"] == "up" else "bottom_divergence"
        values = [
            (object_id, value)
            for object_id, value in active_divergences.items()
            if value.get("confirmed") is True
            and value.get("divergence_kind") == "consolidation"
            and value.get("signal_type") == expected_type
            and int(value.get("bar_index", -1)) == unit["end_bar_index"]
        ]
        if not values:
            return None
        return max(
            values,
            key=lambda item: (
                int(item[1].get("known_at_bar_index", -1)),
                item[0],
            ),
        )

    def reverse_operation(
        bar: StrategyBar,
        reference: dict[str, Any],
        current: dict[str, Any],
        divergence: tuple[str, dict[str, Any]] | None,
    ) -> None:
        nonlocal position_side, position_quantity, position_source_id, pending_branch
        comparison_key = (reference["object_id"], current["object_id"])
        if comparison_key in reversed_comparisons:
            return
        reversed_comparisons.add(comparison_key)
        pending_branch = None
        desired_side = "short" if current["direction"] == "up" else "long"
        enabled = allow_short if desired_side == "short" else allow_long
        source_id = divergence[0] if divergence is not None else current["object_id"]
        reason = (
            "CONFIRMED_SAME_LEVEL_CONSOLIDATION_DIVERGENCE"
            if divergence is not None
            else "SAME_LEVEL_LATER_MOVEMENT_FAILED_NEW_EXTREME"
        )
        same_position = position_side == desired_side
        opposite_position = position_side not in {"flat", desired_side}
        will_open = not same_position and enabled
        action_name = "buy" if desired_side == "long" else "sell"
        next_state = (
            f"same_level_{desired_side}_hold"
            if same_position
            else f"same_level_{desired_side}"
            if will_open
            else f"same_level_{action_name}_filtered"
        )
        details = {
            "ai_object_id": reference["object_id"],
            "ai_index": reference["sequence_index"],
            "ai_plus_2_object_id": current["object_id"],
            "ai_plus_2_index": current["sequence_index"],
            "odd_direction": odd_direction,
            "decomposition_unit": "confirmed_segment",
            "comparison": "directional_structural_endpoint",
            "later_made_new_extreme": (
                current["end_price_i64"] > reference["end_price_i64"]
                if current["direction"] == "up"
                else current["end_price_i64"] < reference["end_price_i64"]
            ),
            "source_divergence_id": None if divergence is None else divergence[0],
            "quantity": quantity,
        }
        stage_id = output.transition(
            bar,
            next_state,
            reason
            if enabled or opposite_position
            else f"SAME_LEVEL_{action_name.upper()}_DISABLED",
            source_id,
            anchor_bar_index=current["end_bar_index"],
            price_i64=current["end_price_i64"],
            details=details,
        )
        if opposite_position:
            close_position(
                bar,
                reason,
                source_id,
                stage_id,
                anchor_bar_index=current["end_bar_index"],
                price_i64=current["end_price_i64"],
            )
        if will_open:
            output.trade(
                bar,
                "open_long" if desired_side == "long" else "open_short",
                quantity,
                reason,
                source_id,
                anchor_bar_index=current["end_bar_index"],
                price_i64=current["end_price_i64"],
                stage_id=stage_id,
                details=details,
            )
            position_side = desired_side
            position_quantity = quantity
            position_source_id = source_id
        event_type = (
            f"same_level_{action_name}_hold"
            if same_position
            else f"same_level_{action_name}"
            if will_open or opposite_position
            else f"same_level_{action_name}_filtered"
        )
        output.chart_event(
            bar,
            event_type,
            reason
            if enabled or opposite_position
            else f"SAME_LEVEL_{action_name.upper()}_DISABLED",
            source_id,
            anchor_bar_index=current["end_bar_index"],
            price_i64=current["end_price_i64"],
            details=details,
        )

    def consider_comparison(bar: StrategyBar, current: dict[str, Any]) -> None:
        nonlocal pending_branch
        reference = next(
            (
                value
                for value in reversed(sequence[:-1])
                if value["sequence_index"] == current["sequence_index"] - 2
            ),
            None,
        )
        if reference is None or reference["direction"] != current["direction"]:
            return
        comparison_key = (reference["object_id"], current["object_id"])
        if comparison_key in reversed_comparisons:
            return
        later_made_new_extreme = (
            current["end_price_i64"] > reference["end_price_i64"]
            if current["direction"] == "up"
            else current["end_price_i64"] < reference["end_price_i64"]
        )
        divergence = matching_divergence(current)
        if not later_made_new_extreme or divergence is not None:
            reverse_operation(bar, reference, current, divergence)
            return
        details = {
            "ai_object_id": reference["object_id"],
            "ai_index": reference["sequence_index"],
            "ai_plus_2_object_id": current["object_id"],
            "ai_plus_2_index": current["sequence_index"],
            "odd_direction": odd_direction,
            "decomposition_unit": "confirmed_segment",
        }
        output.transition(
            bar,
            f"same_level_hold_{current['direction']}",
            "SAME_LEVEL_LATER_MOVEMENT_NEW_EXTREME_WITHOUT_DIVERGENCE",
            current["object_id"],
            anchor_bar_index=current["end_bar_index"],
            price_i64=current["end_price_i64"],
            details=details,
        )
        output.chart_event(
            bar,
            "same_level_hold",
            "SAME_LEVEL_LATER_MOVEMENT_NEW_EXTREME_WITHOUT_DIVERGENCE",
            current["object_id"],
            anchor_bar_index=current["end_bar_index"],
            price_i64=current["end_price_i64"],
            details=details,
        )
        pending_branch = {
            "reference": reference,
            "compared": current,
            "expected_index": current["sequence_index"] + 1,
        }

    def resolve_ai_plus_3_branch(bar: StrategyBar, current: dict[str, Any]) -> None:
        nonlocal pending_branch
        if pending_branch is None or current["sequence_index"] != pending_branch["expected_index"]:
            return
        reference = pending_branch["reference"]
        compared = pending_branch["compared"]
        if current["direction"] == compared["direction"]:
            pending_branch = None
            return
        destroyed = (
            current["end_price_i64"] < reference["end_price_i64"]
            if compared["direction"] == "up"
            else current["end_price_i64"] > reference["end_price_i64"]
        )
        event_type = "wait_new_same_level_structure" if destroyed else "continue_original_center"
        reason = (
            "AI_PLUS_3_DESTROYED_AI_DIRECTIONAL_EXTREME"
            if destroyed
            else "AI_PLUS_3_PRESERVED_AI_DIRECTIONAL_EXTREME"
        )
        details = {
            "ai_object_id": reference["object_id"],
            "ai_index": reference["sequence_index"],
            "ai_plus_2_object_id": compared["object_id"],
            "ai_plus_2_index": compared["sequence_index"],
            "ai_plus_3_object_id": current["object_id"],
            "ai_plus_3_index": current["sequence_index"],
            "odd_direction": odd_direction,
            "decomposition_unit": "confirmed_segment",
        }
        output.transition(
            bar,
            "same_level_wait_new_structure" if destroyed else "same_level_continue_original_center",
            reason,
            current["object_id"],
            anchor_bar_index=current["end_bar_index"],
            price_i64=current["end_price_i64"],
            details=details,
        )
        output.chart_event(
            bar,
            event_type,
            reason,
            current["object_id"],
            anchor_bar_index=current["end_bar_index"],
            price_i64=current["end_price_i64"],
            details=details,
        )
        pending_branch = None

    def append_segment(bar: StrategyBar, object_id: str, value: dict[str, Any]) -> None:
        if active_level_id != "L0" or value.get("confirmed") is not True:
            return
        direction = value.get("direction")
        if direction not in {"up", "down"}:
            return
        start = int(value.get("start_bar_index", -1))
        end = int(value.get("end_bar_index", -1))
        if start < 0 or end <= start:
            return
        if sequence and (
            start != sequence[-1]["end_bar_index"] or direction == sequence[-1]["direction"]
        ):
            reset_decomposition(
                bar,
                "CANONICAL_SAME_LEVEL_SEQUENCE_DISCONTINUITY",
                object_id,
                anchor_bar_index=end,
                price_i64=int(value["end_price_i64"]),
            )
        sequence_index = (
            sequence[-1]["sequence_index"] + 1
            if sequence
            else 1
            if direction == odd_direction
            else 2
        )
        unit = {
            "object_id": object_id,
            "sequence_index": sequence_index,
            "start_bar_index": start,
            "end_bar_index": end,
            "start_price_i64": int(value["start_price_i64"]),
            "end_price_i64": int(value["end_price_i64"]),
            "direction": direction,
        }
        sequence.append(unit)
        resolve_ai_plus_3_branch(bar, unit)
        consider_comparison(bar, unit)

    for bar in feed.bars:
        if bar.bar_index % 256 == 0 and cancelled.is_set():
            raise InterruptedError("strategy execution cancelled")
        new_segments: list[tuple[str, dict[str, Any]]] = []
        new_divergence_ids: set[str] = set()
        new_centers: list[tuple[str, dict[str, Any]]] = []
        new_level_centers: list[tuple[str, dict[str, Any]]] = []
        reset_source_ids: set[str] = set()
        promotion_reset_ids: set[str] = set()
        for event in feed.events_by_bar.get(bar.bar_index, []):
            if event.object_type == "segment":
                previous = active_segments.get(event.object_id)
                if event.operation == "delete":
                    active_segments.pop(event.object_id, None)
                    if any(unit["object_id"] == event.object_id for unit in sequence):
                        reset_source_ids.add(event.object_id)
                    continue
                value = json.loads(event.payload_json)
                active_segments[event.object_id] = value
                if previous is None or segment_signature(previous) != segment_signature(value):
                    if previous is not None and any(
                        unit["object_id"] == event.object_id for unit in sequence
                    ):
                        reset_source_ids.add(event.object_id)
                    new_segments.append((event.object_id, value))
            elif event.object_type == "divergence":
                previous = active_divergences.get(event.object_id)
                if event.operation == "delete":
                    active_divergences.pop(event.object_id, None)
                    if position_source_id == event.object_id:
                        reset_source_ids.add(event.object_id)
                    continue
                value = json.loads(event.payload_json)
                active_divergences[event.object_id] = value
                if previous is None or divergence_signature(previous) != divergence_signature(
                    value
                ):
                    if previous is not None and position_source_id == event.object_id:
                        reset_source_ids.add(event.object_id)
                    new_divergence_ids.add(event.object_id)
            elif event.object_type == "segment_zhongshu":
                if event.operation == "delete":
                    active_centers.pop(event.object_id, None)
                    continue
                value = json.loads(event.payload_json)
                active_centers[event.object_id] = value
                new_centers.append((event.object_id, value))
            elif event.object_type == "level_center":
                previous = active_level_centers.get(event.object_id)
                if event.operation == "delete":
                    active_level_centers.pop(event.object_id, None)
                    if active_promotion_id == event.object_id:
                        promotion_reset_ids.add(event.object_id)
                    continue
                value = json.loads(event.payload_json)
                active_level_centers[event.object_id] = value
                if active_promotion_id == event.object_id and value.get("confirmed") is not True:
                    promotion_reset_ids.add(event.object_id)
                elif previous is None:
                    new_level_centers.append((event.object_id, value))

        if reset_source_ids:
            source_id = sorted(reset_source_ids)[0]
            reset_decomposition(
                bar,
                "CANONICAL_SAME_LEVEL_DECOMPOSITION_REVISED",
                source_id,
                anchor_bar_index=bar.bar_index,
                price_i64=bar.close_i64,
            )

        if promotion_reset_ids:
            source_id = sorted(promotion_reset_ids)[0]
            reset_decomposition(
                bar,
                "CONFIRMED_HIGHER_LEVEL_CENTER_REVISED",
                source_id,
                anchor_bar_index=bar.bar_index,
                price_i64=bar.close_i64,
            )
            active_level_id = "L0"
            active_promotion_id = None

        promotion = next(
            (
                (object_id, value)
                for object_id, value in sorted(
                    new_centers,
                    key=lambda item: (
                        int(item[1].get("end_bar_index", -1)),
                        item[0],
                    ),
                )
                if object_id not in promotion_candidate_ids
                and value.get("confirmed") is True
                and value.get("analysis_level", "segment") == "segment"
                and value.get("component_kind", "segment") == "segment"
                and int(value.get("component_count", 0)) >= 9
            ),
            None,
        )
        if promotion is not None and active_level_id == "L0":
            object_id, center = promotion
            promotion_candidate_ids.add(object_id)
            anchor = int(center.get("end_bar_index", bar.bar_index))
            price = int(
                center.get(
                    "z_i64",
                    (
                        int(center.get("zd_i64", bar.close_i64))
                        + int(center.get("zg_i64", bar.close_i64))
                    )
                    // 2,
                )
            )
            output.transition(
                bar,
                "same_level_promotion_candidate",
                "NINE_COMPONENT_HIGHER_LEVEL_CENTER_CANDIDATE",
                object_id,
                anchor_bar_index=anchor,
                price_i64=price,
                details={
                    "component_count": int(center["component_count"]),
                    "promotion_status": "candidate",
                    "small_level_action": "wait_for_confirmed_level_center",
                },
            )
            output.chart_event(
                bar,
                "promote_level_candidate",
                "NINE_COMPONENT_HIGHER_LEVEL_CENTER_CANDIDATE",
                object_id,
                anchor_bar_index=anchor,
                price_i64=price,
                details={
                    "component_count": int(center["component_count"]),
                    "promotion_status": "candidate",
                    "from_level_id": "L0",
                    "to_level_id": "L1",
                },
            )

        confirmed_promotion = next(
            (
                (object_id, value)
                for object_id, value in sorted(
                    new_level_centers,
                    key=lambda item: (
                        int(item[1].get("confirmed_at_bar_index") or -1),
                        item[0],
                    ),
                )
                if value.get("confirmed") is True
                and value.get("parent_level_id") == active_level_id
                and isinstance(value.get("promoted_from_center_id"), str)
                and value.get("promoted_from_center_id") in active_centers
                and int(
                    active_centers[str(value["promoted_from_center_id"])].get("component_count", 0)
                )
                >= 9
            ),
            None,
        )
        if confirmed_promotion is not None:
            object_id, center = confirmed_promotion
            source_center_id = str(center["promoted_from_center_id"])
            source_center = active_centers[source_center_id]
            target_level_id = str(center.get("level_id", ""))
            if not target_level_id.startswith("L") or target_level_id == active_level_id:
                raise ValueError(
                    "confirmed level promotion must advance to another structural level"
                )
            anchor = int(center.get("end_bar_index", bar.bar_index))
            price = (
                int(center.get("zd_i64", bar.close_i64)) + int(center.get("zg_i64", bar.close_i64))
            ) // 2
            details = {
                "component_count": int(source_center["component_count"]),
                "promotion_status": "confirmed",
                "migration_status": "waiting_higher_level_sequence",
                "from_level_id": active_level_id,
                "to_level_id": target_level_id,
                "promoted_from_center_id": source_center_id,
            }
            stage_id = output.transition(
                bar,
                "same_level_promoted_waiting_sequence",
                "CONFIRMED_HIGHER_LEVEL_CENTER_PROMOTION",
                object_id,
                anchor_bar_index=anchor,
                price_i64=price,
                details=details,
            )
            close_position(
                bar,
                "CONFIRMED_HIGHER_LEVEL_CENTER_PROMOTION",
                object_id,
                stage_id,
                anchor_bar_index=anchor,
                price_i64=price,
            )
            output.chart_event(
                bar,
                "promote_level",
                "CONFIRMED_HIGHER_LEVEL_CENTER_PROMOTION",
                object_id,
                anchor_bar_index=anchor,
                price_i64=price,
                details=details,
            )
            active_level_id = target_level_id
            active_promotion_id = object_id
            sequence.clear()
            pending_branch = None
            continue

        for object_id, value in sorted(
            new_segments,
            key=lambda item: (
                int(item[1].get("end_bar_index", -1)),
                int(item[1].get("start_bar_index", -1)),
                item[0],
            ),
        ):
            append_segment(bar, object_id, value)

        if active_level_id != "L0":
            continue
        for divergence_id in sorted(new_divergence_ids):
            divergence = active_divergences.get(divergence_id)
            if divergence is None or divergence.get("confirmed") is not True:
                continue
            endpoint = int(divergence.get("bar_index", -1))
            current = next(
                (unit for unit in reversed(sequence) if unit["end_bar_index"] == endpoint),
                None,
            )
            if current is None or sequence[-1]["sequence_index"] - current["sequence_index"] > 1:
                continue
            reference = next(
                (
                    unit
                    for unit in reversed(sequence)
                    if unit["sequence_index"] == current["sequence_index"] - 2
                ),
                None,
            )
            matched = matching_divergence(current)
            if reference is not None and matched is not None and matched[0] == divergence_id:
                reverse_operation(bar, reference, current, matched)

    return output.result(feed.bars)


def _run_three_level_complete_classification(
    payload: dict[str, Any],
    guard: PathGuard,
    cancelled: threading.Event,
    *,
    last_bar_index: int | None,
) -> StrategyRun:
    """Run ALG-STR-007 over the explicit segment -> center -> center-chain graph."""
    parameters = payload["parameters"]
    profile_id = parameters["level_graph_profile_id"]
    quantity = parameters["operation_quantity"]
    if isinstance(profile_id, bool) or profile_id != 1:
        raise ValueError("level_graph_profile_id must be 1")
    if isinstance(quantity, bool) or not isinstance(quantity, int) or not 1 <= quantity <= 100:
        raise ValueError("operation_quantity must be an integer between 1 and 100")
    boolean_names = (
        "allow_long",
        "allow_short",
        "can_handle_mid_third_point",
        "can_handle_mid_center_continue",
        "can_handle_high_change_candidate",
    )
    for name in boolean_names:
        if not isinstance(parameters[name], bool):
            raise ValueError(f"{name} must be a boolean")
    allow_long = parameters["allow_long"]
    allow_short = parameters["allow_short"]
    ability = {
        "mid_third_point": parameters["can_handle_mid_third_point"],
        "mid_center_continue": parameters["can_handle_mid_center_continue"],
        "high_change_candidate": parameters["can_handle_high_change_candidate"],
    }
    feed = _load_chan_strategy_feed(
        payload,
        guard,
        cancelled,
        last_bar_index=last_bar_index,
        object_types={
            "segment",
            "segment_zhongshu",
            "movement_state",
            "divergence",
            "trade_point",
        },
    )
    output = _CausalStrategyOutput("3LC", "NO_ACTIVE_THREE_LEVEL_CONTEXT")
    graph_details = {
        "level_graph_profile_id": 1,
        "level_graph_profile": "segment_center_chain_v1",
        "low_level": "confirmed_segment_turn",
        "middle_level": "confirmed_segment_zhongshu",
        "high_level": "confirmed_center_migration_chain",
    }
    centers: dict[str, dict[str, Any]] = {}
    segments: dict[str, dict[str, Any]] = {}
    divergences: dict[str, dict[str, Any]] = {}
    trade_points: dict[str, dict[str, Any]] = {}
    movements: dict[str, dict[str, Any]] = {}
    context_center_id: str | None = None
    context_center: dict[str, Any] | None = None
    context_direction: str | None = None
    phase = "NO_ACTIVE_THREE_LEVEL_CONTEXT"
    low_turn_id: str | None = None
    low_turn_type: str | None = None
    low_turn_bar_index: int | None = None
    middle_source_id: str | None = None
    middle_source_type: str | None = None
    middle_completion_bar_index: int | None = None
    high_source_id: str | None = None
    position_side = "flat"
    position_quantity = 0
    activated_context_signatures: set[tuple[str, tuple[Any, ...]]] = set()
    consumed_low_turn_ids: set[str] = set()

    def center_signature(value: dict[str, Any]) -> tuple[Any, ...]:
        return tuple(
            value.get(name)
            for name in (
                "start_bar_index",
                "end_bar_index",
                "zd_i64",
                "zg_i64",
                "component_count",
                "status",
                "leave_direction",
                "confirmed",
                "confirmed_at_bar_index",
            )
        )

    def source_signature(object_type: str, value: dict[str, Any]) -> tuple[Any, ...]:
        names = {
            "segment": (
                "start_bar_index",
                "end_bar_index",
                "start_price_i64",
                "end_price_i64",
                "direction",
                "confirmed",
                "confirmed_at_bar_index",
            ),
            "divergence": (
                "bar_index",
                "price_i64",
                "signal_type",
                "divergence_kind",
                "reference_object_id",
                "confirmed",
                "confirmed_at_bar_index",
            ),
            "trade_point": (
                "bar_index",
                "price_i64",
                "signal_type",
                "signal_class",
                "reference_object_id",
                "confirmed",
                "confirmed_at_bar_index",
            ),
            "movement_state": (
                "start_bar_index",
                "end_bar_index",
                "price_i64",
                "state_type",
                "direction",
                "reference_object_id",
                "confirmed",
                "confirmed_at_bar_index",
            ),
        }[object_type]
        return tuple(value.get(name) for name in names)

    def close_position(
        bar: StrategyBar,
        reason: str,
        source_id: str,
        stage_id: str,
        *,
        anchor_bar_index: int,
        price_i64: int,
    ) -> None:
        nonlocal position_side, position_quantity
        if position_side != "flat":
            output.trade(
                bar,
                "close_long" if position_side == "long" else "close_short",
                position_quantity,
                reason,
                source_id,
                anchor_bar_index=anchor_bar_index,
                price_i64=price_i64,
                stage_id=stage_id,
                details=graph_details,
            )
        position_side = "flat"
        position_quantity = 0

    def clear_context() -> None:
        nonlocal context_center_id, context_center, context_direction, phase
        nonlocal low_turn_id, low_turn_type, low_turn_bar_index
        nonlocal middle_source_id, middle_source_type, middle_completion_bar_index
        nonlocal high_source_id
        context_center_id = None
        context_center = None
        context_direction = None
        phase = "NO_ACTIVE_THREE_LEVEL_CONTEXT"
        low_turn_id = None
        low_turn_type = None
        low_turn_bar_index = None
        middle_source_id = None
        middle_source_type = None
        middle_completion_bar_index = None
        high_source_id = None

    def reset_context(bar: StrategyBar, reason: str, source_id: str) -> None:
        stage_id = output.transition(
            bar,
            "THREE_LEVEL_CONTEXT_RESET",
            reason,
            source_id,
            anchor_bar_index=bar.bar_index,
            price_i64=bar.close_i64,
            details={**graph_details, "source_center_id": context_center_id},
        )
        close_position(
            bar,
            reason,
            source_id,
            stage_id,
            anchor_bar_index=bar.bar_index,
            price_i64=bar.close_i64,
        )
        output.chart_event(
            bar,
            "three_level_context_reset",
            reason,
            source_id,
            anchor_bar_index=bar.bar_index,
            price_i64=bar.close_i64,
            details={"source_center_id": context_center_id},
        )
        clear_context()

    def valid_middle_context(value: dict[str, Any]) -> bool:
        return (
            value.get("confirmed") is True
            and value.get("analysis_level", "segment") == "segment"
            and value.get("component_kind", "segment") == "segment"
            and value.get("status") == "left"
            and value.get("leave_direction") in {"up", "down"}
            and int(value.get("component_count", 0)) < 9
        )

    def activate_context(bar: StrategyBar, object_id: str, value: dict[str, Any]) -> None:
        nonlocal context_center_id, context_center, context_direction, phase
        signature = (object_id, center_signature(value))
        activated_context_signatures.add(signature)
        context_center_id = object_id
        context_center = value
        context_direction = str(value["leave_direction"])
        phase = "WAIT_LOW_TURN"
        zd_i64 = int(value["zd_i64"])
        zg_i64 = int(value["zg_i64"])
        details = {
            **graph_details,
            "source_center_id": object_id,
            "middle_leave_direction": context_direction,
            "expected_low_turn": (
                "bottom_divergence_or_B1" if context_direction == "down" else "top_divergence_or_S1"
            ),
        }
        output.transition(
            bar,
            "WAIT_LOW_TURN",
            "MIDDLE_LEVEL_LEFT_CENTER_WAITING_LOWEST_TURN",
            object_id,
            anchor_bar_index=int(value["end_bar_index"]),
            price_i64=(zd_i64 + zg_i64) // 2,
            details=details,
        )
        output.chart_event(
            bar,
            "wait_low_turn",
            "MIDDLE_LEVEL_LEFT_CENTER_WAITING_LOWEST_TURN",
            object_id,
            anchor_bar_index=int(value["end_bar_index"]),
            price_i64=(zd_i64 + zg_i64) // 2,
            details=details,
        )

    def low_turn_candidate(
        new_divergences: list[tuple[str, dict[str, Any]]],
        new_points: list[tuple[str, dict[str, Any]]],
    ) -> tuple[str, str, dict[str, Any]] | None:
        if context_center is None or context_direction is None:
            return None
        center_end = int(context_center["end_bar_index"])
        expected_divergence = (
            "bottom_divergence" if context_direction == "down" else "top_divergence"
        )
        expected_point = "buy_1" if context_direction == "down" else "sell_1"
        candidates: list[tuple[int, int, str, str, dict[str, Any]]] = []
        for object_id, value in new_points:
            if (
                object_id not in consumed_low_turn_ids
                and value.get("confirmed") is True
                and value.get("signal_class") == "standard"
                and value.get("signal_type") == expected_point
                and int(value.get("bar_index", -1)) > center_end
            ):
                candidates.append((int(value["bar_index"]), 0, object_id, "trade_point", value))
        for object_id, value in new_divergences:
            if (
                object_id not in consumed_low_turn_ids
                and value.get("confirmed") is True
                and value.get("signal_type") == expected_divergence
                and value.get("divergence_kind") in {"trend", "consolidation"}
                and int(value.get("bar_index", -1)) > center_end
            ):
                candidates.append((int(value["bar_index"]), 1, object_id, "divergence", value))
        if not candidates:
            return None
        _, _, object_id, object_type, value = min(candidates)
        return object_id, object_type, value

    def activate_low_turn(
        bar: StrategyBar,
        object_id: str,
        object_type: str,
        value: dict[str, Any],
    ) -> None:
        nonlocal low_turn_id, low_turn_type, low_turn_bar_index, phase
        nonlocal position_side, position_quantity
        assert context_center_id is not None and context_direction is not None
        consumed_low_turn_ids.add(object_id)
        low_turn_id = object_id
        low_turn_type = object_type
        low_turn_bar_index = int(value["bar_index"])
        desired_side = "long" if context_direction == "down" else "short"
        direction_allowed = allow_long if desired_side == "long" else allow_short
        all_branches_handled = all(ability.values())
        participation_quantity = quantity if direction_allowed and all_branches_handled else 0
        reason = (
            "LOWEST_LEVEL_TURN_CONFIRMED"
            if participation_quantity > 0
            else "LEGAL_BRANCH_UNMANAGEABLE"
            if not all_branches_handled
            else "LOWEST_LEVEL_COUNTER_DIRECTION_DISABLED"
        )
        phase = "LOW_TURN_ACTIVE"
        details = {
            **graph_details,
            "source_center_id": context_center_id,
            "low_turn_source_type": object_type,
            "legal_future_branches": [
                "mid_third_point",
                "mid_center_continue",
                "high_change_candidate",
            ],
            "handled_branches": [name for name, handled in ability.items() if handled],
            "max_participation_quantity": participation_quantity,
            "desired_side": desired_side,
        }
        stage_id = output.transition(
            bar,
            "LOW_TURN_ACTIVE",
            reason,
            object_id,
            anchor_bar_index=low_turn_bar_index,
            price_i64=int(value["price_i64"]),
            details=details,
        )
        if participation_quantity > 0:
            output.trade(
                bar,
                "open_long" if desired_side == "long" else "open_short",
                participation_quantity,
                reason,
                object_id,
                anchor_bar_index=low_turn_bar_index,
                price_i64=int(value["price_i64"]),
                stage_id=stage_id,
                details=details,
            )
            position_side = desired_side
            position_quantity = participation_quantity
        output.chart_event(
            bar,
            "low_turn_active" if participation_quantity > 0 else "low_turn_participation_blocked",
            reason,
            object_id,
            anchor_bar_index=low_turn_bar_index,
            price_i64=int(value["price_i64"]),
            details=details,
        )
        output.chart_event(
            bar,
            "participation_cap",
            reason,
            object_id,
            anchor_bar_index=low_turn_bar_index,
            price_i64=int(value["price_i64"]),
            details={"max_participation_quantity": participation_quantity},
        )

    def middle_third_point(
        new_points: list[tuple[str, dict[str, Any]]],
    ) -> tuple[str, dict[str, Any]] | None:
        if context_center_id is None or context_direction is None or low_turn_bar_index is None:
            return None
        expected = "sell_3" if context_direction == "down" else "buy_3"
        values = [
            (object_id, value)
            for object_id, value in new_points
            if value.get("confirmed") is True
            and value.get("signal_class") == "standard"
            and value.get("signal_type") == expected
            and value.get("reference_object_id") == context_center_id
            and int(value.get("bar_index", -1)) >= low_turn_bar_index
        ]
        if not values:
            return None
        return min(values, key=lambda item: (int(item[1]["bar_index"]), item[0]))

    def classify_middle_third(bar: StrategyBar, object_id: str, value: dict[str, Any]) -> None:
        nonlocal phase, middle_source_id, middle_source_type, middle_completion_bar_index
        assert context_center_id is not None and context_direction is not None
        phase = "MID_THIRD_POINT"
        middle_source_id = object_id
        middle_source_type = "trade_point"
        middle_completion_bar_index = int(value["bar_index"])
        reason = (
            "MIDDLE_LEVEL_S3_CONFIRMED"
            if context_direction == "down"
            else "MIDDLE_LEVEL_B3_CONFIRMED"
        )
        details = {
            **graph_details,
            "source_center_id": context_center_id,
            "middle_branch": "third_point",
            "next_high_state": "candidate_only_after_middle_migration_completion",
        }
        stage_id = output.transition(
            bar,
            "MID_THIRD_POINT",
            reason,
            object_id,
            anchor_bar_index=int(value["bar_index"]),
            price_i64=int(value["price_i64"]),
            details=details,
        )
        close_position(
            bar,
            reason,
            object_id,
            stage_id,
            anchor_bar_index=int(value["bar_index"]),
            price_i64=int(value["price_i64"]),
        )
        output.chart_event(
            bar,
            "mid_third_point",
            reason,
            object_id,
            anchor_bar_index=int(value["bar_index"]),
            price_i64=int(value["price_i64"]),
            details=details,
        )

    def center_continue_segment(
        new_segments: list[tuple[str, dict[str, Any]]],
    ) -> tuple[str, dict[str, Any]] | None:
        if context_center is None or context_direction is None or low_turn_bar_index is None:
            return None
        return_direction = "up" if context_direction == "down" else "down"
        zd_i64 = int(context_center["zd_i64"])
        zg_i64 = int(context_center["zg_i64"])
        values: list[tuple[str, dict[str, Any]]] = []
        for object_id, value in new_segments:
            if (
                value.get("confirmed") is not True
                or value.get("direction") != return_direction
                or int(value.get("start_bar_index", -1)) != low_turn_bar_index
            ):
                continue
            low_i64 = min(int(value["start_price_i64"]), int(value["end_price_i64"]))
            high_i64 = max(int(value["start_price_i64"]), int(value["end_price_i64"]))
            entered_core = high_i64 > zd_i64 if context_direction == "down" else low_i64 < zg_i64
            if entered_core:
                values.append((object_id, value))
        if not values:
            return None
        return min(values, key=lambda item: (int(item[1]["end_bar_index"]), item[0]))

    def classify_center_continue(bar: StrategyBar, object_id: str, value: dict[str, Any]) -> None:
        nonlocal phase, middle_source_id, middle_source_type
        assert context_center_id is not None
        phase = "MID_CENTER_CONTINUE"
        middle_source_id = object_id
        middle_source_type = "segment"
        price_i64 = int(value["end_price_i64"])
        details = {
            **graph_details,
            "source_center_id": context_center_id,
            "middle_branch": "center_continue",
            "position_action": "hold" if position_side != "flat" else "wait",
        }
        output.transition(
            bar,
            "MID_CENTER_CONTINUE",
            "MIDDLE_LEVEL_RETURN_REENTERED_SOURCE_CENTER",
            object_id,
            anchor_bar_index=int(value["end_bar_index"]),
            price_i64=price_i64,
            details=details,
        )
        output.chart_event(
            bar,
            "mid_center_continue",
            "MIDDLE_LEVEL_RETURN_REENTERED_SOURCE_CENTER",
            object_id,
            anchor_bar_index=int(value["end_bar_index"]),
            price_i64=price_i64,
            details=details,
        )

    def high_change_candidate(
        new_movements: list[tuple[str, dict[str, Any]]],
    ) -> tuple[str, dict[str, Any]] | None:
        if (
            context_center is None
            or context_center_id is None
            or context_direction is None
            or low_turn_bar_index is None
            or middle_completion_bar_index is None
        ):
            return None
        expected_state = f"centre_migration_{context_direction}"
        values = [
            (object_id, value)
            for object_id, value in new_movements
            if value.get("confirmed") is True
            and value.get("analysis_level", "segment") == "segment"
            and value.get("state_type") == expected_state
            and value.get("reference_object_id") != context_center_id
            and int(value.get("start_bar_index", -1)) == int(context_center["end_bar_index"])
            and int(value.get("end_bar_index", -1)) > middle_completion_bar_index
        ]
        if not values:
            return None
        return min(values, key=lambda item: (int(item[1]["end_bar_index"]), item[0]))

    def classify_high_change(bar: StrategyBar, object_id: str, value: dict[str, Any]) -> None:
        nonlocal phase, high_source_id
        assert context_center_id is not None and context_direction is not None
        phase = "HIGH_CHANGE_CANDIDATE"
        high_source_id = object_id
        details = {
            **graph_details,
            "source_center_id": context_center_id,
            "middle_branch": "third_point",
            "candidate_direction": context_direction,
            "high_change_status": "candidate",
        }
        output.transition(
            bar,
            "HIGH_CHANGE_CANDIDATE",
            "MIDDLE_LEVEL_MIGRATION_COMPLETED_HIGH_CHANGE_CANDIDATE",
            object_id,
            anchor_bar_index=int(value["end_bar_index"]),
            price_i64=int(value["price_i64"]),
            details=details,
        )
        output.chart_event(
            bar,
            "high_change_candidate",
            "MIDDLE_LEVEL_MIGRATION_COMPLETED_HIGH_CHANGE_CANDIDATE",
            object_id,
            anchor_bar_index=int(value["end_bar_index"]),
            price_i64=int(value["price_i64"]),
            details=details,
        )

    def advance_context(
        bar: StrategyBar,
        new_segments: list[tuple[str, dict[str, Any]]],
        new_divergences: list[tuple[str, dict[str, Any]]],
        new_points: list[tuple[str, dict[str, Any]]],
        new_movements: list[tuple[str, dict[str, Any]]],
    ) -> None:
        nonlocal phase
        if phase == "WAIT_LOW_TURN":
            low_candidate = low_turn_candidate(new_divergences, new_points)
            if low_candidate is not None:
                activate_low_turn(bar, *low_candidate)
        if phase == "LOW_TURN_ACTIVE":
            third_point = middle_third_point(new_points)
            if third_point is not None:
                classify_middle_third(bar, *third_point)
            else:
                continuation = center_continue_segment(new_segments)
                if continuation is not None:
                    classify_center_continue(bar, *continuation)
        if phase == "MID_THIRD_POINT":
            high_candidate = high_change_candidate(new_movements)
            if high_candidate is not None:
                classify_high_change(bar, *high_candidate)

    for bar in feed.bars:
        if bar.bar_index % 256 == 0 and cancelled.is_set():
            raise InterruptedError("strategy execution cancelled")
        new_centers: list[tuple[str, dict[str, Any]]] = []
        new_segments: list[tuple[str, dict[str, Any]]] = []
        new_divergences: list[tuple[str, dict[str, Any]]] = []
        new_points: list[tuple[str, dict[str, Any]]] = []
        new_movements: list[tuple[str, dict[str, Any]]] = []
        invalidated_dependencies: set[str] = set()
        targets = {
            "segment": segments,
            "segment_zhongshu": centers,
            "movement_state": movements,
            "divergence": divergences,
            "trade_point": trade_points,
        }
        new_values = {
            "segment": new_segments,
            "segment_zhongshu": new_centers,
            "movement_state": new_movements,
            "divergence": new_divergences,
            "trade_point": new_points,
        }
        for event in feed.events_by_bar.get(bar.bar_index, []):
            target = targets[event.object_type]
            dependency = (
                event.object_id == context_center_id
                or event.object_id == low_turn_id
                or event.object_id == middle_source_id
                or event.object_id == high_source_id
            )
            if event.operation == "delete":
                target.pop(event.object_id, None)
                if dependency:
                    invalidated_dependencies.add(event.object_id)
                continue
            value = json.loads(event.payload_json)
            previous = target.get(event.object_id)
            target[event.object_id] = value
            changed = previous is None
            if previous is not None:
                if event.object_type == "segment_zhongshu":
                    changed = center_signature(previous) != center_signature(value)
                else:
                    changed = source_signature(event.object_type, previous) != source_signature(
                        event.object_type, value
                    )
                if dependency and changed:
                    invalidated_dependencies.add(event.object_id)
            if changed:
                new_values[event.object_type].append((event.object_id, value))

        if invalidated_dependencies:
            reset_context(
                bar,
                "THREE_LEVEL_SOURCE_FACT_REVISED",
                sorted(invalidated_dependencies)[0],
            )

        advanced_context_id = context_center_id
        if advanced_context_id is not None:
            advance_context(
                bar,
                new_segments,
                new_divergences,
                new_points,
                new_movements,
            )

        context_candidates = [
            (object_id, value)
            for object_id, value in new_centers
            if valid_middle_context(value)
            and (object_id, center_signature(value)) not in activated_context_signatures
        ]
        context_candidates.sort(
            key=lambda item: (
                int(item[1]["end_bar_index"]),
                int(item[1]["start_bar_index"]),
                item[0],
            )
        )
        if context_candidates:
            object_id, value = context_candidates[-1]
            if context_center_id is not None and object_id != context_center_id:
                reset_context(
                    bar,
                    "NEW_MIDDLE_LEVEL_CONTEXT_SUPERSEDED_ACTIVE_CLASSIFICATION",
                    object_id,
                )
            if context_center_id is None:
                activate_context(bar, object_id, value)

        if context_center_id is not None and context_center_id != advanced_context_id:
            advance_context(
                bar,
                new_segments,
                new_divergences,
                new_points,
                new_movements,
            )

    return output.result(feed.bars)


def _run_target_level_rebound_segmented_operation(
    payload: dict[str, Any],
    guard: PathGuard,
    cancelled: threading.Event,
    *,
    last_bar_index: int | None,
) -> StrategyRun:
    """Run ALG-STR-008 over confirmed target-turn and one-lower-level objects."""
    parameters = payload["parameters"]
    profile_id = parameters["level_graph_profile_id"]
    operation_quantity = parameters["operation_quantity"]
    partial_quantity = parameters["partial_take_profit_quantity"]
    if isinstance(profile_id, bool) or profile_id != 1:
        raise ValueError("level_graph_profile_id must be 1")
    if (
        isinstance(operation_quantity, bool)
        or not isinstance(operation_quantity, int)
        or not 2 <= operation_quantity <= 100
    ):
        raise ValueError("operation_quantity must be an integer between 2 and 100")
    if (
        isinstance(partial_quantity, bool)
        or not isinstance(partial_quantity, int)
        or not 1 <= partial_quantity < operation_quantity
    ):
        raise ValueError("partial_take_profit_quantity must be less than operation_quantity")
    for name in ("allow_long", "allow_short", "execution_available"):
        if not isinstance(parameters[name], bool):
            raise ValueError(f"{name} must be a boolean")
    for name in ("estimated_round_trip_cost_i64", "minimum_net_segment_i64"):
        value = parameters[name]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{name} must be a non-negative integer")

    allow_long = parameters["allow_long"]
    allow_short = parameters["allow_short"]
    execution_available = parameters["execution_available"]
    required_gross_i64 = (
        parameters["estimated_round_trip_cost_i64"] + parameters["minimum_net_segment_i64"]
    )
    feed = _load_chan_strategy_feed(
        payload,
        guard,
        cancelled,
        last_bar_index=last_bar_index,
        object_types={"segment", "segment_zhongshu", "divergence", "trade_point"},
    )
    output = _CausalStrategyOutput("RBS", "WAIT_TARGET_LEVEL_TURN")
    graph_details = {
        "level_graph_profile_id": 1,
        "level_graph_profile": "segment_rebound_rhythm_v1",
        "target_level": "confirmed_segment_rebound_or_callback",
        "execution_level": "confirmed_segment",
        "assume_second_directional_leg_new_extreme": False,
    }
    segments: dict[str, dict[str, Any]] = {}
    centers: dict[str, dict[str, Any]] = {}
    divergences: dict[str, dict[str, Any]] = {}
    trade_points: dict[str, dict[str, Any]] = {}
    consumed_turn_ids: set[str] = set()

    source_turn_id: str | None = None
    source_turn: dict[str, Any] | None = None
    target_direction: str | None = None
    position_side = "flat"
    position_quantity = 0
    participating = False
    phase = "WAIT_TARGET_LEVEL_TURN"
    first_leg_id: str | None = None
    first_leg: dict[str, Any] | None = None
    counter_leg_id: str | None = None
    counter_leg: dict[str, Any] | None = None
    target_center_id: str | None = None
    target_center: dict[str, Any] | None = None
    third_point_id: str | None = None
    return_segment_id: str | None = None
    departure_segment_id: str | None = None
    departure_extreme_i64: int | None = None
    followthrough_segment_id: str | None = None

    def segment_signature(value: dict[str, Any]) -> tuple[Any, ...]:
        return tuple(
            value.get(name)
            for name in (
                "start_bar_index",
                "end_bar_index",
                "start_price_i64",
                "end_price_i64",
                "direction",
                "confirmed",
                "confirmed_at_bar_index",
            )
        )

    def center_identity_signature(value: dict[str, Any]) -> tuple[Any, ...]:
        return tuple(
            value.get(name)
            for name in (
                "start_bar_index",
                "zd_i64",
                "zg_i64",
                "analysis_level",
                "component_kind",
                "confirmed",
                "confirmed_at_bar_index",
            )
        )

    def signal_signature(value: dict[str, Any]) -> tuple[Any, ...]:
        return tuple(
            value.get(name)
            for name in (
                "bar_index",
                "price_i64",
                "signal_type",
                "signal_class",
                "divergence_kind",
                "reference_object_id",
                "confirmed",
                "confirmed_at_bar_index",
            )
        )

    def clear_context() -> None:
        nonlocal source_turn_id, source_turn, target_direction
        nonlocal position_side, position_quantity, participating, phase
        nonlocal first_leg_id, first_leg, counter_leg_id, counter_leg
        nonlocal target_center_id, target_center, third_point_id
        nonlocal return_segment_id, departure_segment_id, departure_extreme_i64
        nonlocal followthrough_segment_id
        source_turn_id = None
        source_turn = None
        target_direction = None
        position_side = "flat"
        position_quantity = 0
        participating = False
        phase = "WAIT_TARGET_LEVEL_TURN"
        first_leg_id = None
        first_leg = None
        counter_leg_id = None
        counter_leg = None
        target_center_id = None
        target_center = None
        third_point_id = None
        return_segment_id = None
        departure_segment_id = None
        departure_extreme_i64 = None
        followthrough_segment_id = None

    def close_position(
        bar: StrategyBar,
        stage_id: str,
        reason: str,
        source_id: str,
        *,
        anchor_bar_index: int,
        price_i64: int,
    ) -> None:
        nonlocal position_side, position_quantity
        if position_quantity > 0:
            output.trade(
                bar,
                "close_long" if position_side == "long" else "close_short",
                position_quantity,
                reason,
                source_id,
                anchor_bar_index=anchor_bar_index,
                price_i64=price_i64,
                stage_id=stage_id,
                details=graph_details,
            )
        position_side = "flat"
        position_quantity = 0

    def exit_context(
        bar: StrategyBar,
        reason: str,
        source_id: str,
        *,
        anchor_bar_index: int,
        price_i64: int,
        event_type: str = "segmented_operation_exit",
        state: str = "SEGMENTED_OPERATION_EXITED",
        details: dict[str, Any] | None = None,
    ) -> None:
        stage_id = output.transition(
            bar,
            state,
            reason,
            source_id,
            anchor_bar_index=anchor_bar_index,
            price_i64=price_i64,
            details={**graph_details, **(details or {})},
        )
        close_position(
            bar,
            stage_id,
            reason,
            source_id,
            anchor_bar_index=anchor_bar_index,
            price_i64=price_i64,
        )
        output.chart_event(
            bar,
            event_type,
            reason,
            source_id,
            anchor_bar_index=anchor_bar_index,
            price_i64=price_i64,
            details={**graph_details, **(details or {})},
        )
        clear_context()

    def standard_turn(value: dict[str, Any]) -> bool:
        return (
            value.get("confirmed") is True
            and value.get("signal_class") == "standard"
            and value.get("signal_type") in {"buy_1", "sell_1"}
        )

    def activate_turn(bar: StrategyBar, object_id: str, value: dict[str, Any]) -> None:
        nonlocal source_turn_id, source_turn, target_direction
        nonlocal position_side, position_quantity, participating, phase
        consumed_turn_ids.add(object_id)
        source_turn_id = object_id
        source_turn = value
        target_direction = "up" if value["signal_type"] == "buy_1" else "down"
        desired_side = "long" if target_direction == "up" else "short"
        direction_allowed = allow_long if desired_side == "long" else allow_short
        participating = execution_available and direction_allowed
        position_side = desired_side if participating else "flat"
        position_quantity = operation_quantity if participating else 0
        phase = "TARGET_REBOUND_ACTIVE" if target_direction == "up" else "TARGET_CALLBACK_ACTIVE"
        reason = (
            "TARGET_LEVEL_STANDARD_B1_CONFIRMED"
            if target_direction == "up"
            else "TARGET_LEVEL_STANDARD_S1_CONFIRMED"
        )
        if not execution_available:
            reason = "SEGMENTED_EXECUTION_UNAVAILABLE"
        elif not direction_allowed:
            reason = "SEGMENTED_DIRECTION_DISABLED"
        endpoint = int(value["bar_index"])
        price_i64 = int(value["price_i64"])
        details = {
            **graph_details,
            "target_direction": target_direction,
            "operation_quantity": operation_quantity,
            "partial_take_profit_quantity": partial_quantity,
            "max_participation_quantity": position_quantity,
        }
        stage_id = output.transition(
            bar,
            phase,
            reason,
            object_id,
            anchor_bar_index=endpoint,
            price_i64=price_i64,
            details=details,
        )
        if participating:
            output.trade(
                bar,
                "open_long" if desired_side == "long" else "open_short",
                operation_quantity,
                reason,
                object_id,
                anchor_bar_index=endpoint,
                price_i64=price_i64,
                stage_id=stage_id,
                details=details,
            )
        output.chart_event(
            bar,
            "rebound_started" if target_direction == "up" else "callback_started",
            reason,
            object_id,
            anchor_bar_index=endpoint,
            price_i64=price_i64,
            details=details,
        )

    def first_directional_leg(
        new_segments: list[tuple[str, dict[str, Any]]],
    ) -> tuple[str, dict[str, Any]] | None:
        if source_turn is None or target_direction is None:
            return None
        origin = int(source_turn["bar_index"])
        values = [
            (object_id, value)
            for object_id, value in new_segments
            if value.get("confirmed") is True
            and value.get("direction") == target_direction
            and int(value.get("start_bar_index", -1)) == origin
            and int(value.get("end_bar_index", -1)) > origin
        ]
        if not values:
            return None
        return min(values, key=lambda item: (int(item[1]["end_bar_index"]), item[0]))

    def take_first_leg_profit(bar: StrategyBar, object_id: str, value: dict[str, Any]) -> None:
        nonlocal first_leg_id, first_leg, phase, position_quantity
        assert source_turn is not None and target_direction is not None
        first_leg_id = object_id
        first_leg = value
        endpoint = int(value["end_bar_index"])
        price_i64 = int(value["end_price_i64"])
        gross_i64 = abs(price_i64 - int(source_turn["price_i64"]))
        if gross_i64 < required_gross_i64:
            exit_context(
                bar,
                "FIRST_LEG_UNFAVORABLE_AFTER_ESTIMATED_COST",
                object_id,
                anchor_bar_index=endpoint,
                price_i64=price_i64,
                event_type="unfavorable_execution_exit",
                details={
                    "first_leg_gross_i64": gross_i64,
                    "required_gross_i64": required_gross_i64,
                },
            )
            return
        phase = "FIRST_LEG_PARTIAL_TAKE_PROFIT"
        details = {
            **graph_details,
            "target_direction": target_direction,
            "first_leg_id": object_id,
            "first_leg_gross_i64": gross_i64,
            "required_gross_i64": required_gross_i64,
            "executed_quantity": partial_quantity if position_quantity else 0,
            "retained_quantity": (position_quantity - partial_quantity if position_quantity else 0),
        }
        stage_id = output.transition(
            bar,
            phase,
            "FIRST_EXECUTION_LEVEL_LEG_COMPLETED",
            object_id,
            anchor_bar_index=endpoint,
            price_i64=price_i64,
            details=details,
        )
        if position_quantity:
            output.trade(
                bar,
                "reduce_long" if position_side == "long" else "reduce_short",
                partial_quantity,
                "FIRST_EXECUTION_LEVEL_LEG_COMPLETED",
                object_id,
                anchor_bar_index=endpoint,
                price_i64=price_i64,
                stage_id=stage_id,
                details=details,
            )
            position_quantity -= partial_quantity
        output.chart_event(
            bar,
            "partial_take_profit",
            "FIRST_EXECUTION_LEVEL_LEG_COMPLETED",
            object_id,
            anchor_bar_index=endpoint,
            price_i64=price_i64,
            details=details,
        )

    def first_counter_leg(
        new_segments: list[tuple[str, dict[str, Any]]],
    ) -> tuple[str, dict[str, Any]] | None:
        if first_leg is None or target_direction is None:
            return None
        expected = "down" if target_direction == "up" else "up"
        start = int(first_leg["end_bar_index"])
        values = [
            (object_id, value)
            for object_id, value in new_segments
            if value.get("confirmed") is True
            and value.get("direction") == expected
            and int(value.get("start_bar_index", -1)) == start
            and int(value.get("end_bar_index", -1)) > start
        ]
        if not values:
            return None
        return min(values, key=lambda item: (int(item[1]["end_bar_index"]), item[0]))

    def reenter_after_counter_leg(bar: StrategyBar, object_id: str, value: dict[str, Any]) -> None:
        nonlocal counter_leg_id, counter_leg, phase, position_quantity
        assert source_turn is not None and target_direction is not None
        counter_leg_id = object_id
        counter_leg = value
        endpoint = int(value["end_bar_index"])
        price_i64 = int(value["end_price_i64"])
        source_price = int(source_turn["price_i64"])
        structure_broken = (
            price_i64 < source_price if target_direction == "up" else price_i64 > source_price
        )
        if structure_broken:
            exit_context(
                bar,
                "COUNTER_LEG_BROKE_SOURCE_TURN_EXTREME",
                object_id,
                anchor_bar_index=endpoint,
                price_i64=price_i64,
                details={"source_turn_price_i64": source_price},
            )
            return
        phase = "COUNTER_LEG_REENTERED"
        executed_quantity = partial_quantity if position_quantity else 0
        details = {
            **graph_details,
            "target_direction": target_direction,
            "counter_leg_id": object_id,
            "executed_quantity": executed_quantity,
            "position_quantity_after": (
                position_quantity + partial_quantity if position_quantity else 0
            ),
        }
        stage_id = output.transition(
            bar,
            phase,
            "FIRST_COUNTER_LEG_COMPLETED_WITHOUT_STRUCTURE_BREAK",
            object_id,
            anchor_bar_index=endpoint,
            price_i64=price_i64,
            details=details,
        )
        if position_quantity:
            output.trade(
                bar,
                "add_long" if position_side == "long" else "add_short",
                partial_quantity,
                "FIRST_COUNTER_LEG_COMPLETED_WITHOUT_STRUCTURE_BREAK",
                object_id,
                anchor_bar_index=endpoint,
                price_i64=price_i64,
                stage_id=stage_id,
                details=details,
            )
            position_quantity += partial_quantity
        output.chart_event(
            bar,
            "reenter",
            "FIRST_COUNTER_LEG_COMPLETED_WITHOUT_STRUCTURE_BREAK",
            object_id,
            anchor_bar_index=endpoint,
            price_i64=price_i64,
            details=details,
        )

    def resulting_center(
        new_centers: list[tuple[str, dict[str, Any]]],
    ) -> tuple[str, dict[str, Any]] | None:
        if source_turn is None or counter_leg is None:
            return None
        origin = int(source_turn["bar_index"])
        counter_end = int(counter_leg["end_bar_index"])
        values = [
            (object_id, value)
            for object_id, value in new_centers
            if value.get("confirmed") is True
            and value.get("analysis_level", "segment") == "segment"
            and value.get("component_kind", "segment") == "segment"
            and int(value.get("start_bar_index", -1)) >= origin
            and int(value.get("end_bar_index", -1)) >= counter_end
        ]
        if not values:
            return None
        return min(
            values,
            key=lambda item: (
                int(item[1]["end_bar_index"]),
                int(item[1]["start_bar_index"]),
                item[0],
            ),
        )

    def register_center(bar: StrategyBar, object_id: str, value: dict[str, Any]) -> None:
        nonlocal target_center_id, target_center, phase
        target_center_id = object_id
        target_center = value
        phase = "TARGET_CENTER_CONFIRMED"
        endpoint = int(value["end_bar_index"])
        price_i64 = int(value.get("z_i64", (int(value["zd_i64"]) + int(value["zg_i64"])) // 2))
        details = {
            **graph_details,
            "target_direction": target_direction,
            "target_center_id": object_id,
            "component_count": int(value.get("component_count", 3)),
        }
        output.transition(
            bar,
            phase,
            "FIRST_TARGET_LEVEL_CENTER_CONFIRMED",
            object_id,
            anchor_bar_index=endpoint,
            price_i64=price_i64,
            details=details,
        )
        output.chart_event(
            bar,
            "target_center_confirmed",
            "FIRST_TARGET_LEVEL_CENTER_CONFIRMED",
            object_id,
            anchor_bar_index=endpoint,
            price_i64=price_i64,
            details=details,
        )

    def segment_for_point(
        value: dict[str, Any], direction: str
    ) -> tuple[str, dict[str, Any]] | None:
        endpoint = int(value["bar_index"])
        price_i64 = int(value["price_i64"])
        candidates = [
            (object_id, segment)
            for object_id, segment in segments.items()
            if segment.get("confirmed") is True
            and segment.get("direction") == direction
            and int(segment.get("end_bar_index", -1)) == endpoint
        ]
        exact = [item for item in candidates if int(item[1].get("end_price_i64", 0)) == price_i64]
        values = exact or candidates
        if not values:
            return None
        return max(
            values,
            key=lambda item: (int(item[1].get("start_bar_index", -1)), item[0]),
        )

    def preceding_departure(
        returning: dict[str, Any], direction: str
    ) -> tuple[str, dict[str, Any]] | None:
        start = int(returning["start_bar_index"])
        values = [
            (object_id, segment)
            for object_id, segment in segments.items()
            if segment.get("confirmed") is True
            and segment.get("direction") == direction
            and int(segment.get("end_bar_index", -1)) == start
        ]
        if not values:
            return None
        return max(
            values,
            key=lambda item: (int(item[1].get("start_bar_index", -1)), item[0]),
        )

    def classify_third_point(
        bar: StrategyBar,
        new_points: list[tuple[str, dict[str, Any]]],
    ) -> None:
        nonlocal third_point_id, return_segment_id, departure_segment_id
        nonlocal departure_extreme_i64, phase
        assert target_center_id is not None and target_direction is not None
        desired = "buy_3" if target_direction == "up" else "sell_3"
        failure = "sell_3" if target_direction == "up" else "buy_3"
        matching = [
            (object_id, value)
            for object_id, value in new_points
            if value.get("confirmed") is True
            and value.get("signal_class") == "standard"
            and value.get("reference_object_id") == target_center_id
            and value.get("signal_type") in {desired, failure}
        ]
        matching.sort(
            key=lambda item: (
                int(item[1].get("bar_index", -1)),
                0 if item[1].get("signal_type") == failure else 1,
                item[0],
            )
        )
        if not matching:
            return
        object_id, value = matching[0]
        endpoint = int(value["bar_index"])
        price_i64 = int(value["price_i64"])
        if value["signal_type"] == failure:
            exit_context(
                bar,
                "OPPOSITE_THIRD_POINT_INVALIDATED_SEGMENTED_OPERATION",
                object_id,
                anchor_bar_index=endpoint,
                price_i64=price_i64,
                details={"target_center_id": target_center_id},
            )
            return
        return_direction = "down" if target_direction == "up" else "up"
        returning = segment_for_point(value, return_direction)
        departure = (
            None if returning is None else preceding_departure(returning[1], target_direction)
        )
        if returning is None or departure is None:
            exit_context(
                bar,
                "THIRD_POINT_OBJECT_CHAIN_MISSING",
                object_id,
                anchor_bar_index=endpoint,
                price_i64=price_i64,
                event_type="segmented_operation_dependency_exit",
            )
            return
        third_point_id = object_id
        return_segment_id = returning[0]
        departure_segment_id = departure[0]
        departure_extreme_i64 = (
            max(
                int(departure[1]["start_price_i64"]),
                int(departure[1]["end_price_i64"]),
            )
            if target_direction == "up"
            else min(
                int(departure[1]["start_price_i64"]),
                int(departure[1]["end_price_i64"]),
            )
        )
        phase = "WAIT_TREND_FOLLOWTHROUGH"
        details = {
            **graph_details,
            "target_center_id": target_center_id,
            "third_point_id": object_id,
            "return_segment_id": return_segment_id,
            "departure_segment_id": departure_segment_id,
            "departure_extreme_i64": departure_extreme_i64,
        }
        output.transition(
            bar,
            phase,
            "FIRST_CENTER_THIRD_POINT_CONFIRMED_WAIT_FOLLOWTHROUGH",
            object_id,
            anchor_bar_index=endpoint,
            price_i64=price_i64,
            details=details,
        )
        output.chart_event(
            bar,
            "trend_handoff_wait",
            "FIRST_CENTER_THIRD_POINT_CONFIRMED_WAIT_FOLLOWTHROUGH",
            object_id,
            anchor_bar_index=endpoint,
            price_i64=price_i64,
            details=details,
        )

    def first_followthrough(
        new_segments: list[tuple[str, dict[str, Any]]],
    ) -> tuple[str, dict[str, Any]] | None:
        if third_point_id is None or target_direction is None:
            return None
        point = trade_points.get(third_point_id)
        if point is None:
            return None
        start = int(point["bar_index"])
        values = [
            (object_id, value)
            for object_id, value in new_segments
            if value.get("confirmed") is True
            and value.get("direction") == target_direction
            and int(value.get("start_bar_index", -1)) == start
            and int(value.get("end_bar_index", -1)) > start
        ]
        if not values:
            return None
        return min(values, key=lambda item: (int(item[1]["end_bar_index"]), item[0]))

    def classify_followthrough(bar: StrategyBar, object_id: str, value: dict[str, Any]) -> None:
        nonlocal followthrough_segment_id, phase
        assert target_direction is not None and departure_extreme_i64 is not None
        followthrough_segment_id = object_id
        endpoint = int(value["end_bar_index"])
        price_i64 = int(value["end_price_i64"])
        extreme = (
            max(int(value["start_price_i64"]), price_i64)
            if target_direction == "up"
            else min(int(value["start_price_i64"]), price_i64)
        )
        made_new_extreme = (
            extreme > departure_extreme_i64
            if target_direction == "up"
            else extreme < departure_extreme_i64
        )
        expected_divergence = "top_divergence" if target_direction == "up" else "bottom_divergence"
        terminal_divergence = next(
            (
                (divergence_id, divergence)
                for divergence_id, divergence in divergences.items()
                if divergence.get("confirmed") is True
                and divergence.get("signal_type") == expected_divergence
                and int(divergence.get("bar_index", -1)) == endpoint
            ),
            None,
        )
        if not made_new_extreme:
            exit_context(
                bar,
                "THIRD_POINT_FOLLOWTHROUGH_FAILED_NEW_EXTREME",
                object_id,
                anchor_bar_index=endpoint,
                price_i64=price_i64,
                details={"departure_extreme_i64": departure_extreme_i64},
            )
            return
        if terminal_divergence is not None:
            divergence_id, divergence = terminal_divergence
            exit_context(
                bar,
                "THIRD_POINT_FOLLOWTHROUGH_DIVERGENCE",
                divergence_id,
                anchor_bar_index=endpoint,
                price_i64=int(divergence.get("price_i64", price_i64)),
                details={"followthrough_segment_id": object_id},
            )
            return
        phase = "TREND_HANDOFF"
        details = {
            **graph_details,
            "target_direction": target_direction,
            "target_center_id": target_center_id,
            "third_point_id": third_point_id,
            "followthrough_segment_id": object_id,
            "position_action": "hold" if position_quantity else "observe",
        }
        output.transition(
            bar,
            phase,
            "FIRST_CENTER_THIRD_POINT_NONDIVERGENT_TREND_HANDOFF",
            object_id,
            anchor_bar_index=endpoint,
            price_i64=price_i64,
            details=details,
        )
        output.chart_event(
            bar,
            "trend_handoff",
            "FIRST_CENTER_THIRD_POINT_NONDIVERGENT_TREND_HANDOFF",
            object_id,
            anchor_bar_index=endpoint,
            price_i64=price_i64,
            details=details,
        )

    def advance_context(
        bar: StrategyBar,
        new_segments: list[tuple[str, dict[str, Any]]],
        new_centers: list[tuple[str, dict[str, Any]]],
        new_divergences: list[tuple[str, dict[str, Any]]],
        new_points: list[tuple[str, dict[str, Any]]],
    ) -> None:
        nonlocal phase
        if source_turn is None:
            return
        opposite = "sell_1" if target_direction == "up" else "buy_1"
        opposite_turn = next(
            (
                (object_id, value)
                for object_id, value in new_points
                if standard_turn(value) and value.get("signal_type") == opposite
            ),
            None,
        )
        if opposite_turn is not None:
            object_id, value = opposite_turn
            exit_context(
                bar,
                "OPPOSITE_TARGET_LEVEL_FIRST_POINT_CONFIRMED",
                object_id,
                anchor_bar_index=int(value["bar_index"]),
                price_i64=int(value["price_i64"]),
            )
            return
        if phase in {"TARGET_REBOUND_ACTIVE", "TARGET_CALLBACK_ACTIVE"}:
            candidate = first_directional_leg(new_segments)
            if candidate is not None:
                take_first_leg_profit(bar, *candidate)
        if source_turn is None:
            return
        if phase == "FIRST_LEG_PARTIAL_TAKE_PROFIT":
            candidate = first_counter_leg(new_segments)
            if candidate is not None:
                reenter_after_counter_leg(bar, *candidate)
        if source_turn is None:
            return
        if phase == "COUNTER_LEG_REENTERED":
            candidate = resulting_center(new_centers)
            if candidate is not None:
                register_center(bar, *candidate)
        if source_turn is None:
            return
        if phase == "TARGET_CENTER_CONFIRMED":
            classify_third_point(bar, new_points)
        if source_turn is None:
            return
        if phase == "WAIT_TREND_FOLLOWTHROUGH":
            candidate = first_followthrough(new_segments)
            if candidate is not None:
                classify_followthrough(bar, *candidate)
        if source_turn is None:
            return
        if phase == "TREND_HANDOFF" and followthrough_segment_id is not None:
            followthrough = segments.get(followthrough_segment_id)
            expected_divergence = (
                "top_divergence" if target_direction == "up" else "bottom_divergence"
            )
            terminal_divergence = next(
                (
                    (object_id, value)
                    for object_id, value in new_divergences
                    if value.get("confirmed") is True
                    and value.get("signal_type") == expected_divergence
                    and followthrough is not None
                    and int(value.get("bar_index", -1)) == int(followthrough["end_bar_index"])
                ),
                None,
            )
            if terminal_divergence is not None:
                object_id, value = terminal_divergence
                exit_context(
                    bar,
                    "TREND_HANDOFF_FOLLOWTHROUGH_DIVERGENCE_CONFIRMED",
                    object_id,
                    anchor_bar_index=int(value["bar_index"]),
                    price_i64=int(value.get("price_i64", bar.close_i64)),
                    details={"followthrough_segment_id": followthrough_segment_id},
                )

    for bar in feed.bars:
        if bar.bar_index % 256 == 0 and cancelled.is_set():
            raise InterruptedError("strategy execution cancelled")
        new_segments: list[tuple[str, dict[str, Any]]] = []
        new_centers: list[tuple[str, dict[str, Any]]] = []
        new_divergences: list[tuple[str, dict[str, Any]]] = []
        new_points: list[tuple[str, dict[str, Any]]] = []
        invalidated_dependencies: set[str] = set()
        targets = {
            "segment": segments,
            "segment_zhongshu": centers,
            "divergence": divergences,
            "trade_point": trade_points,
        }
        new_values = {
            "segment": new_segments,
            "segment_zhongshu": new_centers,
            "divergence": new_divergences,
            "trade_point": new_points,
        }
        dependencies = {
            value
            for value in (
                source_turn_id,
                first_leg_id,
                counter_leg_id,
                target_center_id,
                third_point_id,
                return_segment_id,
                departure_segment_id,
                followthrough_segment_id,
            )
            if value is not None
        }
        for event in feed.events_by_bar.get(bar.bar_index, []):
            target = targets[event.object_type]
            if event.operation == "delete":
                target.pop(event.object_id, None)
                if event.object_id in dependencies:
                    invalidated_dependencies.add(event.object_id)
                continue
            value = json.loads(event.payload_json)
            previous = target.get(event.object_id)
            target[event.object_id] = value
            if previous is None:
                changed = True
            elif event.object_type == "segment":
                changed = segment_signature(previous) != segment_signature(value)
            elif event.object_type == "segment_zhongshu":
                changed = center_identity_signature(previous) != center_identity_signature(value)
            else:
                changed = signal_signature(previous) != signal_signature(value)
            if event.object_id == target_center_id and event.object_type == "segment_zhongshu":
                target_center = value
            if changed:
                new_values[event.object_type].append((event.object_id, value))
                if event.object_id in dependencies and previous is not None:
                    invalidated_dependencies.add(event.object_id)

        if source_turn is not None and invalidated_dependencies:
            exit_context(
                bar,
                "SEGMENTED_OPERATION_SOURCE_FACT_REVISED",
                sorted(invalidated_dependencies)[0],
                anchor_bar_index=bar.bar_index,
                price_i64=bar.close_i64,
                event_type="segmented_operation_reset",
                state="SEGMENTED_OPERATION_RESET",
            )

        if source_turn is not None:
            advance_context(bar, new_segments, new_centers, new_divergences, new_points)

        if (
            source_turn is not None
            and phase == "TARGET_CENTER_CONFIRMED"
            and target_center_id is not None
        ):
            superseding = next(
                (
                    (object_id, value)
                    for object_id, value in new_centers
                    if object_id != target_center_id
                    and value.get("confirmed") is True
                    and int(value.get("start_bar_index", -1))
                    > int(target_center.get("start_bar_index", -1) if target_center else -1)
                ),
                None,
            )
            if superseding is not None:
                object_id, value = superseding
                exit_context(
                    bar,
                    "NEW_TARGET_CENTER_WITHOUT_FIRST_CENTER_THIRD_POINT",
                    object_id,
                    anchor_bar_index=int(value["end_bar_index"]),
                    price_i64=int(
                        value.get(
                            "z_i64",
                            (int(value["zd_i64"]) + int(value["zg_i64"])) // 2,
                        )
                    ),
                )

        if source_turn is None:
            candidates = [
                (object_id, value)
                for object_id, value in new_points
                if object_id not in consumed_turn_ids and standard_turn(value)
            ]
            candidates.sort(key=lambda item: (int(item[1]["bar_index"]), item[0]))
            if candidates:
                activate_turn(bar, *candidates[0])
                advance_context(bar, new_segments, new_centers, new_divergences, new_points)

    return output.result(feed.bars)


def _run_bottom_top_construction(
    payload: dict[str, Any],
    guard: PathGuard,
    cancelled: threading.Event,
    *,
    last_bar_index: int | None,
) -> StrategyRun:
    """Run ALG-STR-009 as precise and coarse causal construction state machines."""
    parameters = payload["parameters"]
    profile_id = parameters["level_graph_profile_id"]
    operation_quantity = parameters["operation_quantity"]
    coarse_hold_bars = parameters["coarse_effective_hold_bars"]
    if isinstance(profile_id, bool) or profile_id != 1:
        raise ValueError("level_graph_profile_id must be 1")
    if (
        isinstance(operation_quantity, bool)
        or not isinstance(operation_quantity, int)
        or not 1 <= operation_quantity <= 100
    ):
        raise ValueError("operation_quantity must be an integer between 1 and 100")
    if (
        isinstance(coarse_hold_bars, bool)
        or not isinstance(coarse_hold_bars, int)
        or not 1 <= coarse_hold_bars <= 20
    ):
        raise ValueError("coarse_effective_hold_bars must be an integer between 1 and 20")
    for name in ("allow_long", "allow_short", "execution_available"):
        if not isinstance(parameters[name], bool):
            raise ValueError(f"{name} must be a boolean")

    feed = _load_chan_strategy_feed(
        payload,
        guard,
        cancelled,
        last_bar_index=last_bar_index,
        object_types={"fractal", "segment_zhongshu", "trade_point"},
    )
    output = _CausalStrategyOutput("BTC", "WAIT_BOTTOM_TOP_CONSTRUCTION")
    graph_details = {
        "level_graph_profile_id": 1,
        "level_graph_profile": "segment_bottom_top_build_v1",
        "precise_source_level": "confirmed_standard_first_point",
        "resulting_center_level": "confirmed_segment_zhongshu",
        "coarse_zone_profile": "processed_fractal_zone_v1",
        "coarse_effective_hold_bars": coarse_hold_bars,
        "coarse_zone_executes_trade": False,
    }

    fractals: dict[str, dict[str, Any]] = {}
    centers: dict[str, dict[str, Any]] = {}
    trade_points: dict[str, dict[str, Any]] = {}
    consumed_turn_ids: set[str] = set()

    source_turn_id: str | None = None
    source_turn: dict[str, Any] | None = None
    construction_side: str | None = None
    resulting_center_id: str | None = None
    resulting_center: dict[str, Any] | None = None
    result_point_id: str | None = None
    position_side = "flat"
    position_quantity = 0
    phase = "WAIT_BOTTOM_TOP_CONSTRUCTION"

    coarse_source_id: str | None = None
    coarse_source: dict[str, Any] | None = None
    coarse_known_at: int | None = None
    coarse_standing_count = 0

    def signal_signature(value: dict[str, Any]) -> tuple[Any, ...]:
        return tuple(
            value.get(name)
            for name in (
                "bar_index",
                "price_i64",
                "signal_type",
                "signal_class",
                "reference_object_id",
                "confirmed",
                "confirmed_at_bar_index",
            )
        )

    def center_identity_signature(value: dict[str, Any]) -> tuple[Any, ...]:
        return tuple(
            value.get(name)
            for name in (
                "start_bar_index",
                "zd_i64",
                "zg_i64",
                "analysis_level",
                "component_kind",
                "confirmed",
                "confirmed_at_bar_index",
            )
        )

    def fractal_signature(value: dict[str, Any]) -> tuple[Any, ...]:
        return tuple(
            value.get(name)
            for name in (
                "bar_index",
                "price_i64",
                "zone_low_i64",
                "zone_high_i64",
                "fractal_type",
                "confirmed",
                "confirmed_at_bar_index",
            )
        )

    def standard_first_point(value: dict[str, Any]) -> bool:
        return (
            value.get("confirmed") is True
            and value.get("signal_class") == "standard"
            and value.get("signal_type") in {"buy_1", "sell_1"}
        )

    def clear_precise() -> None:
        nonlocal source_turn_id, source_turn, construction_side
        nonlocal resulting_center_id, resulting_center, result_point_id
        nonlocal position_side, position_quantity, phase
        source_turn_id = None
        source_turn = None
        construction_side = None
        resulting_center_id = None
        resulting_center = None
        result_point_id = None
        position_side = "flat"
        position_quantity = 0
        phase = "WAIT_BOTTOM_TOP_CONSTRUCTION"

    def clear_coarse() -> None:
        nonlocal coarse_source_id, coarse_source, coarse_known_at, coarse_standing_count
        coarse_source_id = None
        coarse_source = None
        coarse_known_at = None
        coarse_standing_count = 0

    def close_precise_position(
        bar: StrategyBar,
        stage_id: str,
        reason: str,
        source_id: str,
        *,
        anchor_bar_index: int,
        price_i64: int,
    ) -> None:
        nonlocal position_side, position_quantity
        if position_quantity > 0:
            output.trade(
                bar,
                "close_long" if position_side == "long" else "close_short",
                position_quantity,
                reason,
                source_id,
                anchor_bar_index=anchor_bar_index,
                price_i64=price_i64,
                stage_id=stage_id,
                details={**graph_details, "construction_layer": "precise"},
            )
        position_side = "flat"
        position_quantity = 0

    def reset_precise(
        bar: StrategyBar,
        reason: str,
        source_id: str,
        *,
        event_type: str = "bottom_top_construction_reset",
    ) -> None:
        stage_id = output.transition(
            bar,
            "BOTTOM_TOP_CONSTRUCTION_RESET",
            reason,
            source_id,
            details={**graph_details, "construction_layer": "precise"},
        )
        close_precise_position(
            bar,
            stage_id,
            reason,
            source_id,
            anchor_bar_index=bar.bar_index,
            price_i64=bar.close_i64,
        )
        output.chart_event(
            bar,
            event_type,
            reason,
            source_id,
            anchor_bar_index=bar.bar_index,
            price_i64=bar.close_i64,
            details={**graph_details, "construction_layer": "precise"},
        )
        clear_precise()

    def resolve_coarse(
        bar: StrategyBar,
        state: str,
        event_type: str,
        reason: str,
        source_id: str,
    ) -> None:
        assert coarse_source is not None
        low = int(coarse_source["zone_low_i64"])
        high = int(coarse_source["zone_high_i64"])
        details = {
            **graph_details,
            "construction_layer": "coarse",
            "fractal_type": coarse_source["fractal_type"],
            "zone_low_i64": low,
            "zone_high_i64": high,
        }
        output.transition(
            bar,
            state,
            reason,
            source_id,
            anchor_bar_index=int(coarse_source["bar_index"]),
            price_i64=int(coarse_source["price_i64"]),
            details=details,
        )
        output.chart_event(
            bar,
            event_type,
            reason,
            source_id,
            anchor_bar_index=int(coarse_source["bar_index"]),
            price_i64=int(coarse_source["price_i64"]),
            details=details,
        )
        clear_coarse()

    def activate_precise(bar: StrategyBar, object_id: str, value: dict[str, Any]) -> None:
        nonlocal source_turn_id, source_turn, construction_side, position_side
        nonlocal position_quantity, phase
        if coarse_source_id is not None:
            resolve_coarse(
                bar,
                "COARSE_CONSTRUCTION_SUPERSEDED",
                "coarse_construction_superseded",
                "STANDARD_FIRST_POINT_SUPERSEDED_COARSE_ZONE",
                coarse_source_id,
            )
        consumed_turn_ids.add(object_id)
        source_turn_id = object_id
        source_turn = value
        construction_side = "bottom" if value["signal_type"] == "buy_1" else "top"
        desired_position = "long" if construction_side == "bottom" else "short"
        allowed = (
            parameters["allow_long"] if desired_position == "long" else parameters["allow_short"]
        )
        participating = parameters["execution_available"] and allowed
        position_side = desired_position if participating else "flat"
        position_quantity = operation_quantity if participating else 0
        phase = "BOTTOM_BUILDING" if construction_side == "bottom" else "TOP_BUILDING"
        reason = (
            "STANDARD_B1_STARTED_BOTTOM_CONSTRUCTION"
            if construction_side == "bottom"
            else "STANDARD_S1_STARTED_TOP_CONSTRUCTION"
        )
        if not parameters["execution_available"]:
            reason = "CONSTRUCTION_EXECUTION_UNAVAILABLE"
        elif not allowed:
            reason = "CONSTRUCTION_DIRECTION_DISABLED"
        endpoint = int(value["bar_index"])
        price_i64 = int(value["price_i64"])
        details = {
            **graph_details,
            "construction_layer": "precise",
            "construction_side": construction_side,
            "operation_quantity": operation_quantity,
            "participating_quantity": position_quantity,
        }
        stage_id = output.transition(
            bar,
            phase,
            reason,
            object_id,
            anchor_bar_index=endpoint,
            price_i64=price_i64,
            details=details,
        )
        if participating:
            output.trade(
                bar,
                "open_long" if desired_position == "long" else "open_short",
                operation_quantity,
                reason,
                object_id,
                anchor_bar_index=endpoint,
                price_i64=price_i64,
                stage_id=stage_id,
                details=details,
            )
        output.chart_event(
            bar,
            "bottom_building" if construction_side == "bottom" else "top_building",
            reason,
            object_id,
            anchor_bar_index=endpoint,
            price_i64=price_i64,
            details=details,
        )

    def register_resulting_center(bar: StrategyBar, object_id: str, value: dict[str, Any]) -> None:
        nonlocal resulting_center_id, resulting_center, phase
        assert construction_side is not None
        resulting_center_id = object_id
        resulting_center = value
        phase = (
            "BOTTOM_RESULTING_CENTER_CONFIRMED"
            if construction_side == "bottom"
            else "TOP_RESULTING_CENTER_CONFIRMED"
        )
        price_i64 = int(value.get("z_i64", (int(value["zd_i64"]) + int(value["zg_i64"])) // 2))
        details = {
            **graph_details,
            "construction_layer": "precise",
            "construction_side": construction_side,
            "resulting_center_id": object_id,
        }
        output.transition(
            bar,
            phase,
            "FIRST_RESULTING_CENTER_CONFIRMED",
            object_id,
            anchor_bar_index=int(value["end_bar_index"]),
            price_i64=price_i64,
            details=details,
        )
        output.chart_event(
            bar,
            "bottom_resulting_center" if construction_side == "bottom" else "top_resulting_center",
            "FIRST_RESULTING_CENTER_CONFIRMED",
            object_id,
            anchor_bar_index=int(value["end_bar_index"]),
            price_i64=price_i64,
            details=details,
        )

    def classify_precise_result(
        bar: StrategyBar, new_points: list[tuple[str, dict[str, Any]]]
    ) -> None:
        nonlocal result_point_id, phase
        if resulting_center_id is None or construction_side is None:
            return
        success_type = "buy_3" if construction_side == "bottom" else "sell_3"
        failure_type = "sell_3" if construction_side == "bottom" else "buy_3"
        candidates = [
            (object_id, value)
            for object_id, value in new_points
            if value.get("confirmed") is True
            and value.get("signal_class") == "standard"
            and value.get("reference_object_id") == resulting_center_id
            and value.get("signal_type") in {success_type, failure_type}
        ]
        candidates.sort(
            key=lambda item: (
                int(item[1].get("bar_index", -1)),
                0 if item[1].get("signal_type") == failure_type else 1,
                item[0],
            )
        )
        if not candidates:
            return
        object_id, value = candidates[0]
        result_point_id = object_id
        success = value["signal_type"] == success_type
        side_label = "BOTTOM" if construction_side == "bottom" else "TOP"
        phase = f"{side_label}_BUILD_SUCCESS" if success else f"{side_label}_BUILD_FAILED"
        reason = (
            f"FIRST_RESULTING_CENTER_{success_type.upper()}_COMPLETED_{side_label}"
            if success
            else f"FIRST_RESULTING_CENTER_{failure_type.upper()}_FAILED_{side_label}"
        )
        endpoint = int(value["bar_index"])
        price_i64 = int(value["price_i64"])
        details = {
            **graph_details,
            "construction_layer": "precise",
            "construction_side": construction_side,
            "resulting_center_id": resulting_center_id,
            "result_point_id": object_id,
            "position_action": "hold" if success and position_quantity else "observe",
        }
        stage_id = output.transition(
            bar,
            phase,
            reason,
            object_id,
            anchor_bar_index=endpoint,
            price_i64=price_i64,
            details=details,
        )
        if not success:
            close_precise_position(
                bar,
                stage_id,
                reason,
                object_id,
                anchor_bar_index=endpoint,
                price_i64=price_i64,
            )
        output.chart_event(
            bar,
            (
                f"{construction_side}_build_success"
                if success
                else f"{construction_side}_build_failure"
            ),
            reason,
            object_id,
            anchor_bar_index=endpoint,
            price_i64=price_i64,
            details=details,
        )
        if not success:
            clear_precise()

    def advance_precise(
        bar: StrategyBar,
        new_centers: list[tuple[str, dict[str, Any]]],
        new_points: list[tuple[str, dict[str, Any]]],
    ) -> None:
        if source_turn is None or construction_side is None:
            return
        if resulting_center_id is None:
            origin = int(source_turn["bar_index"])
            candidates = [
                (object_id, value)
                for object_id, value in new_centers
                if value.get("confirmed") is True
                and value.get("analysis_level", "segment") == "segment"
                and value.get("component_kind", "segment") == "segment"
                and int(value.get("start_bar_index", -1)) == origin
                and int(value.get("end_bar_index", -1)) > origin
            ]
            candidates.sort(key=lambda item: (int(item[1]["end_bar_index"]), item[0]))
            if candidates:
                register_resulting_center(bar, *candidates[0])
        if phase in {"BOTTOM_RESULTING_CENTER_CONFIRMED", "TOP_RESULTING_CENTER_CONFIRMED"}:
            classify_precise_result(bar, new_points)

    def activate_coarse(bar: StrategyBar, object_id: str, value: dict[str, Any]) -> None:
        nonlocal coarse_source_id, coarse_source, coarse_known_at, coarse_standing_count
        if value.get("confirmed") is not True or value.get("fractal_type") not in {
            "bottom",
            "top",
        }:
            return
        low = value.get("zone_low_i64")
        high = value.get("zone_high_i64")
        if not isinstance(low, int) or not isinstance(high, int) or low > high:
            return
        if coarse_source_id is not None:
            resolve_coarse(
                bar,
                "COARSE_CONSTRUCTION_SUPERSEDED",
                "coarse_construction_superseded",
                "NEW_CONFIRMED_FRACTAL_SUPERSEDED_COARSE_ZONE",
                coarse_source_id,
            )
        coarse_source_id = object_id
        coarse_source = value
        coarse_known_at = bar.bar_index
        coarse_standing_count = 0
        kind = str(value["fractal_type"])
        state = "COARSE_BOTTOM_BUILDING" if kind == "bottom" else "COARSE_TOP_BUILDING"
        reason = (
            "CONFIRMED_BOTTOM_FRACTAL_ZONE" if kind == "bottom" else "CONFIRMED_TOP_FRACTAL_ZONE"
        )
        details = {
            **graph_details,
            "construction_layer": "coarse",
            "fractal_type": kind,
            "zone_low_i64": low,
            "zone_high_i64": high,
        }
        output.transition(
            bar,
            state,
            reason,
            object_id,
            anchor_bar_index=int(value["bar_index"]),
            price_i64=int(value["price_i64"]),
            details=details,
        )
        output.chart_event(
            bar,
            f"coarse_{kind}_zone",
            reason,
            object_id,
            anchor_bar_index=int(value["bar_index"]),
            price_i64=int(value["price_i64"]),
            details=details,
        )

    def monitor_coarse(bar: StrategyBar) -> None:
        nonlocal coarse_standing_count
        if (
            coarse_source_id is None
            or coarse_source is None
            or coarse_known_at is None
            or bar.bar_index <= coarse_known_at
        ):
            return
        kind = str(coarse_source["fractal_type"])
        low = int(coarse_source["zone_low_i64"])
        high = int(coarse_source["zone_high_i64"])
        failed = bar.low_i64 < low if kind == "bottom" else bar.high_i64 > high
        if failed:
            resolve_coarse(
                bar,
                f"COARSE_{kind.upper()}_BUILD_FAILED",
                f"coarse_{kind}_failure",
                f"PRICE_STRICTLY_BROKE_COARSE_{kind.upper()}_ZONE",
                coarse_source_id,
            )
            return
        stood_beyond = bar.close_i64 > high if kind == "bottom" else bar.close_i64 < low
        coarse_standing_count = coarse_standing_count + 1 if stood_beyond else 0
        if coarse_standing_count >= coarse_hold_bars:
            resolve_coarse(
                bar,
                f"COARSE_{kind.upper()}_BUILD_SUCCESS",
                f"coarse_{kind}_success",
                f"CLOSE_EFFECTIVELY_STOOD_BEYOND_COARSE_{kind.upper()}_ZONE",
                coarse_source_id,
            )

    for bar in feed.bars:
        if bar.bar_index % 256 == 0 and cancelled.is_set():
            raise InterruptedError("strategy execution cancelled")
        new_fractals: list[tuple[str, dict[str, Any]]] = []
        new_centers: list[tuple[str, dict[str, Any]]] = []
        new_points: list[tuple[str, dict[str, Any]]] = []
        precise_invalidations: set[str] = set()
        coarse_invalidated = False
        precise_dependencies = {
            value
            for value in (source_turn_id, resulting_center_id, result_point_id)
            if value is not None
        }
        targets = {
            "fractal": fractals,
            "segment_zhongshu": centers,
            "trade_point": trade_points,
        }
        new_values = {
            "fractal": new_fractals,
            "segment_zhongshu": new_centers,
            "trade_point": new_points,
        }
        for event in feed.events_by_bar.get(bar.bar_index, []):
            target = targets[event.object_type]
            if event.operation == "delete":
                target.pop(event.object_id, None)
                if event.object_id in precise_dependencies:
                    precise_invalidations.add(event.object_id)
                if event.object_id == coarse_source_id:
                    coarse_invalidated = True
                continue
            value = json.loads(event.payload_json)
            previous = target.get(event.object_id)
            target[event.object_id] = value
            if previous is None:
                changed = True
            elif event.object_type == "fractal":
                changed = fractal_signature(previous) != fractal_signature(value)
            elif event.object_type == "segment_zhongshu":
                changed = center_identity_signature(previous) != center_identity_signature(value)
            else:
                changed = signal_signature(previous) != signal_signature(value)
            if event.object_id == resulting_center_id and event.object_type == "segment_zhongshu":
                resulting_center = value
            if changed:
                new_values[event.object_type].append((event.object_id, value))
                if previous is not None and event.object_id in precise_dependencies:
                    precise_invalidations.add(event.object_id)
                if previous is not None and event.object_id == coarse_source_id:
                    coarse_invalidated = True

        if source_turn_id is not None and precise_invalidations:
            reset_precise(
                bar,
                "BOTTOM_TOP_CONSTRUCTION_SOURCE_FACT_REVISED",
                sorted(precise_invalidations)[0],
            )
        if coarse_source_id is not None and coarse_invalidated:
            resolve_coarse(
                bar,
                "COARSE_CONSTRUCTION_RESET",
                "coarse_construction_reset",
                "COARSE_FRACTAL_SOURCE_REVISED",
                coarse_source_id,
            )

        first_points = [
            (object_id, value)
            for object_id, value in new_points
            if object_id not in consumed_turn_ids and standard_first_point(value)
        ]
        first_points.sort(key=lambda item: (int(item[1]["bar_index"]), item[0]))
        if first_points:
            object_id, value = first_points[0]
            if source_turn_id is not None:
                reset_precise(
                    bar,
                    "NEW_STANDARD_FIRST_POINT_STARTED_NEXT_CONSTRUCTION",
                    object_id,
                    event_type="bottom_top_construction_handoff",
                )
            activate_precise(bar, object_id, value)
            advance_precise(bar, new_centers, new_points)
        elif source_turn_id is not None:
            advance_precise(bar, new_centers, new_points)

        if source_turn_id is None:
            for object_id, value in sorted(
                new_fractals,
                key=lambda item: (int(item[1].get("bar_index", -1)), item[0]),
            ):
                activate_coarse(bar, object_id, value)
            monitor_coarse(bar)

    return output.result(feed.bars)
