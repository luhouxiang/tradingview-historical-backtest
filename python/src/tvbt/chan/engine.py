from __future__ import annotations

import hashlib
import inspect
import json
from dataclasses import asdict, dataclass
from typing import Any, Literal

from tvbt.chan.events import EventEmitter
from tvbt.chan.local_center import RULE_VERSION as LOCAL_CENTER_RULE_VERSION
from tvbt.chan.local_center import (
    CenterAuditEvent,
    CenterConnection,
    CenterStreamKey,
    CenterUnit,
    LocalCenter,
    LocalCenterAccumulator,
    classify_center_relative_direction,
    compare_center_boundaries,
)
from tvbt.chan.reference import (
    ReferenceSegment,
    ReferenceSegmentAccumulator,
)
from tvbt.chan.signals import (
    BiCenterEvidence,
    ChanSignal,
    StructuralCenter,
    chan_divergences,
    chan_first_point_candidates,
    chan_forming_divergences,
    chan_trade_points,
)
from tvbt.chan.zn import classify_zn_components
from tvbt.logging_proxy import logger

"""逐 K 线因果缠论引擎。

算法分层：

1. `RawBar` 保存 Go 指定的标准化 K 线字段。
2. `_update_inclusion` 处理包含关系，生成独立 K 线 `IncludedBar`。
3. `_seal_fractal` 在第三根独立 K 线出现后确认中间 K 线的严格分型。
4. `_consume_fractal` 按独立 K 线闭区间极值规则维护最长合法笔链。
5. `_update_structures` 在笔变化时只重算各级尚未确定的结构尾部。
6. `EventEmitter` 统一把结构变化转换为 `known_at_bar_index` 约束的因果事件。

本文件不读取磁盘、不写缓存、不处理订单成交；这些职责分别在 `algorithm.py`、
`storage.py` 和策略/回测模块中完成。
"""

Direction = Literal["up", "down", "unknown"]
FractalKind = Literal["top", "bottom"]
FractalStatus = Literal["candidate", "confirmed", "invalidated"]
BiLiveState = Literal[
    "SEEK_FIRST_FRACTAL",
    "UP_EXTENDING",
    "TOP_FORMING",
    "DOWN_EXTENDING",
    "BOTTOM_FORMING",
]
CenterBoundaryProfile = Literal["local_center_boundary_v1"]
# 一笔至少跨越 5 根包含处理后的独立 K 线。
REFERENCE_MIN_INDEPENDENT_BARS = 5


def _stable_id(prefix: str, *parts: object) -> str:
    """根据语义字段生成稳定 ID，避免同一对象在重算后 ID 漂移。"""
    data = json.dumps(parts, ensure_ascii=False, separators=(",", ":"), default=str).encode()
    return f"{prefix}-{hashlib.sha256(data).hexdigest()[:20]}"


@dataclass(frozen=True, slots=True)
class RawBar:
    """Go 指定的标准化原始 K 线，完整保留 OHLC 定点价格字段。"""

    # 全数据集内连续递增的原始 K 线序号，从0开始。
    bar_index: int
    # K 线时间戳，沿用上游传入的 UTC 毫秒语义。
    time: int
    # 开盘价、最高价、最低价、收盘价均使用定点整数，避免检查点出现浮点误差。
    # 当前含义是原始价格乘以数据集 price_scale 后取整数；具体倍数由数据集元数据决定。
    open_i64: int
    high_i64: int
    low_i64: int
    close_i64: int


@dataclass(slots=True)
class IncludedBar:
    """包含关系处理后的独立 K 线，是分型和成笔判断的基础序列。"""

    # 独立 K 线在包含处理序列中的位置。
    normalized_index: int
    # 该独立 K 线覆盖的原始 K 线闭区间。
    start_raw_index: int
    end_raw_index: int
    start_time: int
    end_time: int
    open_i64: int
    close_i64: int
    # 包含处理后的高低点价格。
    high_i64: int
    low_i64: int
    # 高低点实际来自哪根原始 K 线的时间。
    high_time: int
    low_time: int
    # 高低点实际来自哪根原始 K 线的 bar_index。
    high_raw_index: int
    low_raw_index: int
    # 本独立 K 线最新一次被确认或扩展的时间。
    confirm_time: int
    # 与前一个独立 K 线形成的方向，用于包含关系合并时选择取高高还是低低。
    direction: Direction
    # 当前独立 K 线吸收的全部原始 K 线索引，便于审计包含关系。
    source_raw_indices: list[int]

    @property
    def object_id(self) -> str:
        return _stable_id("processed-bar", self.start_raw_index)

    def payload(
        self,
        *,
        status: Literal["forming", "sealed"],
        sealed_at_bar_index: int | None,
        catalog_event: Literal["processed_bar", "processed_bar_revision"],
    ) -> dict[str, Any]:
        return {
            "normalized_index": self.normalized_index,
            "start_bar_index": self.start_raw_index,
            "start_time": self.start_time,
            "end_bar_index": self.end_raw_index,
            "end_time": self.end_time,
            "open_i64": self.open_i64,
            "high_i64": self.high_i64,
            "low_i64": self.low_i64,
            "close_i64": self.close_i64,
            "high_source_bar_index": self.high_raw_index,
            "low_source_bar_index": self.low_raw_index,
            "direction": self.direction,
            "source_bar_indices": list(self.source_raw_indices),
            "status": status,
            "sealed_at_bar_index": sealed_at_bar_index,
            "catalog_event": catalog_event,
        }


@dataclass(frozen=True, slots=True)
class Fractal:
    """严格三根独立 K 线确认的顶/底分型。"""

    # 稳定语义 ID，用于事件流 upsert/delete 和前端对象树定位。
    object_id: str
    # top 表示顶分型，bottom 表示底分型。
    fractal_type: FractalKind
    # 分型中间独立 K 线的位置。
    normalized_index: int
    # 分型价格锚点对应的原始 K 线位置、时间和定点价格。
    bar_index: int
    time: int
    price_i64: int
    # 分型被确认的原始 K 线位置；known_at 与其一致，禁止提前显示。
    confirmed_at_bar_index: int | None
    known_at_bar_index: int
    # 分型极值实际来源的原始 K 线位置；当前与 bar_index 相同，单独保存以防字段误读。
    extreme_source_bar_index: int = -1
    # 分型中间处理后 K 线的完整价格区间，供上层粗略底/顶构造观察使用。
    # 旧检查点缺少该字段时退化为极值点；10.0.0 新输出始终显式填写真实区间。
    zone_low_i64: int | None = None
    zone_high_i64: int | None = None
    status: FractalStatus = "confirmed"
    invalidation_reason: str | None = None
    aux_strength: Literal["strong_reversal", "unclassified"] = "unclassified"
    strength_reason: str = "lesson82_strong_reversal_condition_not_met"
    body_i64: int | None = None
    upper_shadow_i64: int | None = None
    lower_shadow_i64: int | None = None
    range_i64: int | None = None
    close_position_milli: int | None = None
    feature_profile: str = "processed_bar_ohlc_v1"

    def __post_init__(self) -> None:
        if self.extreme_source_bar_index < 0:
            object.__setattr__(self, "extreme_source_bar_index", self.bar_index)
        if self.zone_low_i64 is None:
            object.__setattr__(self, "zone_low_i64", self.price_i64)
        if self.zone_high_i64 is None:
            object.__setattr__(self, "zone_high_i64", self.price_i64)
        assert self.zone_low_i64 is not None and self.zone_high_i64 is not None
        if self.zone_low_i64 > self.zone_high_i64:
            raise ValueError("fractal zone_low_i64 must not exceed zone_high_i64")

    def payload(self) -> dict[str, Any]:
        """转换为因果事件流载荷，字段名保持跨进程 snake_case 契约。"""
        return {
            "bar_index": self.bar_index,
            "time": self.time,
            "price_i64": self.price_i64,
            "zone_low_i64": self.zone_low_i64,
            "zone_high_i64": self.zone_high_i64,
            "extreme_source_bar_index": self.extreme_source_bar_index,
            "fractal_type": self.fractal_type,
            "status": self.status,
            "invalidation_reason": self.invalidation_reason,
            "aux_strength": self.aux_strength,
            "strength_reason": self.strength_reason,
            "body_i64": self.body_i64,
            "upper_shadow_i64": self.upper_shadow_i64,
            "lower_shadow_i64": self.lower_shadow_i64,
            "range_i64": self.range_i64,
            "close_position_milli": self.close_position_milli,
            "feature_profile": self.feature_profile,
            "catalog_algorithm_id": "ALG-GEO-002",
            "strength_semantic_namespace": "auxiliary",
            "standard_signal": False,
            "execution_allowed": False,
            "confirmed": self.status == "confirmed",
            "confirmed_at_bar_index": self.confirmed_at_bar_index,
        }


@dataclass(frozen=True, slots=True)
class LineObject:
    """缠论线性对象，当前同时用于笔和已确认线段的统一表示。"""

    # 稳定语义 ID。
    object_id: str
    # 起止分型锚点，保留时间和价格语义，不依赖屏幕像素。
    start: Fractal
    end: Fractal
    # 由起点分型类型决定的线方向。
    direction: Literal["up", "down"]
    # 对象确认与可知时间，所有下游事件必须遵守因果性。
    confirmed_at_bar_index: int
    known_at_bar_index: int
    # 结构计算使用的完整价格区间。笔默认等于端点极值；线段由全部组成笔区间并集给出。
    range_low_i64: int | None = None
    range_high_i64: int | None = None
    range_low_source_bar_index: int | None = None
    range_high_source_bar_index: int | None = None
    range_profile: Literal[
        "endpoint_extrema_v1", "constituent_bi_union_v1", "forming_observed_bars_v1"
    ] = "endpoint_extrema_v1"

    def __post_init__(self) -> None:
        endpoint_low = min(self.start.price_i64, self.end.price_i64)
        endpoint_high = max(self.start.price_i64, self.end.price_i64)
        low_source = (
            self.start.extreme_source_bar_index
            if self.start.price_i64 <= self.end.price_i64
            else self.end.extreme_source_bar_index
        )
        high_source = (
            self.start.extreme_source_bar_index
            if self.start.price_i64 >= self.end.price_i64
            else self.end.extreme_source_bar_index
        )
        if self.range_low_i64 is None:
            object.__setattr__(self, "range_low_i64", endpoint_low)
        if self.range_high_i64 is None:
            object.__setattr__(self, "range_high_i64", endpoint_high)
        if self.range_low_source_bar_index is None:
            object.__setattr__(self, "range_low_source_bar_index", low_source)
        if self.range_high_source_bar_index is None:
            object.__setattr__(self, "range_high_source_bar_index", high_source)
        assert self.range_low_i64 is not None and self.range_high_i64 is not None
        if self.range_low_i64 > endpoint_low or self.range_high_i64 < endpoint_high:
            raise ValueError("line actual range must contain both structural endpoints")

    def payload(
        self,
        *,
        confirmed: bool = True,
        status: Literal["candidate", "confirmed", "invalidated"] | None = None,
        invalidation_reason: str | None = None,
        catalog_algorithm_id: Literal["ALG-GEO-003", "ALG-GEO-004"] = "ALG-GEO-003",
    ) -> dict[str, Any]:
        """转换为图层绘制和对象树使用的事件载荷。"""
        return {
            "start_bar_index": self.start.bar_index,
            "start_time": self.start.time,
            "start_price_i64": self.start.price_i64,
            "start_extreme_source_bar_index": self.start.extreme_source_bar_index,
            "end_bar_index": self.end.bar_index,
            "end_time": self.end.time,
            "end_price_i64": self.end.price_i64,
            "end_extreme_source_bar_index": self.end.extreme_source_bar_index,
            "range_low_i64": self.range_low_i64,
            "range_high_i64": self.range_high_i64,
            "range_low_source_bar_index": self.range_low_source_bar_index,
            "range_high_source_bar_index": self.range_high_source_bar_index,
            "range_profile": self.range_profile,
            "direction": self.direction,
            "status": status or ("confirmed" if confirmed else "candidate"),
            "invalidation_reason": invalidation_reason,
            "catalog_algorithm_id": catalog_algorithm_id,
            "confirmed": confirmed,
            "confirmed_at_bar_index": self.confirmed_at_bar_index if confirmed else None,
        }


