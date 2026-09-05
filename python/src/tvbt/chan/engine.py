from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, Literal

from tvbt.chan.events import EventEmitter
from tvbt.chan.level_graph import GraphCenter, build_level_graph
from tvbt.chan.reference import (
    ReferenceCenter,
    ReferenceSegment,
    ReferenceSegmentAccumulator,
    update_reference_centers,
)
from tvbt.chan.signals import (
    ChanSignal,
    chan_divergences,
    chan_first_point_candidates,
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
# 一笔至少跨越 5 根包含处理后的独立 K 线。
REFERENCE_MIN_INDEPENDENT_BARS = 5


def _stable_id(prefix: str, *parts: object) -> str:
    """根据语义字段生成稳定 ID，避免同一对象在重算后 ID 漂移。"""
    data = json.dumps(parts, ensure_ascii=False, separators=(",", ":"), default=str).encode()
    return f"{prefix}-{hashlib.sha256(data).hexdigest()[:20]}"


@dataclass(frozen=True)
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


@dataclass
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


@dataclass(frozen=True)
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


@dataclass(frozen=True)
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
    range_profile: Literal["endpoint_extrema_v1", "constituent_bi_union_v1"] = "endpoint_extrema_v1"

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


@dataclass(frozen=True)
class ChanParameters:
    """缠论引擎参数。当前只暴露检查点间隔，便于后续恢复与长任务拆分。"""

    # 每处理多少根 K 线允许外层保存一次检查点。
    checkpoint_interval: int = 1024

    def __post_init__(self) -> None:
        if self.checkpoint_interval < 1:
            raise ValueError("bar-count parameters must be positive")


class ChanEngine:
    """逐 K 线因果缠论引擎，负责分型、笔、线段、中枢和信号事件生成。"""

    # 算法版本参与缓存键；任何语义变化都必须升级版本，禁止复用旧缓存。
    algorithm_version = "15.0.0"

    def __init__(self, parameters: ChanParameters | None = None) -> None:
        # 运行参数。
        self.parameters = parameters or ChanParameters()
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
        self._bi_centers: list[ReferenceCenter] = []
        self._center_values: list[tuple[str, dict[str, Any], int]] = []
        self._segment_specs: list[ReferenceSegment] = []
        self._segment_records: list[tuple[tuple[str, dict[str, Any], int], LineObject | None]] = []
        self._segment_lines: list[LineObject] = []
        self._all_segment_centers: list[ReferenceCenter] = []
        # 独立 K 线位置到分型列表位置的索引，供区间极值扫描快速定位候选端点。
        self._fractal_by_normalized_index: dict[int, int] = {}
        # MACD EMA 状态，用于后续线段背驰面积计算。
        self._macd_fast: float | None = None
        self._macd_slow: float | None = None
        self._macd_dea: float | None = None
        # 每根原始 K 线的 MACD 柱值，按国内常用 2 * (DIFF - DEA) 语义保存。
        self._macd_histogram: dict[int, float] = {}
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

    def _update_structures(self, known_at_bar_index: int, changed_bi_index: int) -> None:
        """从首次变化笔位置更新中枢、线段、线段中枢、背驰和买卖点。"""
        # 笔中枢：使用参考扫描器从已确认笔序列中提取。
        updated_bi_centers = update_reference_centers(
            self.bi,
            self._bi_centers,
            changed_bi_index,
        )
        changed_center_index = self._common_prefix_length(
            self._bi_centers,
            updated_bi_centers,
        )
        centers = self._center_values[:changed_center_index]
        self._bi_centers = updated_bi_centers
        for center in self._bi_centers[changed_center_index:]:
            object_id = _stable_id(
                "zhongshu",
                self.bi[center.base_index].object_id,
                self.bi[center.seed_end_index].object_id,
            )
            components = self.bi[center.base_index : center.end_index + 1]
            dd_i64 = min(
                line.range_low_i64 for line in components if line.range_low_i64 is not None
            )
            gg_i64 = max(
                line.range_high_i64 for line in components if line.range_high_i64 is not None
            )
            centers.append(
                (
                    object_id,
                    {
                        "start_bar_index": center.start_bar_index,
                        "start_time": center.start_time,
                        "end_bar_index": center.end_bar_index,
                        "end_time": center.end_time,
                        "zg_i64": center.zg_i64,
                        "zd_i64": center.zd_i64,
                        "gg_i64": gg_i64,
                        "dd_i64": dd_i64,
                        "z_i64": (center.zd_i64 + center.zg_i64) // 2,
                        "analysis_level": "stroke",
                        "component_kind": "bi",
                        "component_count": len(components),
                        "confirmed": True,
                        "confirmed_at_bar_index": center.known_at_bar_index,
                        "status": center.status,
                        "leave_direction": center.leave_direction,
                    },
                    center.known_at_bar_index,
                )
            )
        self._center_values = centers
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
        # 线段：参考算法返回线段语义对象；确认线段额外转成 LineObject 供线段中枢复用。
        for segment in segment_specs[changed_segment_spec_index:]:
            object_id = _stable_id(
                "segment",
                self.bi[segment.start_index].object_id,
                "up" if segment.up else "down",
            )
            component_bi = self.bi[segment.start_index : segment.end_index + 1]
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

        # 标准中枢使用闭区间交集；三条已确认线段的重叠可以退化为 ZD == ZG 的点中枢。
        changed_confirmed_segment_index = self._common_prefix_length(
            self._segment_lines,
            segment_lines,
        )
        confirmed_segments_changed = changed_confirmed_segment_index < len(
            self._segment_lines
        ) or changed_confirmed_segment_index < len(segment_lines)
        self._segment_lines = segment_lines
        self._sync_objects("zhongshu", centers, known_at_bar_index)
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

        self._all_segment_centers = update_reference_centers(
            segment_lines,
            self._all_segment_centers,
            changed_confirmed_segment_index,
            minimum_line_count=4,
        )
        segment_centers = [
            center for center in self._all_segment_centers if center.zd_i64 <= center.zg_i64
        ]
        segment_center_ids: list[str] = []
        segment_center_values: list[tuple[str, dict[str, Any], int]] = []
        graph_centers: list[GraphCenter] = []
        # 走势状态事件：记录中枢震荡、盘整和中枢迁移。
        movement_state_values: list[tuple[str, dict[str, Any], int]] = []
        # Z/Zn 监控事件：跟踪各线段相对中枢中轴的位置、强弱和越界/楔形预警。
        center_monitor_values: list[tuple[str, dict[str, Any], int]] = []
        previous_center: tuple[ReferenceCenter, str, int, int] | None = None
        for center in segment_centers:
            object_id = _stable_id(
                "segment-zhongshu",
                segment_lines[center.base_index].object_id,
                segment_lines[center.seed_end_index].object_id,
            )
            segment_center_ids.append(object_id)
            components = segment_lines[center.base_index : center.end_index + 1]
            dd_i64 = min(
                line.range_low_i64 for line in components if line.range_low_i64 is not None
            )
            gg_i64 = max(
                line.range_high_i64 for line in components if line.range_high_i64 is not None
            )
            z_i64 = (center.zd_i64 + center.zg_i64) // 2
            segment_center_values.append(
                (
                    object_id,
                    {
                        "start_bar_index": center.start_bar_index,
                        "start_time": center.start_time,
                        "end_bar_index": center.end_bar_index,
                        "end_time": center.end_time,
                        "zg_i64": center.zg_i64,
                        "zd_i64": center.zd_i64,
                        "gg_i64": gg_i64,
                        "dd_i64": dd_i64,
                        "z_i64": z_i64,
                        "analysis_level": "segment",
                        "component_kind": "segment",
                        "component_count": len(components),
                        "confirmed": True,
                        "confirmed_at_bar_index": center.known_at_bar_index,
                        "status": center.status,
                        "leave_direction": center.leave_direction,
                    },
                    center.known_at_bar_index,
                )
            )
            graph_centers.append(
                GraphCenter(
                    object_id=object_id,
                    level_id="L0",
                    start_bar_index=center.start_bar_index,
                    start_time=center.start_time,
                    end_bar_index=center.end_bar_index,
                    end_time=center.end_time,
                    zd_i64=center.zd_i64,
                    zg_i64=center.zg_i64,
                    dd_i64=dd_i64,
                    gg_i64=gg_i64,
                    component_kind="segment",
                    component_object_ids=tuple(line.object_id for line in components),
                    component_known_at=tuple(line.known_at_bar_index for line in components),
                    status=center.status,
                    confirmed_at_bar_index=center.known_at_bar_index,
                    known_at_bar_index=center.known_at_bar_index,
                )
            )
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
                prior, prior_id, prior_dd, prior_gg = previous_center
                # 新中枢整体脱离前一中枢完整振荡包络时，标记同级别中枢迁移。
                migration = "up" if dd_i64 > prior_gg else "down" if gg_i64 < prior_dd else None
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
            previous_center = (center, object_id, dd_i64, gg_i64)

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

        level_graph = build_level_graph(graph_centers)
        divergence_specs = chan_divergences(
            segment_lines,
            segment_centers,
            segment_center_ids,
            self._macd_histogram,
            self._macd_area_cache,
        )
        divergence_values: list[tuple[str, dict[str, Any], int]] = []
        divergence_objects: list[tuple[str, ChanSignal]] = []
        # 背驰：使用线段、线段中枢和 MACD 柱面积计算，结果仍以因果 known_at 发布。
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

        trade_point_by_id: dict[str, tuple[str, dict[str, Any], int]] = {}
        level_sources: list[tuple[str, list[ReferenceCenter], list[str]]] = [
            ("L0", segment_centers, segment_center_ids)
        ]
        promoted_indices = [
            index
            for index, center in enumerate(graph_centers)
            if len(center.component_object_ids) >= 9
        ]
        if promoted_indices:
            level_sources.append(
                (
                    "L1",
                    [segment_centers[index] for index in promoted_indices],
                    [segment_center_ids[index] for index in promoted_indices],
                )
            )
        for level_id, source_centers, source_ids in level_sources:
            for signal in chan_first_point_candidates(
                segment_lines,
                source_centers,
                source_ids,
                self._macd_histogram,
                self._macd_area_cache,
                level_id=level_id,
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
        # 买卖点：消费标准线段中枢和背驰对象，生成一二三类买卖点事件。
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
        self._sync_objects("segment_zhongshu", segment_center_values, known_at_bar_index)
        self._sync_objects("level_center", list(level_graph.centers), known_at_bar_index)
        self._sync_objects("level_movement", list(level_graph.movements), known_at_bar_index)
        self._sync_objects("movement_state", movement_state_values, known_at_bar_index)
        self._sync_objects("center_monitor", center_monitor_values, known_at_bar_index)
        self._sync_objects("divergence", divergence_values, known_at_bar_index)
        self._sync_objects("trade_point", trade_point_values, known_at_bar_index)
        logger.debug(
            "chan.structures.updated",
            "Chan structures updated after confirmed bi change",
            {
                "bar_index": known_at_bar_index,
                "merged_bar_count": len(self.included),
                "fractal_count": len(self.fractals),
                "bi_count": len(self.bi),
                "zhongshu_count": len(centers),
                "segment_count": len(segments),
                "segment_zhongshu_count": len(segment_center_values),
                "level_center_count": len(level_graph.centers),
                "level_movement_count": len(level_graph.movements),
                "movement_state_count": len(movement_state_values),
                "center_monitor_count": len(center_monitor_values),
                "divergence_count": len(divergence_values),
                "trade_point_count": len(trade_point_values),
                "event_count": len(self.emitter.events),
            },
        )

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

    def result_rows(self) -> dict[str, list[dict[str, Any]]]:
        """导出当前对象快照，按各对象的图形起点排序，供 Parquet 写入或 API 返回。"""
        return {
            "processed_bars": sorted(
                self.emitter.current("processed_bar"), key=lambda item: item["normalized_index"]
            ),
            "fractals": sorted(self.emitter.current("fractal"), key=lambda item: item["bar_index"]),
            "bi": sorted(self.emitter.current("bi"), key=lambda item: item["start_bar_index"]),
            "bi_states": sorted(
                self.emitter.current("bi_state"), key=lambda item: item["bar_index"]
            ),
            "segments": sorted(
                self.emitter.current("segment"), key=lambda item: item["start_bar_index"]
            ),
            "zhongshu": sorted(
                self.emitter.current("zhongshu"), key=lambda item: item["start_bar_index"]
            ),
            "segment_zhongshu": sorted(
                self.emitter.current("segment_zhongshu"),
                key=lambda item: item["start_bar_index"],
            ),
            "level_centers": sorted(
                self.emitter.current("level_center"),
                key=lambda item: (item["start_bar_index"], item["level_id"]),
            ),
            "level_movements": sorted(
                self.emitter.current("level_movement"),
                key=lambda item: (item["start_bar_index"], item["level_id"]),
            ),
            "movement_states": sorted(
                self.emitter.current("movement_state"), key=lambda item: item["start_bar_index"]
            ),
            "center_monitors": sorted(
                self.emitter.current("center_monitor"), key=lambda item: item["bar_index"]
            ),
            "divergences": sorted(
                self.emitter.current("divergence"), key=lambda item: item["bar_index"]
            ),
            "trade_points": sorted(
                self.emitter.current("trade_point"), key=lambda item: item["bar_index"]
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
            "emitter": self.emitter.state(),
        }

    @classmethod
    def from_state(cls, state: dict[str, Any]) -> ChanEngine:
        """从检查点恢复引擎，并重建 MACD 状态和事件收集器。"""
        engine = cls(ChanParameters(**state["parameters"]))
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
        self._macd_histogram[bar.bar_index] = 2.0 * (diff - self._macd_dea)


def _signal_payload(signal: ChanSignal) -> dict[str, Any]:
    """统一背驰和买卖点信号的事件载荷结构。"""
    return {
        "bar_index": signal.bar_index,
        "time": signal.time,
        "price_i64": signal.price_i64,
        "signal_type": signal.signal_type,
        "divergence_kind": signal.divergence_kind,
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