@dataclass(frozen=True, slots=True)
class ChanParameters:
    """缠论引擎参数。当前只暴露检查点间隔，便于后续恢复与长任务拆分。"""

    # 每处理多少根 K 线允许外层保存一次检查点。
    checkpoint_interval: int = 1024
    center_boundary_profile: CenterBoundaryProfile = "local_center_boundary_v1"

    def __post_init__(self) -> None:
        if self.checkpoint_interval < 1:
            raise ValueError("bar-count parameters must be positive")
        if self.center_boundary_profile != "local_center_boundary_v1":
            raise ValueError("unsupported center_boundary_profile")


class ChanEngine:
    """逐 K 线因果缠论引擎，负责分型、笔、线段、中枢和信号事件生成。"""

    # 算法版本参与缓存键；任何语义变化都必须升级版本，禁止复用旧缓存。
    algorithm_version = "19.3.2"

    def __init__(
        self,
        parameters: ChanParameters | None = None,
        *,
        stream_symbol: str = "UNKNOWN",
        stream_timeframe: str = "unknown",
        stream_anchor_id: str = "full-history",
        price_tick_i64: int = 1,
    ) -> None:
        # 运行参数。
        self.parameters = parameters or ChanParameters()
        self.stream_symbol = stream_symbol
        self.stream_timeframe = stream_timeframe
        self.stream_anchor_id = stream_anchor_id
        if (
            isinstance(price_tick_i64, bool)
            or not isinstance(price_tick_i64, int)
            or price_tick_i64 <= 0
        ):
            raise ValueError("price_tick_i64 must be a positive integer")
        self.price_tick_i64 = price_tick_i64
        # 原始 K 线序列，必须按 bar_index 和 time 严格递增。
        self.raw_bars: list[RawBar] = []
        # 经过包含关系处理后的独立 K 线序列。
        self.included: list[IncludedBar] = []
        # 已确认并发布过的分型。
        self.fractals: list[Fractal] = []
        # 最新一根处理后 K 线形成的分型候选；确认或失效后仍通过事件保留审计历史。
        self._fractal_candidate: Fractal | None = None
        # 已确认的笔序列，方向必须严格交替。
        self.bi: list[LineObject] = []
        # 每个分型作为笔终点时可形成的最长笔链长度及其前驱分型位置。
        self._bi_scores: list[int] = []
        self._bi_predecessors: list[int | None] = []
        # 当前最长合法笔链的末端分型位置。
        self._best_bi_endpoint: int | None = None
        # 当前已发布笔链对应的分型位置，用于只修订发生变化的尾部。
        self._bi_path_positions: list[int] = []
        self._active_bi_candidate_id: str | None = None
        self._bi_live_state: BiLiveState = "SEEK_FIRST_FRACTAL"
        # 线段扫描器保存每个笔前缀的小状态，笔尾修订时只回滚并重放变化部分。
        self._segment_accumulator = ReferenceSegmentAccumulator()
        # 已离开的中枢和已确认线段构成稳定前缀，后续只替换各级未确定尾部。
        self._segment_specs: list[ReferenceSegment] = []
        self._segment_records: list[tuple[tuple[str, dict[str, Any], int], LineObject | None]] = []
        self._segment_lines: list[LineObject] = []
        self._local_center_accumulators: dict[str, LocalCenterAccumulator] = {}
        self._local_center_preview_ids: dict[str, str] = {}
        self._center_unit_cache: dict[tuple[str, int, LineObject], CenterUnit] = {}
        self._center_audit_payload_cache: dict[str, dict[str, Any]] = {}
        # 独立 K 线位置到分型列表位置的索引，供区间极值扫描快速定位候选端点。
        self._fractal_by_normalized_index: dict[int, int] = {}
        # MACD EMA 状态，用于后续线段背驰面积计算。
        self._macd_fast: float | None = None
        self._macd_slow: float | None = None
        self._macd_dea: float | None = None
        # 每根原始 K 线的 MACD 柱值，按国内常用 2 * (DIFF - DEA) 语义保存。
        self._macd_histogram: dict[int, float] = {}
        self._macd_diff_by_bar: dict[int, float] = {}
        self._macd_dea_by_bar: dict[int, float] = {}
        # 已确认线段的 MACD 面积缓存，键包含实际端点，端点修订时不会误复用。
        self._macd_area_cache: dict[tuple[str, int, int, str], float] = {}
        # 因果事件收集器，统一管理 upsert/delete 和当前对象快照。
        self.emitter = EventEmitter()

    def update(self, bar: RawBar) -> None:
        """输入一根新 K 线并增量推进全部缠论对象。"""
        if self.raw_bars and bar.bar_index != self.raw_bars[-1].bar_index + 1:
            raise ValueError("raw bars must have contiguous bar_index")
        if self.raw_bars and bar.time <= self.raw_bars[-1].time:
            raise ValueError("raw bar times must be strictly increasing")
        if bar.low_i64 > bar.high_i64:
            raise ValueError("raw bar low exceeds high")
        if not bar.low_i64 <= bar.open_i64 <= bar.high_i64:
            raise ValueError("raw bar open is outside high-low range")
        if not bar.low_i64 <= bar.close_i64 <= bar.high_i64:
            raise ValueError("raw bar close is outside high-low range")
        self.raw_bars.append(bar)
        self._append_macd(bar)
        # 包含关系只有在追加出新的独立 K 线时，才可能封存上一根独立 K 线的分型。
        appended = self._update_inclusion(bar)
        self._publish_processed_bar(bar, appended)
        changed_bi_index: int | None = None
        resolved_candidate = self._fractal_candidate if appended else None
        resolved: Fractal | None = None
        if appended and resolved_candidate is not None:
            resolved = self._resolve_fractal_candidate(resolved_candidate)
            if resolved.status == "confirmed":
                self.fractals.append(resolved)
                changed_bi_index = self._consume_fractal(resolved)
            self._finalize_bi_candidate(resolved)
        if changed_bi_index is not None:
            # 只从首次变化的笔位置更新中枢、线段、背驰和买卖点尾部。
            self._update_structures(bar.bar_index, changed_bi_index)
        candidate_changed = self._refresh_fractal_candidate(bar.bar_index)
        bi_candidate_changed = self._refresh_bi_candidate(bar.bar_index)
        self._publish_local_center_previews(bar.bar_index)
        self._refresh_forming_divergences(bar.bar_index)
        if not self.fractals and self._fractal_candidate is None:
            trigger = "initial"
        elif resolved is not None:
            if changed_bi_index is not None:
                trigger = "bi_confirmed"
            elif resolved.status == "confirmed" and len(self.fractals) == 1:
                trigger = "first_fractal_confirmed"
            elif resolved.status == "confirmed":
                trigger = "fractal_confirmed"
            else:
                trigger = "candidate_invalidated"
        elif bi_candidate_changed:
            trigger = "candidate_started"
        elif candidate_changed:
            trigger = "candidate_revised"
        else:
            trigger = "processed_bar_update"
        self._publish_bi_state(bar, trigger)

    def _update_inclusion(self, bar: RawBar) -> bool:
        """按前一根独立 K 线方向处理包含关系，返回是否产生新独立 K 线。"""
        # 先把新原始 K 线包装成候选独立 K 线。
        current = IncludedBar(
            normalized_index=len(self.included),
            start_raw_index=bar.bar_index,
            end_raw_index=bar.bar_index,
            start_time=bar.time,
            end_time=bar.time,
            open_i64=bar.open_i64,
            close_i64=bar.close_i64,
            high_i64=bar.high_i64,
            low_i64=bar.low_i64,
            high_time=bar.time,
            low_time=bar.time,
            high_raw_index=bar.bar_index,
            low_raw_index=bar.bar_index,
            confirm_time=bar.time,
            direction="unknown",
            source_raw_indices=[bar.bar_index],
        )
        if not self.included:
            self.included.append(current)
            return True
        previous = self.included[-1]
        if current.high_i64 > previous.high_i64 and current.low_i64 > previous.low_i64:
            current.direction = "up"
            self.included.append(current)
            return True
        if current.high_i64 < previous.high_i64 and current.low_i64 < previous.low_i64:
            current.direction = "down"
            self.included.append(current)
            return True
        # 剩余情况存在包含关系，需要按既有方向合并到上一根独立 K 线。
        direction = previous.direction
        # 首对 K 线方向尚不稳定时，参考右侧是否外包左侧单独处理。
        first_pair = len(self.included) == 1 and direction == "unknown"
        if first_pair:
            right_contains = (
                current.high_i64 > previous.high_i64 or current.low_i64 < previous.low_i64
            )
            if right_contains:
                high = current.high_i64
                low = previous.low_i64
                high_from_current = current.high_i64 != previous.high_i64
                low_from_current = False
            else:
                high = previous.high_i64
                low = current.low_i64
                high_from_current = False
                low_from_current = current.low_i64 != previous.low_i64
        elif direction == "up":
            high_from_current = current.high_i64 > previous.high_i64
            low_from_current = current.low_i64 > previous.low_i64
            high = max(previous.high_i64, current.high_i64)
            low = max(previous.low_i64, current.low_i64)
        else:
            high_from_current = current.high_i64 < previous.high_i64
            low_from_current = current.low_i64 < previous.low_i64
            high = min(previous.high_i64, current.high_i64)
            low = min(previous.low_i64, current.low_i64)
        self.included[-1] = IncludedBar(
            normalized_index=previous.normalized_index,
            start_raw_index=previous.start_raw_index,
            end_raw_index=bar.bar_index,
            start_time=previous.start_time,
            end_time=bar.time,
            open_i64=previous.open_i64,
            close_i64=bar.close_i64,
            high_i64=high,
            low_i64=low,
            high_time=current.high_time if high_from_current else previous.high_time,
            low_time=current.low_time if low_from_current else previous.low_time,
            high_raw_index=current.high_raw_index if high_from_current else previous.high_raw_index,
            low_raw_index=current.low_raw_index if low_from_current else previous.low_raw_index,
            confirm_time=bar.time,
            direction=direction,
            source_raw_indices=[*previous.source_raw_indices, bar.bar_index],
        )
        return False

    def _publish_processed_bar(self, bar: RawBar, appended: bool) -> None:
        """发布形成、包含修订与封存；同一处理后 K 线始终复用稳定 ID。"""
        if appended and len(self.included) > 1:
            previous = self.included[-2]
            self.emitter.upsert(
                bar.bar_index,
                "processed_bar",
                previous.object_id,
                previous.payload(
                    status="sealed",
                    sealed_at_bar_index=bar.bar_index,
                    catalog_event="processed_bar_revision",
                ),
            )
        current = self.included[-1]
        self.emitter.upsert(
            bar.bar_index,
            "processed_bar",
            current.object_id,
            current.payload(
                status="forming",
                sealed_at_bar_index=None,
                catalog_event="processed_bar" if appended else "processed_bar_revision",
            ),
        )

    def _fractal_from_processed_bar(
        self,
        center: IncludedBar,
        kind: FractalKind,
        *,
        status: FractalStatus,
        known_at_bar_index: int,
        confirmed_at_bar_index: int | None,
        invalidation_reason: str | None = None,
        aux_strength: Literal["strong_reversal", "unclassified"] = "unclassified",
        strength_reason: str = "awaiting_right_processed_bar",
    ) -> Fractal:
        pivot_index = center.high_raw_index if kind == "top" else center.low_raw_index
        pivot_time = center.high_time if kind == "top" else center.low_time
        price = center.high_i64 if kind == "top" else center.low_i64
        return Fractal(
            object_id=_stable_id("fractal", kind, center.start_raw_index),
            fractal_type=kind,
            normalized_index=center.normalized_index,
            bar_index=pivot_index,
            time=pivot_time,
            price_i64=price,
            confirmed_at_bar_index=confirmed_at_bar_index,
            known_at_bar_index=known_at_bar_index,
            extreme_source_bar_index=pivot_index,
            zone_low_i64=center.low_i64,
            zone_high_i64=center.high_i64,
            status=status,
            invalidation_reason=invalidation_reason,
            aux_strength=aux_strength,
            strength_reason=strength_reason,
            body_i64=abs(center.close_i64 - center.open_i64),
            upper_shadow_i64=center.high_i64 - max(center.open_i64, center.close_i64),
            lower_shadow_i64=min(center.open_i64, center.close_i64) - center.low_i64,
            range_i64=center.high_i64 - center.low_i64,
            close_position_milli=(
                500
                if center.high_i64 == center.low_i64
                else (center.close_i64 - center.low_i64)
                * 1000
                // (center.high_i64 - center.low_i64)
            ),
        )

    def _refresh_fractal_candidate(self, known_at_bar_index: int) -> bool:
        """用最后两根处理后 K 线发布或修订等待右侧 K 线的候选分型。"""
        previous = self._fractal_candidate
        if len(self.included) < 2:
            self._fractal_candidate = None
            return False
        left, center = self.included[-2:]
        if center.high_i64 > left.high_i64 and center.low_i64 > left.low_i64:
            kind: FractalKind = "top"
        elif center.high_i64 < left.high_i64 and center.low_i64 < left.low_i64:
            kind = "bottom"
        else:
            raise AssertionError("adjacent processed bars must not contain each other")
        candidate = self._fractal_from_processed_bar(
            center,
            kind,
            status="candidate",
            known_at_bar_index=known_at_bar_index,
            confirmed_at_bar_index=None,
        )
        if previous is not None and previous.object_id != candidate.object_id:
            invalidated = Fractal(
                **{
                    **asdict(previous),
                    "status": "invalidated",
                    "known_at_bar_index": known_at_bar_index,
                    "invalidation_reason": "inclusion_revision_changed_candidate",
                    "strength_reason": "candidate_invalidated_before_right_bar",
                }
            )
            self.emitter.upsert(
                known_at_bar_index, "fractal", invalidated.object_id, invalidated.payload()
            )
        self._fractal_candidate = candidate
        event = self.emitter.upsert(
            known_at_bar_index,
            "fractal",
            candidate.object_id,
            candidate.payload(),
        )
        return event is not None

    def _resolve_fractal_candidate(self, candidate: Fractal) -> Fractal:
        """右侧处理后 K 线到来后确认或保留失效候选，并计算第 82 课辅助标签。"""
        index = candidate.normalized_index
        if index < 1 or index + 1 >= len(self.included):
            raise AssertionError("fractal candidate requires left and right processed bars")
        left, center, right = self.included[index - 1 : index + 2]
        confirmed = (
            candidate.fractal_type == "top"
            and center.high_i64 > right.high_i64
            and center.low_i64 > right.low_i64
        ) or (
            candidate.fractal_type == "bottom"
            and center.high_i64 < right.high_i64
            and center.low_i64 < right.low_i64
        )
        known_at = right.end_raw_index
        if confirmed:
            strong = (
                candidate.fractal_type == "top"
                and right.low_i64 < left.low_i64
                and 2 * right.close_i64 <= left.high_i64 + left.low_i64
            ) or (
                candidate.fractal_type == "bottom"
                and right.high_i64 > left.high_i64
                and 2 * right.close_i64 >= left.high_i64 + left.low_i64
            )
            resolved = self._fractal_from_processed_bar(
                center,
                candidate.fractal_type,
                status="confirmed",
                known_at_bar_index=known_at,
                confirmed_at_bar_index=known_at,
                aux_strength="strong_reversal" if strong else "unclassified",
                strength_reason=(
                    "lesson82_third_bar_break_and_close_beyond_first_midpoint"
                    if strong
                    else "lesson82_strong_reversal_condition_not_met"
                ),
            )
        else:
            resolved = Fractal(
                **{
                    **asdict(candidate),
                    "status": "invalidated",
                    "known_at_bar_index": known_at,
                    "invalidation_reason": "right_bar_failed_strict_fractal_relation",
                    "strength_reason": "candidate_invalidated_before_strength_classification",
                }
            )
        self.emitter.upsert(known_at, "fractal", resolved.object_id, resolved.payload())
        self._fractal_candidate = None
        return resolved

    def _invalidate_bi_object(
        self,
        object_id: str,
        known_at_bar_index: int,
        reason: str,
    ) -> None:
        current = {str(item["object_id"]): item for item in self.emitter.current("bi")}.get(
            object_id
        )
        if current is None or current.get("status") == "invalidated":
            return
        payload = {
            name: value
            for name, value in current.items()
            if name not in {"object_id", "known_at_bar_index", "object_revision"}
        }
        payload.update(
            {
                "status": "invalidated",
                "invalidation_reason": reason,
                "confirmed": False,
            }
        )
        self.emitter.upsert(known_at_bar_index, "bi", object_id, payload)

    def _candidate_bi_predecessor(self, endpoint: Fractal) -> int | None:
        candidates = self._incoming_bi_candidates(endpoint)
        if not candidates:
            return None
        score = max(self._bi_scores[position] + 1 for position in candidates)
        eligible = [position for position in candidates if self._bi_scores[position] + 1 == score]
        if self._best_bi_endpoint in eligible:
            return self._best_bi_endpoint
        if self._best_bi_endpoint is not None:
            current_predecessor = self._bi_predecessors[self._best_bi_endpoint]
            if current_predecessor in eligible:
                return current_predecessor
        return max(eligible, key=lambda position: self.fractals[position].normalized_index)

    def _refresh_bi_candidate(self, known_at_bar_index: int) -> bool:
        """把合法但尚待右侧分型确认的线发布为候选笔。"""
        endpoint = self._fractal_candidate
        predecessor = self._candidate_bi_predecessor(endpoint) if endpoint is not None else None
        if endpoint is None or predecessor is None:
            if self._active_bi_candidate_id is not None:
                self._invalidate_bi_object(
                    self._active_bi_candidate_id,
                    known_at_bar_index,
                    "candidate_geometry_no_longer_valid",
                )
                self._active_bi_candidate_id = None
                return True
            return False
        start = self.fractals[predecessor]
        direction: Literal["up", "down"] = "up" if start.fractal_type == "bottom" else "down"
        line = LineObject(
            object_id=_stable_id("bi", start.object_id, endpoint.object_id),
            start=start,
            end=endpoint,
            direction=direction,
            confirmed_at_bar_index=known_at_bar_index,
            known_at_bar_index=known_at_bar_index,
        )
        if (
            self._active_bi_candidate_id is not None
            and self._active_bi_candidate_id != line.object_id
        ):
            self._invalidate_bi_object(
                self._active_bi_candidate_id,
                known_at_bar_index,
                "more_extreme_or_revised_candidate_replaced_endpoint",
            )
        previous_id = self._active_bi_candidate_id
        self._active_bi_candidate_id = line.object_id
        event = self.emitter.upsert(
            known_at_bar_index,
            "bi",
            line.object_id,
            line.payload(confirmed=False, status="candidate"),
        )
        return event is not None or previous_id != line.object_id

    def _finalize_bi_candidate(self, resolved_fractal: Fractal) -> None:
        object_id = self._active_bi_candidate_id
        if object_id is None:
            return
        current = {str(item["object_id"]): item for item in self.emitter.current("bi")}.get(
            object_id
        )
        if current is None or current.get("status") != "confirmed":
            reason = (
                "fractal_candidate_invalidated"
                if resolved_fractal.status == "invalidated"
                else "confirmed_fractal_not_selected_by_unique_bi_partition"
            )
            self._invalidate_bi_object(object_id, resolved_fractal.known_at_bar_index, reason)
        self._active_bi_candidate_id = None

    def _publish_bi_state(self, bar: RawBar, trigger: str) -> None:
        """从已确认笔端点与当前候选派生第 91/93 课在线状态，不读取未来 K 线。"""
        anchor_position = self._best_bi_endpoint
        anchor = self.fractals[anchor_position] if anchor_position is not None else None
        if anchor is None and self.fractals:
            anchor = self.fractals[-1]
        candidate = self._fractal_candidate
        if anchor is None:
            state: BiLiveState = "SEEK_FIRST_FRACTAL"
            direction: Literal["up", "down"] | None = None
        elif anchor.fractal_type == "bottom":
            direction = "up"
            state = (
                "TOP_FORMING" if candidate and candidate.fractal_type == "top" else "UP_EXTENDING"
            )
        else:
            direction = "down"
            state = (
                "BOTTOM_FORMING"
                if candidate and candidate.fractal_type == "bottom"
                else "DOWN_EXTENDING"
            )
        self._bi_live_state = state
        point = candidate or anchor
        self.emitter.upsert(
            bar.bar_index,
            "bi_state",
            "bi-state-current",
            {
                "bar_index": point.bar_index if point is not None else bar.bar_index,
                "time": point.time if point is not None else bar.time,
                "price_i64": point.price_i64 if point is not None else bar.close_i64,
                "state": state,
                "direction": direction,
                "anchor_fractal_id": anchor.object_id if anchor is not None else None,
                "candidate_object_id": self._active_bi_candidate_id,
                "trigger": trigger,
                "catalog_algorithm_id": "ALG-GEO-003",
            },
        )

    def _consume_fractal(self, endpoint: Fractal) -> int | None:
        """把新分型加入 processed_k 区间极值笔链，并同步必要的因果修订。"""
        endpoint_position = len(self.fractals) - 1
        if self.fractals[endpoint_position] is not endpoint:
            raise AssertionError("new fractal must be appended before bi selection")
        self._fractal_by_normalized_index[endpoint.normalized_index] = endpoint_position
        candidates = self._incoming_bi_candidates(endpoint)
        score = 0
        predecessor: int | None = None
        if candidates:
            score = max(self._bi_scores[position] + 1 for position in candidates)
            eligible = [
                position for position in candidates if self._bi_scores[position] + 1 == score
            ]
            # 优先延长当前终态笔链；同类更极端端点修订时则优先保留原前驱，
            # 避免同分路径无业务原因地整体跳换。
            if self._best_bi_endpoint in eligible:
                predecessor = self._best_bi_endpoint
            elif self._best_bi_endpoint is not None:
                current_predecessor = self._bi_predecessors[self._best_bi_endpoint]
                if current_predecessor in eligible:
                    predecessor = current_predecessor
            if predecessor is None:
                predecessor = max(
                    eligible,
                    key=lambda position: self.fractals[position].normalized_index,
                )
        self._bi_scores.append(score)
        self._bi_predecessors.append(predecessor)

        previous_best = self._best_bi_endpoint
        if previous_best is None or score > self._bi_scores[previous_best]:
            self._best_bi_endpoint = endpoint_position
        elif score == self._bi_scores[previous_best]:
            previous = self.fractals[previous_best]
            if endpoint.fractal_type == previous.fractal_type and self._is_more_extreme(
                endpoint, previous
            ):
                self._best_bi_endpoint = endpoint_position
        if self._best_bi_endpoint == previous_best:
            return None
        return self._sync_bi_path(endpoint.known_at_bar_index)

    def _incoming_bi_candidates(self, endpoint: Fractal) -> list[int]:
        """寻找所有能与终点构成 processed_k 全区间极值笔的前驱分型。"""
        range_low = endpoint.price_i64
        range_high = endpoint.price_i64
        candidates: list[int] = []
        for normalized_index in range(endpoint.normalized_index, -1, -1):
            included = self.included[normalized_index]
            range_low = min(range_low, included.low_i64)
            range_high = max(range_high, included.high_i64)
            # 一旦终点不再是相应区间极值，更早的任何分型都不可能与其成笔。
            if endpoint.fractal_type == "top" and range_high > endpoint.price_i64:
                break
            if endpoint.fractal_type == "bottom" and range_low < endpoint.price_i64:
                break
            start_position = self._fractal_by_normalized_index.get(normalized_index)
            if start_position is None:
                continue
            start = self.fractals[start_position]
            independent_count = endpoint.normalized_index - start.normalized_index + 1
            if independent_count < REFERENCE_MIN_INDEPENDENT_BARS:
                continue
            if (
                endpoint.fractal_type == "top"
                and start.fractal_type == "bottom"
                and start.price_i64 == range_low
            ) or (
                endpoint.fractal_type == "bottom"
                and start.fractal_type == "top"
                and start.price_i64 == range_high
            ):
                candidates.append(start_position)
        return candidates

    @staticmethod
    def _is_more_extreme(candidate: Fractal, current: Fractal) -> bool:
        """判断同类分型是否满足后顶更高或后底更低的替换条件。"""
        if candidate.fractal_type != current.fractal_type:
            return False
        if candidate.fractal_type == "top":
            return candidate.price_i64 > current.price_i64
        return candidate.price_i64 < current.price_i64

    def _sync_bi_path(self, known_at_bar_index: int) -> int | None:
        """把最长合法端点链转换为笔，并用 delete/upsert 发布因果修订。"""
        endpoint_positions: list[int] = []
        position = self._best_bi_endpoint
        while position is not None:
            endpoint_positions.append(position)
            position = self._bi_predecessors[position]
        endpoint_positions.reverse()
        common_endpoint_count = 0
        for old_position, new_position in zip(
            self._bi_path_positions,
            endpoint_positions,
            strict=False,
        ):
            if old_position != new_position:
                break
            common_endpoint_count += 1
        preserved_line_count = max(common_endpoint_count - 1, 0)
        removed = self.bi[preserved_line_count:]
        desired = self.bi[:preserved_line_count]
        for edge_index in range(preserved_line_count, len(endpoint_positions) - 1):
            start_position = endpoint_positions[edge_index]
            end_position = endpoint_positions[edge_index + 1]
            start = self.fractals[start_position]
            end = self.fractals[end_position]
            object_id = _stable_id("bi", start.object_id, end.object_id)
            direction: Literal["up", "down"] = "up" if start.fractal_type == "bottom" else "down"
            line = LineObject(
                object_id=object_id,
                start=start,
                end=end,
                direction=direction,
                confirmed_at_bar_index=known_at_bar_index,
                known_at_bar_index=known_at_bar_index,
            )
            self._assert_bi_extremes(line)
            desired.append(line)
        changed = bool(removed) or len(desired) != len(self.bi)
        self._bi_path_positions = endpoint_positions
        if not changed:
            return None
        for line in sorted(removed, key=lambda value: value.object_id):
            self.emitter.upsert(
                known_at_bar_index,
                "bi",
                line.object_id,
                line.payload(
                    confirmed=False,
                    status="invalidated",
                    invalidation_reason="more_extreme_confirmed_endpoint_repartitioned_bi",
                ),
            )
        for line in desired[preserved_line_count:]:
            self.emitter.upsert(
                known_at_bar_index,
                "bi",
                line.object_id,
                line.payload(status="confirmed"),
            )
        self.bi = desired
        return preserved_line_count

    def _assert_bi_extremes(self, line: LineObject) -> None:
        """断言笔端点是 processed_k 闭区间的方向极值，而不是原始 K 线局部点。"""
        bars = self.included[line.start.normalized_index : line.end.normalized_index + 1]
        if len(bars) < REFERENCE_MIN_INDEPENDENT_BARS:
            raise AssertionError("bi must span at least five processed bars")
        range_low = min(item.low_i64 for item in bars)
        range_high = max(item.high_i64 for item in bars)
        if line.direction == "up":
            valid = line.start.price_i64 == range_low and line.end.price_i64 == range_high
        else:
            valid = line.start.price_i64 == range_high and line.end.price_i64 == range_low
        if not valid:
            raise AssertionError(
                "bi endpoints must equal processed_k interval extremes: "
                f"{line.object_id} {line.start.price_i64}->{line.end.price_i64} "
                f"range=[{range_low},{range_high}]"
            )

    @staticmethod
    def _segment_component_bi(bi: list[LineObject], segment: ReferenceSegment) -> list[LineObject]:
        if not (0 <= segment.start_index < segment.end_index < len(bi)):
            raise AssertionError("segment must end at the start of an existing next bi")
        return bi[segment.start_index : segment.end_index]

    def _update_structures(self, known_at_bar_index: int, changed_bi_index: int) -> None:
        """从首次变化笔位置更新线段、实体中枢、背驰和买卖点。"""
        segment_specs = self._segment_accumulator.update(
            self.bi,
            self.raw_bars,
            changed_bi_index,
        )
        changed_segment_spec_index = self._common_prefix_length(
            self._segment_specs,
            segment_specs,
        )
        segment_records = self._segment_records[:changed_segment_spec_index]
        self._segment_specs = segment_specs
        # 线段：扫描器返回线段语义对象；确认段转成 LineObject 供实体中枢状态机消费。
        for segment in segment_specs[changed_segment_spec_index:]:
            object_id = _stable_id(
                "segment",
                self.bi[segment.start_index].object_id,
                "up" if segment.up else "down",
            )
            # end_index anchors the segment at the *start* of that bi.  The bi
            # itself belongs to the following segment and can extend well past
            # this segment's end bar; including it leaks the next leg's range.
            component_bi = self._segment_component_bi(self.bi, segment)
            range_low_line = min(
                component_bi,
                key=lambda line: (
                    line.range_low_i64
                    if line.range_low_i64 is not None
                    else min(line.start.price_i64, line.end.price_i64)
                ),
            )
            range_high_line = max(
                component_bi,
                key=lambda line: (
                    line.range_high_i64
                    if line.range_high_i64 is not None
                    else max(line.start.price_i64, line.end.price_i64)
                ),
            )
            range_low_i64 = range_low_line.range_low_i64
            range_high_i64 = range_high_line.range_high_i64
            assert range_low_i64 is not None and range_high_i64 is not None
            low_source = range_low_line.range_low_source_bar_index
            high_source = range_high_line.range_high_source_bar_index
            assert low_source is not None and high_source is not None
            if not (
                segment.start_bar_index <= low_source <= segment.end_bar_index
                and segment.start_bar_index <= high_source <= segment.end_bar_index
            ):
                raise AssertionError("segment range source must lie within its bar interval")
            value = (
                object_id,
                {
                    "start_bar_index": segment.start_bar_index,
                    "start_time": segment.start_time,
                    "start_price_i64": segment.start_price_i64,
                    "start_extreme_source_bar_index": segment.start_bar_index,
                    "end_bar_index": segment.end_bar_index,
                    "end_time": segment.end_time,
                    "end_price_i64": segment.end_price_i64,
                    "end_extreme_source_bar_index": segment.end_bar_index,
                    "range_low_i64": range_low_i64,
                    "range_high_i64": range_high_i64,
                    "range_low_source_bar_index": range_low_line.range_low_source_bar_index,
                    "range_high_source_bar_index": range_high_line.range_high_source_bar_index,
                    "range_profile": "constituent_bi_union_v1",
                    "direction": "up" if segment.up else "down",
                    "status": "confirmed" if segment.confirmed else "candidate",
                    "invalidation_reason": None,
                    "catalog_algorithm_id": "ALG-GEO-004",
                    "confirmed": segment.confirmed,
                    "confirmed_at_bar_index": (
                        segment.known_at_bar_index if segment.confirmed else None
                    ),
                },
                segment.known_at_bar_index,
            )
            segment_line: LineObject | None = None
            if segment.confirmed:
                direction: Literal["up", "down"] = "up" if segment.up else "down"
                start = Fractal(
                    f"{object_id}-start",
                    "bottom" if direction == "up" else "top",
                    segment.start_index,
                    segment.start_bar_index,
                    segment.start_time,
                    segment.start_price_i64,
                    segment.known_at_bar_index,
                    segment.known_at_bar_index,
                )
                end = Fractal(
                    f"{object_id}-end",
                    "top" if direction == "up" else "bottom",
                    segment.end_index,
                    segment.end_bar_index,
                    segment.end_time,
                    segment.end_price_i64,
                    segment.known_at_bar_index,
                    segment.known_at_bar_index,
                )
                segment_line = LineObject(
                    object_id,
                    start,
                    end,
                    direction,
                    segment.known_at_bar_index,
                    segment.known_at_bar_index,
                    range_low_i64,
                    range_high_i64,
                    range_low_line.range_low_source_bar_index,
                    range_high_line.range_high_source_bar_index,
                    "constituent_bi_union_v1",
                )
            segment_records.append((value, segment_line))
        self._segment_records = segment_records
        segments = [value for value, _ in segment_records]
        segment_lines = [line for _, line in segment_records if line is not None]

        # BI/SEGMENT 两条流在同一状态机中独立生成实体中枢。
        changed_confirmed_segment_index = self._common_prefix_length(
            self._segment_lines,
            segment_lines,
        )
        confirmed_segments_changed = changed_confirmed_segment_index < len(
            self._segment_lines
        ) or changed_confirmed_segment_index < len(segment_lines)
        self._segment_lines = segment_lines
        self._update_local_center_objects(known_at_bar_index)
        self._sync_objects("segment", segments, known_at_bar_index)
        if not confirmed_segments_changed:
            logger.debug(
                "chan.structures.tail_reused",
                "Confirmed segment prefix unchanged; upper Chan structures reused",
                {
                    "bar_index": known_at_bar_index,
                    "changed_bi_index": changed_bi_index,
                    "bi_count": len(self.bi),
                    "segment_count": len(segments),
                },
            )
            return

        segment_centers, segment_center_ids = self._segment_structural_centers()
        # 走势状态事件：记录中枢震荡、盘整和中枢迁移。
        movement_state_values: list[tuple[str, dict[str, Any], int]] = []
        # Z/Zn 监控事件：跟踪各线段相对中枢中轴的位置、强弱和越界/楔形预警。
        center_monitor_values: list[tuple[str, dict[str, Any], int]] = []
        previous_center: tuple[StructuralCenter, str] | None = None
        for center, object_id in zip(segment_centers, segment_center_ids, strict=True):
            components = segment_lines[center.base_index : center.end_index + 1]
            z_i64 = (center.zd_i64 + center.zg_i64) // 2
            phase = "centre_oscillation" if len(components) > 3 else "consolidation"
            movement_state_values.append(
                (
                    _stable_id("movement-state", object_id, phase),
                    {
                        "start_bar_index": center.start_bar_index,
                        "start_time": center.start_time,
                        "end_bar_index": center.end_bar_index,
                        "end_time": center.end_time,
                        "price_i64": z_i64,
                        "state_type": phase,
                        "direction": None,
                        "analysis_level": "segment",
                        "reference_object_id": object_id,
                        "confirmed": True,
                        "confirmed_at_bar_index": center.known_at_bar_index,
                    },
                    center.known_at_bar_index,
                )
            )
            if previous_center is not None:
                prior, prior_id = previous_center
                # The same core-and-comparison-envelope verdict drives both
                # the center object and its migration state event.
                migration = (
                    "up"
                    if center.relative_dir == "UP"
                    else "down"
                    if center.relative_dir == "DOWN"
                    else None
                )
                if migration is not None:
                    movement_state_values.append(
                        (
                            _stable_id("movement-state", prior_id, object_id, migration),
                            {
                                "start_bar_index": prior.end_bar_index,
                                "start_time": prior.end_time,
                                "end_bar_index": center.end_bar_index,
                                "end_time": center.end_time,
                                "price_i64": z_i64,
                                "state_type": f"centre_migration_{migration}",
                                "direction": migration,
                                "analysis_level": "segment",
                                "reference_object_id": object_id,
                                "confirmed": True,
                                "confirmed_at_bar_index": center.known_at_bar_index,
                            },
                            center.known_at_bar_index,
                        )
                    )
            previous_center = (center, object_id)

            for observation in classify_zn_components(
                core_low_i64=center.zd_i64,
                core_high_i64=center.zg_i64,
                components=components,
            ):
                center_monitor_values.append(
                    (
                        _stable_id("center-monitor", object_id, observation.component_object_id),
                        {
                            "bar_index": observation.bar_index,
                            "time": observation.time,
                            "z_i64": observation.z_i64,
                            "zn_i64": observation.zn_i64,
                            "z_twice_i64": observation.z_twice_i64,
                            "zn_twice_i64": observation.zn_twice_i64,
                            "core_low_i64": observation.core_low_i64,
                            "core_high_i64": observation.core_high_i64,
                            "range_high_i64": observation.range_high_i64,
                            "range_low_i64": observation.range_low_i64,
                            "component_ordinal": observation.component_ordinal,
                            "component_direction": observation.component_direction,
                            "relative_position": observation.relative_position,
                            "oscillation_bias": observation.oscillation_bias,
                            "breakout_warning": observation.breakout_warning,
                            "catalog_algorithm_id": "ALG-AUX-004",
                            "semantic_namespace": "auxiliary",
                            "evidence_level": "AUXILIARY",
                            "level_mapping_profile": "segment_center_components_v1",
                            "standard_signal": False,
                            "execution_allowed": False,
                            "confirms_third_point": False,
                            "analysis_level": "segment",
                            "reference_object_id": object_id,
                            "confirmed": True,
                            "confirmed_at_bar_index": observation.known_at_bar_index,
                        },
                        observation.known_at_bar_index,
                    )
                )

        divergence_specs = chan_divergences(
            segment_lines,
            segment_centers,
            segment_center_ids,
            self._macd_histogram,
            self._macd_area_cache,
            diff=self._macd_diff_by_bar,
            dea=self._macd_dea_by_bar,
            bi_lines=self.bi,
            bi_centers=self._bi_sublevel_centers,
        )
        divergence_values: list[tuple[str, dict[str, Any], int]] = []
        divergence_objects: list[tuple[str, ChanSignal]] = []
        # 背驰：使用线段、SEGMENT 实体中枢投影和 MACD 柱面积计算。
        for signal in divergence_specs:
            object_id = _stable_id(
                "divergence",
                signal.divergence_kind,
                segment_lines[signal.segment_index].object_id,
                signal.reference_object_id,
            )
            divergence_objects.append((object_id, signal))
            divergence_values.append(
                (object_id, _signal_payload(signal), signal.known_at_bar_index)
            )
        # Defer provisional revisions to the common end-of-bar path. A forced
        # structural rescan must not emit them before fractal/BI state events.
        finalized_ids = {object_id for object_id, _, _ in divergence_values}
        for previous in self.emitter.current("divergence"):
            object_id = str(previous["object_id"])
            if previous.get("status") != "forming" or object_id in finalized_ids:
                continue
            payload = {
                key: value
                for key, value in previous.items()
                if key not in {"object_id", "object_revision", "known_at_bar_index"}
            }
            divergence_values.append((object_id, payload, int(previous["known_at_bar_index"])))

        trade_point_by_id: dict[str, tuple[str, dict[str, Any], int]] = {}
        for signal in chan_first_point_candidates(
            segment_lines,
            segment_centers,
            segment_center_ids,
            self._macd_histogram,
            self._macd_area_cache,
            level_id="L0",
        ):
            object_id = _stable_id(
                "trade-point",
                signal.signal_type,
                signal.level_id,
                segment_lines[signal.segment_index].object_id,
            )
            trade_point_by_id[object_id] = (
                object_id,
                _signal_payload(signal),
                signal.known_at_bar_index,
            )
        # 买卖点：消费 SEGMENT 实体中枢投影和背驰对象。
        for signal in chan_trade_points(
            segment_lines, segment_centers, segment_center_ids, divergence_objects
        ):
            object_id = _stable_id(
                "trade-point",
                signal.signal_type,
                signal.level_id,
                segment_lines[signal.segment_index].object_id,
            )
            trade_point_by_id[object_id] = (
                object_id,
                _signal_payload(signal),
                signal.known_at_bar_index,
            )
        trade_point_values = self._retain_invalidated_first_points(
            list(trade_point_by_id.values()), known_at_bar_index
        )
        self._sync_objects("movement_state", movement_state_values, known_at_bar_index)
        self._sync_objects("center_monitor", center_monitor_values, known_at_bar_index)
        self._sync_objects(
            "divergence",
            self._retain_invalidated_divergences(divergence_values, known_at_bar_index),
            known_at_bar_index,
        )
        self._sync_objects("trade_point", trade_point_values, known_at_bar_index)
        logger.debug(
            "chan.structures.updated",
            "Chan structures updated after confirmed bi change",
            {
                "bar_index": known_at_bar_index,
                "merged_bar_count": len(self.included),
                "fractal_count": len(self.fractals),
                "bi_count": len(self.bi),
                "segment_count": len(segments),
                "local_segment_center_count": len(segment_centers),
                "movement_state_count": len(movement_state_values),
                "center_monitor_count": len(center_monitor_values),
                "divergence_count": len(divergence_values),
                "trade_point_count": len(trade_point_values),
                "event_count": len(self.emitter.events),
            },
        )

    def _center_units(
        self, lines: list[LineObject], unit_kind: Literal["BI", "SEGMENT"]
    ) -> tuple[tuple[CenterUnit, ...], dict[str, LineObject], dict[int, int]]:
        units: list[CenterUnit] = []
        by_id: dict[str, LineObject] = {}
        bar_by_time: dict[int, int] = {}
        for index, line in enumerate(lines):
            bar_by_time[line.start.time] = line.start.bar_index
            bar_by_time[line.end.time] = line.end.bar_index
            cache_key = (unit_kind, index, line)
            cached = self._center_unit_cache.get(cache_key)
            if cached is not None:
                units.append(cached)
                by_id[line.object_id] = line
                continue
            assert line.range_low_i64 is not None and line.range_high_i64 is not None
            source_revision = _stable_id(
                "center-unit-revision",
                line.object_id,
                line.start.bar_index,
                line.end.bar_index,
                line.start.price_i64,
                line.end.price_i64,
                line.range_low_i64,
                line.range_high_i64,
                line.confirmed_at_bar_index,
            )
            unit = CenterUnit(
                id=line.object_id,
                index=index,
                direction=line.direction,
                start_pivot_id=_stable_id(
                    "center-pivot",
                    unit_kind,
                    line.start.bar_index,
                    line.start.time,
                    line.start.price_i64,
                ),
                end_pivot_id=_stable_id(
                    "center-pivot",
                    unit_kind,
                    line.end.bar_index,
                    line.end.time,
                    line.end.price_i64,
                ),
                start_time=line.start.time,
                end_time=line.end.time,
                start_price_tick=self._to_price_tick(line.start.price_i64),
                end_price_tick=self._to_price_tick(line.end.price_i64),
                low_tick=self._to_price_tick(line.range_low_i64),
                high_tick=self._to_price_tick(line.range_high_i64),
                confirmed_at=line.confirmed_at_bar_index,
                source_revision=source_revision,
            )
            self._center_unit_cache[cache_key] = unit
            units.append(unit)
            by_id[line.object_id] = line
        return tuple(units), by_id, bar_by_time

    def _to_price_tick(self, price_i64: int) -> int:
        if price_i64 % self.price_tick_i64:
            raise ValueError("local center unit price is not aligned to the minimum quotation tick")
        return price_i64 // self.price_tick_i64

    def _publish_local_center_previews(self, known_at_bar_index: int) -> None:
        """Project upstream tail units separately from confirmed boundary state."""
        for unit_kind in ("BI", "SEGMENT"):
            accumulator = self._local_center_accumulators.get(unit_kind)
            last = None if accumulator is None else accumulator.last_unit
            candidates: list[dict[str, Any]] = []
            if last is not None:
                if unit_kind == "BI":
                    candidate = (
                        None
                        if self._active_bi_candidate_id is None
                        else self.emitter.get("bi", self._active_bi_candidate_id)
                    )
                    if candidate is not None:
                        candidates = [candidate]
                else:
                    candidates = self.emitter.current("segment")
            candidates = [
                candidate
                for candidate in candidates
                if candidate.get("status") == "candidate"
                and not candidate.get("confirmed")
                and last is not None
                and candidate["start_time"] == last.end_time
                and candidate["start_price_i64"] == last.end_price_tick * self.price_tick_i64
                and candidate["direction"] != last.direction
            ]
            preview = None
            if candidates and last is not None and accumulator is not None:
                candidate = max(
                    candidates, key=lambda value: (value["end_time"], value["object_id"])
                )
                tail = CenterUnit(
                    id=str(candidate["object_id"]),
                    index=last.index + 1,
                    direction=candidate["direction"],
                    start_pivot_id=last.end_pivot_id,
                    end_pivot_id=_stable_id("preview-pivot", candidate["object_id"]),
                    start_time=int(candidate["start_time"]),
                    end_time=int(candidate["end_time"]),
                    start_price_tick=self._to_price_tick(int(candidate["start_price_i64"])),
                    end_price_tick=self._to_price_tick(int(candidate["end_price_i64"])),
                    low_tick=self._to_price_tick(int(candidate["range_low_i64"])),
                    high_tick=self._to_price_tick(int(candidate["range_high_i64"])),
                    confirmed_at=None,
                    source_revision=_stable_id("preview-unit-revision", candidate),
                )
                preview = accumulator.preview(tail, known_at=known_at_bar_index)
            previous_id = self._local_center_preview_ids.get(unit_kind)
            if previous_id is not None and (preview is None or previous_id != preview.id):
                self.emitter.delete(known_at_bar_index, "center_audit_event", previous_id)
                del self._local_center_preview_ids[unit_kind]
            if preview is None or accumulator is None:
                continue
            current_preview = self.emitter.get("center_audit_event", preview.id)
            if current_preview is not None and all(
                current_preview.get(name) == value
                for name, value in {
                    "source_revision": preview.source_revision,
                    "preview_state": preview.state,
                    "comparison_i64": preview.comparison_value * self.price_tick_i64,
                }.items()
            ):
                continue
            center = accumulator.result().centers[-1]
            frame = inspect.currentframe()
            source_line = frame.f_lineno if frame is not None else 1
            del frame
            self.emitter.upsert(
                known_at_bar_index,
                "center_audit_event",
                preview.id,
                {
                    "event_type": "PREVIEW_UPDATED",
                    "center_id": preview.center_id,
                    "unit_ids": list(dict.fromkeys((preview.exit_unit_id, preview.unit_id))),
                    "zd_i64": center.zd_tick * self.price_tick_i64,
                    "zg_i64": center.zg_tick * self.price_tick_i64,
                    "comparison_i64": preview.comparison_value * self.price_tick_i64,
                    "event_bar_index": known_at_bar_index,
                    "event_time": self.raw_bars[-1].time,
                    "rule_version": preview.rule_version,
                    "source_file": "python/src/tvbt/chan/engine.py",
                    "source_line": source_line,
                    "preview_state": preview.state,
                    "preview_confirmed": False,
                    "preview_direction": preview.direction,
                    "source_revision": preview.source_revision,
                },
            )
            self._local_center_preview_ids[unit_kind] = preview.id

    @staticmethod
    def _comparison_components(
        components: list[LineObject],
        *,
        unit_kind: Literal["BI", "SEGMENT"],
        first_seed_id: str,
        previous_exit_id: str | None,
    ) -> tuple[list[LineObject], str | None]:
        """Omit a shared SEGMENT entry only from migration DD/GG, not construction."""
        if unit_kind != "SEGMENT" or previous_exit_id != first_seed_id:
            return components, None
        if len(components) < 3 or components[0].object_id != first_seed_id:
            raise AssertionError("shared entry must lead a three-segment center body")
        return components[1:], first_seed_id

    def _local_center_payload(
        self,
        center: LocalCenter,
        lines: dict[str, LineObject],
        bar_by_time: dict[int, int],
        previous_center: LocalCenter | None = None,
        previous_previous_center: LocalCenter | None = None,
    ) -> dict[str, Any]:
        first_seed = lines[center.seed_ids[0]]
        last_seed = lines[center.seed_ids[-1]]
        relation = (
            None if previous_center is None else compare_center_boundaries(previous_center, center)
        )
        ordered_lines = sorted(lines.values(), key=lambda item: item.start.time)

        def body_components(item: LocalCenter) -> list[LineObject]:
            return [
                line
                for line in ordered_lines
                if line.start.time >= item.body_start
                and (
                    (item.body_end is None and line.end.time <= item.observed_end)
                    or (item.body_end is not None and line.start.time < item.body_end)
                )
            ]

        components = body_components(center)
        comparison_components, excluded_entry_id = self._comparison_components(
            components,
            unit_kind=center.unit_kind,
            first_seed_id=center.seed_ids[0],
            previous_exit_id=None if previous_center is None else previous_center.exit_id,
        )
        body_end_time = center.body_end or center.observed_end
        body_end_bar_index = bar_by_time[body_end_time]
        outer_low = min(line.range_low_i64 for line in components if line.range_low_i64 is not None)
        outer_high = max(
            line.range_high_i64 for line in components if line.range_high_i64 is not None
        )
        comparison_low = min(
            line.range_low_i64 for line in comparison_components if line.range_low_i64 is not None
        )
        comparison_high = max(
            line.range_high_i64 for line in comparison_components if line.range_high_i64 is not None
        )
        relative_dir: Literal["UP", "DOWN", "OVERLAP", "UNKNOWN"] = "UNKNOWN"
        if previous_center is not None:
            previous_components = body_components(previous_center)
            previous_components, _ = self._comparison_components(
                previous_components,
                unit_kind=previous_center.unit_kind,
                first_seed_id=previous_center.seed_ids[0],
                previous_exit_id=(
                    None if previous_previous_center is None else previous_previous_center.exit_id
                ),
            )
            previous_low = min(
                line.range_low_i64 for line in previous_components if line.range_low_i64 is not None
            )
            previous_high = max(
                line.range_high_i64
                for line in previous_components
                if line.range_high_i64 is not None
            )
            relative_dir = classify_center_relative_direction(
                previous_center.zd_tick,
                previous_center.zg_tick,
                previous_low,
                previous_high,
                center.zd_tick,
                center.zg_tick,
                comparison_low,
                comparison_high,
                previous_confirmed=previous_center.status == "CLOSED",
            )
        return {
            "stream_key": center.stream_key,
            "rule_version": center.rule_version,
            "previous_center_id": None if relation is None else relation.previous_center_id,
            "formation_dir": "UP" if first_seed.direction == "up" else "DOWN",
            "relative_dir": relative_dir,
            "core_relation": None if relation is None else relation.core_relation,
            "higher_level_review_required": (
                relation is not None
                and relation.core_relation in {"CORE_ABOVE", "CORE_BELOW"}
                and relative_dir == "OVERLAP"
            ),
            "trend_status": "UNVERIFIED",
            "unit_kind": center.unit_kind,
            "structural_level": center.structural_level,
            "scan_floor": center.scan_floor,
            "seed_ids": list(center.seed_ids),
            "zd_i64": center.zd_tick * self.price_tick_i64,
            "zg_i64": center.zg_tick * self.price_tick_i64,
            "z_i64": (center.zd_tick + center.zg_tick) * self.price_tick_i64 // 2,
            "dd_i64": outer_low,
            "gg_i64": outer_high,
            "comparison_dd_i64": comparison_low,
            "comparison_gg_i64": comparison_high,
            "comparison_excluded_entry_id": excluded_entry_id,
            "start_bar_index": first_seed.start.bar_index,
            "start_time": center.body_start,
            "end_bar_index": body_end_bar_index,
            "end_time": body_end_time,
            "analysis_level": center.structural_level,
            "component_kind": center.unit_kind.lower(),
            "component_count": len(components),
            "confirmed": True,
            "confirmed_at_bar_index": center.formed_at,
            "leave_direction": center.break_direction,
            "seed_start_bar_index": first_seed.start.bar_index,
            "seed_start_time": center.seed_start,
            "seed_end_bar_index": last_seed.end.bar_index,
            "seed_end_time": center.seed_end,
            "formed_at_bar_index": center.formed_at,
            "body_start_bar_index": first_seed.start.bar_index,
            "body_start_time": center.body_start,
            "body_end_bar_index": None if center.body_end is None else body_end_bar_index,
            "body_end_time": center.body_end,
            "observed_start_bar_index": first_seed.start.bar_index,
            "observed_start_time": center.observed_start,
            "observed_end_bar_index": bar_by_time[center.observed_end],
            "observed_end_time": center.observed_end,
            "observed_low_i64": center.observed_low * self.price_tick_i64,
            "observed_high_i64": center.observed_high * self.price_tick_i64,
            "status": center.status,
            "pending_exit_id": center.pending_exit_id,
            "exit_id": center.exit_id,
            "first_retest_id": center.first_retest_id,
            "entry_id": center.entry_id,
            "local_entry": center.local_entry,
            "break_direction": center.break_direction,
            "break_confirmed_at_bar_index": center.break_confirmed_at,
            "parent_id": center.parent_id,
            "left_context_incomplete": center.left_context_incomplete,
            "roles_overlap_seed": center.roles_overlap_seed,
            "source_revision": center.source_revision,
        }

    @staticmethod
    def _center_connection_payload(
        connection: CenterConnection,
        lines: dict[str, LineObject],
        *,
        unit_kind: Literal["BI", "SEGMENT"],
        structural_level: str,
    ) -> dict[str, Any]:
        first = lines[connection.unit_ids[0]]
        last = lines[connection.unit_ids[-1]]
        return {
            "stream_key": connection.stream_key,
            "rule_version": connection.rule_version,
            "unit_kind": unit_kind,
            "structural_level": structural_level,
            "from_center_id": connection.from_center_id,
            "to_center_id": connection.to_center_id,
            "ordered_unit_ids": list(connection.unit_ids),
            "exit_unit_id": connection.exit_unit_id,
            "entry_unit_id": connection.entry_unit_id,
            "first_retest_id": connection.first_retest_id,
            "start_bar_index": first.start.bar_index,
            "start_time": first.start.time,
            "end_bar_index": last.end.bar_index,
            "end_time": last.end.time,
            "confirmed_at_bar_index": connection.confirmed_at,
            "roles_overlap_seed": connection.roles_overlap_seed,
            "source_revision": connection.source_revision,
        }

    def _center_audit_payload(
        self, event: CenterAuditEvent, bar_by_time: dict[int, int]
    ) -> dict[str, Any]:
        return {
            "event_type": event.event_type,
            "center_id": event.center_id,
            "unit_ids": list(event.unit_ids),
            "zd_i64": event.zd_tick * self.price_tick_i64,
            "zg_i64": event.zg_tick * self.price_tick_i64,
            "comparison_i64": None
            if event.comparison_value is None
            else event.comparison_value
            * (1 if event.event_type == "SEARCH_RESTARTED" else self.price_tick_i64),
            "event_bar_index": bar_by_time[event.event_time],
            "event_time": event.event_time,
            "rule_version": event.rule_version,
            "source_file": event.source_file,
            "source_line": event.source_line,
        }

    def _update_local_center_objects(self, known_at_bar_index: int) -> None:
        center_values: list[tuple[str, dict[str, Any], int]] = []
        connection_values: list[tuple[str, dict[str, Any], int]] = []
        audit_values: list[tuple[CenterAuditEvent, dict[int, int]]] = []
        streams: tuple[tuple[Literal["BI", "SEGMENT"], str, list[LineObject]], ...] = (
            ("BI", "stroke", self.bi),
            ("SEGMENT", "segment", self._segment_lines),
        )
        for unit_kind, structural_level, lines in streams:
            units, by_id, bar_by_time = self._center_units(lines, unit_kind)
            stream = CenterStreamKey(
                symbol=self.stream_symbol,
                timeframe=self.stream_timeframe,
                unit_kind=unit_kind,
                structural_level=structural_level,
                algorithm_version=self.algorithm_version,
                anchor_id=self.stream_anchor_id,
            )
            accumulator = self._local_center_accumulators.get(unit_kind)
            if accumulator is None:
                accumulator = LocalCenterAccumulator(stream, left_context_complete=False)
                self._local_center_accumulators[unit_kind] = accumulator
            decomposition = accumulator.update(units)
            center_values.extend(
                (
                    center.id,
                    self._local_center_payload(
                        center,
                        by_id,
                        bar_by_time,
                        None if index == 0 else decomposition.centers[index - 1],
                        None if index < 2 else decomposition.centers[index - 2],
                    ),
                    center.break_confirmed_at or center.formed_at,
                )
                for index, center in enumerate(decomposition.centers)
            )
            connection_values.extend(
                (
                    connection.id,
                    self._center_connection_payload(
                        connection,
                        by_id,
                        unit_kind=unit_kind,
                        structural_level=structural_level,
                    ),
                    connection.confirmed_at,
                )
                for connection in decomposition.connections
            )
            audit_values.extend((event, bar_by_time) for event in accumulator.updated_events)

        previous_centers = {
            str(item["object_id"]): item for item in self.emitter.current("local_center")
        }
        self._sync_objects("local_center", center_values, known_at_bar_index)
        self._sync_objects("center_connection", connection_values, known_at_bar_index)
        for event, bar_by_time in audit_values:
            payload = self._center_audit_payload(event, bar_by_time)
            if self._center_audit_payload_cache.get(event.id) == payload:
                continue
            self.emitter.upsert(
                max(event.known_at, known_at_bar_index),
                "center_audit_event",
                event.id,
                payload,
            )
            self._center_audit_payload_cache[event.id] = payload
        for object_id, payload, _ in center_values:
            previous = previous_centers.get(object_id)
            if previous is None or all(
                previous.get(name) == value for name, value in payload.items()
            ):
                continue
            revision = int(previous["object_revision"]) + 1
            audit_id = _stable_id(object_id, "CENTER_REVISED", revision)
            self.emitter.upsert(
                known_at_bar_index,
                "center_audit_event",
                audit_id,
                {
                    "event_type": "CENTER_REVISED",
                    "center_id": object_id,
                    "unit_ids": payload["seed_ids"],
                    "zd_i64": payload["zd_i64"],
                    "zg_i64": payload["zg_i64"],
                    "comparison_i64": None,
                    "event_bar_index": known_at_bar_index,
                    "event_time": self.raw_bars[-1].time,
                    "rule_version": LOCAL_CENTER_RULE_VERSION,
                    "source_file": "python/src/tvbt/chan/engine.py",
                    "source_line": self._update_local_center_objects.__code__.co_firstlineno,
                },
            )

    def _segment_structural_centers(self) -> tuple[list[StructuralCenter], list[str]]:
        """Project authoritative SEGMENT local centers into the signal algorithms."""
        accumulator = self._local_center_accumulators.get("SEGMENT")
        if accumulator is None:
            return [], []
        positions = {line.object_id: index for index, line in enumerate(self._segment_lines)}

        def envelope(components: list[LineObject]) -> tuple[int, int]:
            return (
                min(line.range_low_i64 for line in components if line.range_low_i64 is not None),
                max(line.range_high_i64 for line in components if line.range_high_i64 is not None),
            )

        centers: list[StructuralCenter] = []
        center_ids: list[str] = []
        previous_center: LocalCenter | None = None
        for center in accumulator.result().centers:
            if any(seed_id not in positions for seed_id in center.seed_ids):
                continue
            base_index = positions[center.seed_ids[0]]
            seed_end_index = positions[center.seed_ids[-1]]
            exit_index = None if center.exit_id is None else positions.get(center.exit_id)
            end_index = (
                max(seed_end_index, exit_index - 1)
                if exit_index is not None
                else max(
                    index
                    for index, line in enumerate(self._segment_lines)
                    if line.end.time <= center.observed_end
                )
            )
            end_line = self._segment_lines[end_index]
            current_components = self._segment_lines[base_index : end_index + 1]
            comparison_components, excluded_entry_id = self._comparison_components(
                current_components,
                unit_kind="SEGMENT",
                first_seed_id=center.seed_ids[0],
                previous_exit_id=None if previous_center is None else previous_center.exit_id,
            )
            current_dd, current_gg = envelope(comparison_components)
            relative_dir: Literal["UP", "DOWN", "OVERLAP", "UNKNOWN"] = "UNKNOWN"
            if centers:
                previous_projected = centers[-1]
                previous_dd = previous_projected.comparison_dd_i64
                previous_gg = previous_projected.comparison_gg_i64
                assert previous_dd is not None and previous_gg is not None
                relative_dir = classify_center_relative_direction(
                    previous_projected.zd_i64,
                    previous_projected.zg_i64,
                    previous_dd,
                    previous_gg,
                    center.zd_tick * self.price_tick_i64,
                    center.zg_tick * self.price_tick_i64,
                    current_dd,
                    current_gg,
                    previous_confirmed=previous_projected.status == "left",
                )
            centers.append(
                StructuralCenter(
                    base_index=base_index,
                    seed_end_index=seed_end_index,
                    end_index=end_index,
                    exit_index=exit_index,
                    start_bar_index=self._segment_lines[base_index].start.bar_index,
                    end_bar_index=end_line.end.bar_index,
                    start_time=self._segment_lines[base_index].start.time,
                    end_time=end_line.end.time,
                    zd_i64=center.zd_tick * self.price_tick_i64,
                    zg_i64=center.zg_tick * self.price_tick_i64,
                    known_at_bar_index=center.break_confirmed_at or center.formed_at,
                    status=(
                        "left"
                        if center.status == "CLOSED"
                        else "extended"
                        if end_index > seed_end_index
                        else "confirmed"
                    ),
                    leave_direction=center.break_direction,
                    formation_dir=(
                        "UP" if self._segment_lines[base_index].direction == "up" else "DOWN"
                    ),
                    relative_dir=relative_dir,
                    previous_center_id=None if previous_center is None else previous_center.id,
                    comparison_dd_i64=current_dd,
                    comparison_gg_i64=current_gg,
                    comparison_excluded_entry_id=excluded_entry_id,
                )
            )
            center_ids.append(center.id)
            previous_center = center
        return centers, center_ids

    def _bi_sublevel_centers(self) -> list[BiCenterEvidence]:
        """Expose only causally closed BI centers to segment-c proof."""
        accumulator = self._local_center_accumulators.get("BI")
        if accumulator is None:
            return []
        bars_by_time = {
            point.time: point.bar_index for line in self.bi for point in (line.start, line.end)
        }
        result: list[BiCenterEvidence] = []
        for center in accumulator.result().centers:
            if (
                center.status != "CLOSED"
                or center.body_end is None
                or center.body_start not in bars_by_time
                or center.body_end not in bars_by_time
                or center.break_confirmed_at is None
            ):
                continue
            result.append(
                BiCenterEvidence(
                    object_id=center.id,
                    start_bar_index=bars_by_time[center.body_start],
                    end_bar_index=bars_by_time[center.body_end],
                    known_at_bar_index=center.break_confirmed_at,
                )
            )
        return result

    def _forming_segment_observation(self) -> LineObject | None:
        """Extend the scanner's candidate leg with only bars observed so far."""
        if not self._segment_lines or not self._segment_specs:
            return None
        last_confirmed = self._segment_lines[-1]
        candidates = [
            segment
            for segment in self._segment_specs
            if not segment.confirmed and segment.start_bar_index == last_confirmed.end.bar_index
        ]
        if not candidates:
            return None
        candidate = max(candidates, key=lambda segment: segment.end_bar_index)
        current = self.raw_bars[-1]
        if candidate.start_bar_index >= current.bar_index:
            return None
        start_offset = candidate.start_bar_index - self.raw_bars[0].bar_index
        observed = self.raw_bars[start_offset:]
        if not observed or observed[0].bar_index != candidate.start_bar_index:
            return None
        low_bar = min(observed, key=lambda bar: (bar.low_i64, bar.bar_index))
        high_bar = max(observed, key=lambda bar: (bar.high_i64, -bar.bar_index))
        direction: Literal["up", "down"] = "up" if candidate.up else "down"
        object_id = _stable_id("segment", self.bi[candidate.start_index].object_id, direction)
        start = Fractal(
            f"{object_id}-forming-start",
            "bottom" if candidate.up else "top",
            candidate.start_index,
            candidate.start_bar_index,
            candidate.start_time,
            candidate.start_price_i64,
            current.bar_index,
            current.bar_index,
        )
        end = Fractal(
            f"{object_id}-forming-observed",
            "top" if candidate.up else "bottom",
            candidate.end_index,
            current.bar_index,
            current.time,
            current.close_i64,
            current.bar_index,
            current.bar_index,
        )
        return LineObject(
            object_id,
            start,
            end,
            direction,
            current.bar_index,
            current.bar_index,
            low_bar.low_i64,
            high_bar.high_i64,
            low_bar.bar_index,
            high_bar.bar_index,
            "forming_observed_bars_v1",
        )

    def _forming_signal_values(
        self,
        centers: list[StructuralCenter] | None = None,
        center_ids: list[str] | None = None,
    ) -> list[tuple[str, dict[str, Any], int]]:
        line = self._forming_segment_observation()
        if line is None:
            return []
        if centers is None or center_ids is None:
            centers, center_ids = self._segment_structural_centers()
        signals = chan_forming_divergences(
            self._segment_lines,
            centers,
            center_ids,
            line,
            self._macd_histogram,
            diff=self._macd_diff_by_bar,
            dea=self._macd_dea_by_bar,
        )
        return [
            (
                _stable_id(
                    "divergence", signal.divergence_kind, line.object_id, signal.reference_object_id
                ),
                _signal_payload(signal),
                signal.known_at_bar_index,
            )
            for signal in signals
        ]

    def _refresh_forming_divergences(self, known_at_bar_index: int) -> None:
        """Revise provisional evidence at each bar without changing confirmed centers."""
        current_forming = {
            str(row["object_id"]): row
            for row in self.emitter.current("divergence")
            if row.get("status") == "forming"
        }
        desired = {object_id: payload for object_id, payload, _ in self._forming_signal_values()}
        for object_id, previous in current_forming.items():
            if object_id in desired:
                continue
            payload = {
                key: value
                for key, value in previous.items()
                if key not in {"object_id", "object_revision", "known_at_bar_index"}
            }
            payload.update(
                status="invalidated",
                invalidation_reason="forming_leg_revised_or_strength_recovered",
                confirmed=False,
                confirmed_at_bar_index=None,
            )
            self.emitter.upsert(known_at_bar_index, "divergence", object_id, payload)
        for object_id, payload in desired.items():
            old_forming = current_forming.get(object_id)
            if old_forming is not None and all(
                old_forming.get(key) == value for key, value in payload.items()
            ):
                continue
            self.emitter.upsert(known_at_bar_index, "divergence", object_id, payload)

    @staticmethod
    def _common_prefix_length(left: list[Any], right: list[Any]) -> int:
        """返回两个结构序列完全相同的前缀长度。"""
        count = 0
        for old, new in zip(left, right, strict=False):
            if old != new:
                break
            count += 1
        return count

    def _sync_objects(
        self,
        object_type: str,
        values: list[tuple[str, dict[str, Any], int]],
        known_at_bar_index: int,
    ) -> None:
        """把重新扫描得到的目标对象集合与事件收集器中的当前集合对齐。"""
        current_values = {
            str(item["object_id"]): item for item in self.emitter.current(object_type)
        }
        desired = {object_id for object_id, _, _ in values}
        for object_id in sorted(current_values.keys() - desired):
            self.emitter.delete(known_at_bar_index, object_type, object_id)
        for object_id, payload, known_at in values:
            previous = current_values.get(object_id)
            if previous is not None and all(
                previous.get(name) == value for name, value in payload.items()
            ):
                continue
            # 结构对象可能要等后续笔改变参考扫描基点后才能发现。
            # 事件时间必须记录发现时刻，不能回填到当时尚不可知的历史形成位置。
            event_known_at = max(known_at, known_at_bar_index)
            self.emitter.upsert(event_known_at, object_type, object_id, payload)

    def _retain_invalidated_first_points(
        self,
        values: list[tuple[str, dict[str, Any], int]],
        known_at_bar_index: int,
    ) -> list[tuple[str, dict[str, Any], int]]:
        """Keep rejected B1/S1 candidates as auditable lifecycle revisions."""
        desired = {object_id for object_id, _, _ in values}
        for previous in self.emitter.current("trade_point"):
            object_id = str(previous["object_id"])
            if object_id in desired or previous.get("catalog_algorithm_id") != "ALG-SIG-001":
                continue
            if previous.get("status") == "invalidated":
                payload = {
                    key: value
                    for key, value in previous.items()
                    if key not in {"object_id", "object_revision", "known_at_bar_index"}
                }
            elif previous.get("status") == "candidate":
                payload = {
                    key: value
                    for key, value in previous.items()
                    if key not in {"object_id", "object_revision", "known_at_bar_index"}
                }
                payload.update(
                    {
                        "status": "invalidated",
                        "invalidation_reason": "trend_structure_revised",
                        "catalog_event": (
                            "B1_invalidated"
                            if previous.get("signal_type") == "buy_1"
                            else "S1_invalidated"
                        ),
                        "confirmed": False,
                        "confirmed_at_bar_index": None,
                    }
                )
            else:
                continue
            values.append((object_id, payload, known_at_bar_index))
        return values

    def _retain_invalidated_divergences(
        self,
        values: list[tuple[str, dict[str, Any], int]],
        known_at_bar_index: int,
    ) -> list[tuple[str, dict[str, Any], int]]:
        """Keep a revised-away divergence as an explicit causal invalidation."""
        desired = {object_id for object_id, _, _ in values}
        for previous in self.emitter.current("divergence"):
            object_id = str(previous["object_id"])
            if object_id in desired:
                continue
            payload = {
                key: value
                for key, value in previous.items()
                if key not in {"object_id", "object_revision", "known_at_bar_index"}
            }
            if payload.get("status") != "invalidated":
                payload.update(
                    {
                        "status": "invalidated",
                        "invalidation_reason": "decomposition_or_strength_revised",
                        "confirmed": False,
                        "confirmed_at_bar_index": None,
                    }
                )
            values.append((object_id, payload, known_at_bar_index))
        return values

    def result_rows(self) -> dict[str, list[dict[str, Any]]]:
        """导出当前对象快照，按各对象的图形起点排序，供 Parquet 写入或 API 返回。"""
        return {
            "processed_bars": sorted(
                self.emitter.current("processed_bar"), key=lambda item: item["normalized_index"]
            ),
            "fractals": sorted(self.emitter.current("fractal"), key=lambda item: item["bar_index"]),
            "bi": sorted(
                self.emitter.current("bi"),
                key=lambda item: (item["start_bar_index"], item["object_id"]),
            ),
            "bi_states": sorted(
                self.emitter.current("bi_state"), key=lambda item: item["bar_index"]
            ),
            "segments": sorted(
                self.emitter.current("segment"), key=lambda item: item["start_bar_index"]
            ),
            "local_centers": sorted(
                self.emitter.current("local_center"),
                key=lambda item: (
                    item["body_start_bar_index"],
                    item["unit_kind"],
                    item["object_id"],
                ),
            ),
            "center_connections": sorted(
                self.emitter.current("center_connection"),
                key=lambda item: (item["start_bar_index"], item["object_id"]),
            ),
            "center_audit_events": sorted(
                self.emitter.current("center_audit_event"),
                key=lambda item: (
                    item["known_at_bar_index"],
                    item["event_bar_index"],
                    item["object_id"],
                ),
            ),
            "movement_states": sorted(
                self.emitter.current("movement_state"), key=lambda item: item["start_bar_index"]
            ),
            "center_monitors": sorted(
                self.emitter.current("center_monitor"),
                key=lambda item: (item["bar_index"], item["object_id"]),
            ),
            "divergences": sorted(
                self.emitter.current("divergence"), key=lambda item: item["bar_index"]
            ),
            "trade_points": sorted(
                self.emitter.current("trade_point"),
                key=lambda item: (item["bar_index"], item["object_id"]),
            ),
        }

    def export_state(self) -> dict[str, Any]:
        """导出可恢复状态，用于长任务检查点，不包含临时运行环境对象。"""
        return {
            "parameters": asdict(self.parameters),
            "raw_bars": [asdict(item) for item in self.raw_bars],
            "included": [asdict(item) for item in self.included],
            "fractals": [asdict(item) for item in self.fractals],
            "fractal_candidate": (
                asdict(self._fractal_candidate) if self._fractal_candidate is not None else None
            ),
            "bi": [_line_state(item) for item in self.bi],
            "bi_scores": self._bi_scores,
            "bi_predecessors": self._bi_predecessors,
            "best_bi_endpoint": self._best_bi_endpoint,
            "bi_path_positions": self._bi_path_positions,
            "active_bi_candidate_id": self._active_bi_candidate_id,
            "bi_live_state": self._bi_live_state,
            "stream_identity": {
                "price_tick_i64": self.price_tick_i64,
                "symbol": self.stream_symbol,
                "timeframe": self.stream_timeframe,
                "anchor_id": self.stream_anchor_id,
            },
            "local_center_accumulators": {
                key: accumulator.state()
                for key, accumulator in self._local_center_accumulators.items()
            },
            "center_audit_payload_cache": self._center_audit_payload_cache,
            "local_center_preview_ids": self._local_center_preview_ids,
            "emitter": self.emitter.state(),
        }

    @classmethod
    def from_state(cls, state: dict[str, Any]) -> ChanEngine:
        """从检查点恢复引擎，并重建 MACD 状态和事件收集器。"""
        identity = state.get("stream_identity", {})
        engine = cls(
            ChanParameters(**state["parameters"]),
            stream_symbol=str(identity.get("symbol", "UNKNOWN")),
            stream_timeframe=str(identity.get("timeframe", "unknown")),
            stream_anchor_id=str(identity.get("anchor_id", "full-history")),
            price_tick_i64=int(identity.get("price_tick_i64", 1)),
        )
        engine.raw_bars = [RawBar(**item) for item in state["raw_bars"]]
        for bar in engine.raw_bars:
            engine._append_macd(bar)
        engine.included = [IncludedBar(**item) for item in state["included"]]
        engine.fractals = [Fractal(**item) for item in state["fractals"]]
        candidate = state.get("fractal_candidate")
        engine._fractal_candidate = Fractal(**candidate) if candidate is not None else None
        fractals = {item.object_id: item for item in engine.fractals}
        engine.bi = [_line_from_state(item, fractals) for item in state["bi"]]
        engine._bi_scores = [int(value) for value in state["bi_scores"]]
        engine._bi_predecessors = [
            None if value is None else int(value) for value in state["bi_predecessors"]
        ]
        best_endpoint = state["best_bi_endpoint"]
        engine._best_bi_endpoint = None if best_endpoint is None else int(best_endpoint)
        engine._bi_path_positions = [int(value) for value in state["bi_path_positions"]]
        active_candidate = state.get("active_bi_candidate_id")
        engine._active_bi_candidate_id = (
            str(active_candidate) if active_candidate is not None else None
        )
        engine._bi_live_state = state.get("bi_live_state", "SEEK_FIRST_FRACTAL")
        engine._fractal_by_normalized_index = {
            fractal.normalized_index: position for position, fractal in enumerate(engine.fractals)
        }
        engine._segment_accumulator.update(engine.bi, engine.raw_bars, 0)
        engine._local_center_accumulators = {
            str(key): LocalCenterAccumulator.from_state(value)
            for key, value in state.get("local_center_accumulators", {}).items()
        }
        engine._local_center_preview_ids = dict(state.get("local_center_preview_ids", {}))
        engine._center_audit_payload_cache = {
            str(key): dict(value)
            for key, value in state.get("center_audit_payload_cache", {}).items()
        }
        engine.emitter = EventEmitter.from_state(state["emitter"])
        return engine

    def _append_macd(self, bar: RawBar) -> None:
        """增量维护 MACD(12,26,9) 柱值，供线段背驰面积比较使用。"""
        close = float(bar.close_i64)
        if self._macd_fast is None or self._macd_slow is None or self._macd_dea is None:
            self._macd_fast = self._macd_slow = close
            self._macd_dea = 0.0
        else:
            self._macd_fast = (2.0 / 13.0) * close + (11.0 / 13.0) * self._macd_fast
            self._macd_slow = (2.0 / 27.0) * close + (25.0 / 27.0) * self._macd_slow
            diff = self._macd_fast - self._macd_slow
            self._macd_dea = (2.0 / 10.0) * diff + (8.0 / 10.0) * self._macd_dea
        diff = self._macd_fast - self._macd_slow
        self._macd_diff_by_bar[bar.bar_index] = diff
        self._macd_dea_by_bar[bar.bar_index] = self._macd_dea
        self._macd_histogram[bar.bar_index] = 2.0 * (diff - self._macd_dea)


def _signal_payload(signal: ChanSignal) -> dict[str, Any]:
    """统一背驰和买卖点信号的事件载荷结构。"""
    return {
        "bar_index": signal.bar_index,
        "time": signal.time,
        "price_i64": signal.price_i64,
        "signal_type": signal.signal_type,
        "divergence_kind": signal.divergence_kind,
        "divergence_profile": signal.divergence_profile,
        "formation_dir": signal.formation_dir,
        "relative_dir": signal.relative_dir,
        "a_object_id": signal.a_object_id,
        "b_object_id": signal.b_object_id,
        "a_center_id": signal.a_center_id,
        "b_center_id": signal.b_center_id,
        "macd_area_ratio": signal.macd_area_ratio,
        "macd_diff_reference_extreme": signal.macd_diff_reference_extreme,
        "macd_diff_current_extreme": signal.macd_diff_current_extreme,
        "macd_dea_reference_extreme": signal.macd_dea_reference_extreme,
        "macd_dea_current_extreme": signal.macd_dea_current_extreme,
        "macd_extreme_relation": signal.macd_extreme_relation,
        "macd_parameter_profile": signal.macd_parameter_profile,
        "c_contains_type3": signal.c_contains_type3,
        "c_meets_sublevel": signal.c_meets_sublevel,
        "c_sublevel_profile": signal.c_sublevel_profile,
        "c_sublevel_center_ids": list(signal.c_sublevel_center_ids),
        "c_type3_departure_id": signal.c_type3_departure_id,
        "c_type3_retest_id": signal.c_type3_retest_id,
        "c_proof_known_at_bar_index": signal.c_proof_known_at_bar_index,
        "signal_class": signal.signal_class,
        "strength": signal.strength,
        "reference_object_id": signal.reference_object_id,
        "macd_area_reference": signal.macd_area_reference,
        "macd_area_current": signal.macd_area_current,
        "status": signal.status,
        "invalidation_reason": signal.invalidation_reason,
        "level_id": signal.level_id,
        "lower_level_turn_object_id": signal.lower_level_turn_object_id,
        "catalog_event": signal.catalog_event,
        "catalog_algorithm_id": signal.catalog_algorithm_id,
        "evidence_profile": signal.evidence_profile,
        "comparison_reference_object_id": signal.comparison_reference_object_id,
        "comparison_current_object_id": signal.comparison_current_object_id,
        "comparison_rule": signal.comparison_rule,
        "new_extreme_satisfied": signal.new_extreme_satisfied,
        "departure_object_id": signal.departure_object_id,
        "return_object_id": signal.return_object_id,
        "return_ordinal": signal.return_ordinal,
        "boundary_profile": signal.boundary_profile,
        "boundary_relation": signal.boundary_relation,
        "return_depth_to_core_i64": signal.return_depth_to_core_i64,
        "return_depth_to_outer_i64": signal.return_depth_to_outer_i64,
        "follow_through_object_id": signal.follow_through_object_id,
        "follow_through_status": signal.follow_through_status,
        "confirmation_latency_bars": signal.confirmation_latency_bars,
        "reference_center_ordinal": signal.reference_center_ordinal,
        "older_center_count": signal.older_center_count,
        "center_chain_profile": signal.center_chain_profile,
        "confirmed": signal.status == "confirmed",
        "confirmed_at_bar_index": (
            signal.known_at_bar_index if signal.status == "confirmed" else None
        ),
    }


def _line_state(line: LineObject) -> dict[str, Any]:
    """把线对象转换为只含 ID 引用的检查点状态。"""
    return {
        "object_id": line.object_id,
        "start_id": line.start.object_id,
        "end_id": line.end.object_id,
        "direction": line.direction,
        "confirmed_at_bar_index": line.confirmed_at_bar_index,
        "known_at_bar_index": line.known_at_bar_index,
        "range_low_i64": line.range_low_i64,
        "range_high_i64": line.range_high_i64,
        "range_low_source_bar_index": line.range_low_source_bar_index,
        "range_high_source_bar_index": line.range_high_source_bar_index,
        "range_profile": line.range_profile,
    }


def _line_from_state(value: dict[str, Any], fractals: dict[str, Fractal]) -> LineObject:
    """根据检查点中的分型 ID 引用还原线对象。"""
    return LineObject(
        object_id=value["object_id"],
        start=fractals[value["start_id"]],
        end=fractals[value["end_id"]],
        direction=value["direction"],
        confirmed_at_bar_index=value["confirmed_at_bar_index"],
        known_at_bar_index=value["known_at_bar_index"],
        range_low_i64=value.get("range_low_i64"),
        range_high_i64=value.get("range_high_i64"),
        range_low_source_bar_index=value.get("range_low_source_bar_index"),
        range_high_source_bar_index=value.get("range_high_source_bar_index"),
        range_profile=value.get("range_profile", "endpoint_extrema_v1"),
    )
