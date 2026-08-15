from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, Literal

from tvbt.chan.events import EventEmitter
from tvbt.chan.reference import ReferenceCenter, reference_centers, reference_segments
from tvbt.chan.signals import ChanSignal, chan_divergences, chan_trade_points
from tvbt.logging_config import get_runtime_logger

"""逐 K 线因果缠论引擎。

算法分层：

1. `RawBar` 保存 Go 指定的标准化 K 线字段。
2. `_update_inclusion` 处理包含关系，生成独立 K 线 `IncludedBar`。
3. `_seal_fractal` 在第三根独立 K 线出现后确认中间 K 线的严格分型。
4. `_consume_fractal` 按参考实现的端点选择规则释放已确认笔。
5. `_update_structures` 在笔变化时重扫笔中枢、线段、标准线段中枢、背驰和买卖点。
6. `EventEmitter` 统一把结构变化转换为 `known_at_bar_index` 约束的因果事件。

本文件不读取磁盘、不写缓存、不处理订单成交；这些职责分别在 `algorithm.py`、
`storage.py` 和策略/回测模块中完成。
"""

Direction = Literal["up", "down", "unknown"]
FractalKind = Literal["top", "bottom"]
# 参考算法要求一笔至少跨越 5 根独立 K 线。
REFERENCE_MIN_INDEPENDENT_BARS = 5


def _stable_id(prefix: str, *parts: object) -> str:
    """根据语义字段生成稳定 ID，避免同一对象在重算后 ID 漂移。"""
    data = json.dumps(parts, ensure_ascii=False, separators=(",", ":"), default=str).encode()
    return f"{prefix}-{hashlib.sha256(data).hexdigest()[:20]}"


@dataclass(frozen=True)
class RawBar:
    """Go 指定的标准化原始 K 线，仅保留缠论引擎需要的定点价格字段。"""

    # 全数据集内连续递增的原始 K 线序号。
    bar_index: int
    # K 线时间戳，沿用上游传入的 UTC 毫秒语义。
    time: int
    # 最高价、最低价、收盘价均使用定点整数，避免持久化与事件输出出现浮点误差。
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
    confirmed_at_bar_index: int
    known_at_bar_index: int

    def payload(self) -> dict[str, Any]:
        """转换为因果事件流载荷，字段名保持跨进程 snake_case 契约。"""
        return {
            "bar_index": self.bar_index,
            "time": self.time,
            "price_i64": self.price_i64,
            "fractal_type": self.fractal_type,
            "confirmed": True,
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

    def payload(self, *, confirmed: bool = True) -> dict[str, Any]:
        """转换为图层绘制和对象树使用的事件载荷。"""
        return {
            "start_bar_index": self.start.bar_index,
            "start_time": self.start.time,
            "start_price_i64": self.start.price_i64,
            "end_bar_index": self.end.bar_index,
            "end_time": self.end.time,
            "end_price_i64": self.end.price_i64,
            "direction": self.direction,
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
    algorithm_version = "5.0.0"

    def __init__(self, parameters: ChanParameters | None = None) -> None:
        # 运行参数。
        self.parameters = parameters or ChanParameters()
        # 原始 K 线序列，必须按 bar_index 和 time 严格递增。
        self.raw_bars: list[RawBar] = []
        # 经过包含关系处理后的独立 K 线序列。
        self.included: list[IncludedBar] = []
        # 已确认并发布过的分型。
        self.fractals: list[Fractal] = []
        # 已确认的笔序列，方向必须严格交替。
        self.bi: list[LineObject] = []
        # 当前待成笔起点，保留用于状态导出和检查点恢复。
        self._stroke_start: Fractal | None = None
        # 尚未释放为确认笔的分型队列。
        self._pending_fractals: list[Fractal] = []
        # 当前未确认临时笔 ID；确认笔出现或条件失效时删除。
        self._provisional_bi_id: str | None = None
        # MACD EMA 状态，用于后续线段背驰面积计算。
        self._macd_fast: float | None = None
        self._macd_slow: float | None = None
        self._macd_dea: float | None = None
        # 每根原始 K 线的 MACD 柱值，按国内常用 2 * (DIFF - DEA) 语义保存。
        self._macd_histogram: dict[int, float] = {}
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
        self.raw_bars.append(bar)
        self._append_macd(bar)
        # 包含关系只有在追加出新的独立 K 线时，才可能封存上一根独立 K 线的分型。
        appended = self._update_inclusion(bar)
        bi_count = len(self.bi)
        if appended:
            fractal = self._seal_fractal()
            if fractal is not None:
                self.fractals.append(fractal)
                self.emitter.upsert(
                    fractal.known_at_bar_index,
                    "fractal",
                    fractal.object_id,
                    fractal.payload(),
                )
                self._consume_fractal(fractal)
        # 即使没有确认笔，也要维护前端可见的未确认临时笔。
        self._update_provisional_bi(bar.bar_index)
        if len(self.bi) != bi_count:
            # 只有确认笔发生变化时，才重新扫描中枢、线段、背驰和买卖点。
            self._update_structures(bar.bar_index)

    def _update_inclusion(self, bar: RawBar) -> bool:
        """按前一根独立 K 线方向处理包含关系，返回是否产生新独立 K 线。"""
        # 先把新原始 K 线包装成候选独立 K 线。
        current = IncludedBar(
            normalized_index=len(self.included),
            start_raw_index=bar.bar_index,
            end_raw_index=bar.bar_index,
            high_i64=bar.high_i64,
            low_i64=bar.low_i64,
            high_time=bar.time,
            low_time=bar.time,
            high_raw_index=bar.bar_index,
            low_raw_index=bar.bar_index,
            confirm_time=bar.time,
            direction="down",
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
        first_pair = len(self.included) == 1 and previous.start_raw_index == previous.end_raw_index
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

    def _seal_fractal(self) -> Fractal | None:
        # 参考 kline-chart/c_bi.py：严格分型只使用三根独立 K 线判断。
        # 新独立 K 线追加后，中间那根独立 K 线立即变为可确认候选。
        index = len(self.included) - 2
        if index < 1:
            return None
        center = self.included[index]
        window = self.included[index - 1 : index + 2]
        others = [window[0], window[2]]
        top = all(
            center.high_i64 > item.high_i64 and center.low_i64 > item.low_i64 for item in others
        )
        bottom = all(
            center.high_i64 < item.high_i64 and center.low_i64 < item.low_i64 for item in others
        )
        if not top and not bottom:
            return None
        # 顶分型取中间独立 K 线的最高点，底分型取最低点，锚定回原始 K 线。
        kind: FractalKind = "top" if top else "bottom"
        pivot_index = center.high_raw_index if top else center.low_raw_index
        pivot_time = center.high_time if top else center.low_time
        price = center.high_i64 if top else center.low_i64
        confirmed_at = self.included[index + 1].end_raw_index
        return Fractal(
            object_id=_stable_id("fractal", kind, pivot_time, confirmed_at, price),
            fractal_type=kind,
            normalized_index=index,
            bar_index=pivot_index,
            time=pivot_time,
            price_i64=price,
            confirmed_at_bar_index=confirmed_at,
            known_at_bar_index=confirmed_at,
        )

    def _consume_fractal(self, endpoint: Fractal) -> None:
        """把新确认分型送入参考成笔规则，必要时一次释放多笔。"""
        self._pending_fractals.append(endpoint)
        if self._stroke_start is None:
            self._stroke_start = endpoint
        while len(self._pending_fractals) >= 3:
            # 参考算法会从 pending 队列头部选择可成笔端点。
            selection = self._reference_selection()
            if selection is None:
                return
            endpoint_position, known_at = selection
            if known_at is None:
                return
            # 一个新封存的分型可能释放多笔；这些笔在本批释放分型前不可知，
            # 批内 known_at 也不能比上一笔倒退。
            known_at = max(
                known_at,
                endpoint.known_at_bar_index,
                self.bi[-1].known_at_bar_index if self.bi else 0,
            )
            start = self._pending_fractals[0]
            selected = self._pending_fractals[endpoint_position]
            direction: Literal["up", "down"] = "up" if start.fractal_type == "bottom" else "down"
            if self.bi and self.bi[-1].direction == direction:
                raise AssertionError("reference bi directions must alternate")
            line = LineObject(
                object_id=_stable_id("bi", start.object_id, selected.object_id),
                start=start,
                end=selected,
                direction=direction,
                confirmed_at_bar_index=known_at,
                known_at_bar_index=known_at,
            )
            if self._provisional_bi_id is not None:
                # 确认笔出现后删除同一起点的临时笔，避免前端同时显示确认和未确认版本。
                self.emitter.delete(known_at, "bi", self._provisional_bi_id)
                self._provisional_bi_id = None
            self.bi.append(line)
            self.emitter.upsert(known_at, "bi", line.object_id, line.payload())
            self._pending_fractals = self._pending_fractals[endpoint_position:]
            self._stroke_start = selected

    def _reference_selection(self) -> tuple[int, int | None] | None:
        """移植 kline-chart c_bi.get_node/satisfy_the_number 的单基点选端逻辑。"""
        values = self._pending_fractals
        if len(values) < 2:
            return None
        base = values[0]
        next_position = next(
            (
                position
                for position in range(1, len(values))
                if values[position].fractal_type != base.fractal_type
            ),
            -1,
        )
        while next_position >= 0:
            # 端点与基点之间必须至少包含 5 根独立 K 线。
            independent_count = values[next_position].normalized_index - base.normalized_index + 1
            if independent_count < REFERENCE_MIN_INDEPENDENT_BARS:
                next_position += 1
                while (
                    next_position < len(values)
                    and values[next_position].fractal_type == base.fractal_type
                ):
                    next_position += 1
                if next_position >= len(values):
                    return None
                continue
            return self._reference_satisfy(next_position)
        return None

    def _reference_satisfy(self, endpoint_position: int) -> tuple[int, int | None]:
        """检查候选端点是否满足后续分型区间确认规则。"""
        values = self._pending_fractals
        base_is_top = values[0].fractal_type == "top"
        # baseline_position 用于计算后续反向候选与当前端点之间的独立 K 线数量。
        baseline_position = endpoint_position
        cursor = endpoint_position + 1
        while cursor < len(values):
            candidate = values[cursor]
            endpoint = values[endpoint_position]
            if candidate.fractal_type == endpoint.fractal_type:
                candidate_bar = self.included[candidate.normalized_index]
                endpoint_bar = self.included[endpoint.normalized_index]
                more_extreme = (base_is_top and candidate_bar.low_i64 < endpoint_bar.low_i64) or (
                    not base_is_top and candidate_bar.high_i64 > endpoint_bar.high_i64
                )
                if more_extreme:
                    # 同类端点出现更极端价格时，按参考算法替换端点。
                    endpoint_position = cursor
                    baseline_position = cursor
                cursor += 1
                continue
            independent_count = (
                candidate.normalized_index - values[baseline_position].normalized_index + 1
            )
            if independent_count < REFERENCE_MIN_INDEPENDENT_BARS:
                cursor += 1
                continue
            candidate_bar = self.included[candidate.normalized_index]
            endpoint_bar = self.included[values[endpoint_position].normalized_index]
            separated = (
                candidate_bar.low_i64 > endpoint_bar.high_i64
                if base_is_top
                else candidate_bar.high_i64 < endpoint_bar.low_i64
            )
            if separated:
                # 后续反向分型与端点价格区间严格分离，候选笔才在该分型处被确认。
                return endpoint_position, candidate.known_at_bar_index
            cursor += 1
        # 参考批量渲染会保留这个尚未由后续分型验证的端点，前端以临时笔展示。
        return endpoint_position, None

    def _update_provisional_bi(self, known_at_bar_index: int) -> None:
        """同步当前未确认临时笔，使前端能显示参考算法的最后候选端点。"""
        selection = self._reference_selection()
        if selection is None or selection[1] is not None:
            if self._provisional_bi_id is not None:
                self.emitter.delete(known_at_bar_index, "bi", self._provisional_bi_id)
                self._provisional_bi_id = None
            return
        endpoint_position, _ = selection
        start = self._pending_fractals[0]
        endpoint = self._pending_fractals[endpoint_position]
        # 临时笔 ID 只绑定起点，端点移动时使用同一个对象 upsert，减少前端闪烁。
        provisional_id = _stable_id("bi-provisional", start.object_id)
        if self._provisional_bi_id is not None and self._provisional_bi_id != provisional_id:
            self.emitter.delete(known_at_bar_index, "bi", self._provisional_bi_id)
        self._provisional_bi_id = provisional_id
        line = LineObject(
            provisional_id,
            start,
            endpoint,
            "up" if start.fractal_type == "bottom" else "down",
            known_at_bar_index,
            known_at_bar_index,
        )
        self.emitter.upsert(known_at_bar_index, "bi", provisional_id, line.payload(confirmed=False))

    def _update_structures(self, known_at_bar_index: int) -> None:
        """基于已确认笔重建并同步中枢、线段、线段中枢、背驰和买卖点。"""
        # 笔中枢：使用参考扫描器从已确认笔序列中提取。
        centers: list[tuple[str, dict[str, Any], int]] = []
        for center in reference_centers(self.bi):
            object_id = _stable_id(
                "zhongshu",
                self.bi[center.base_index].object_id,
                self.bi[center.seed_end_index].object_id,
            )
            components = self.bi[center.base_index : center.end_index + 1]
            dd_i64 = min(min(line.start.price_i64, line.end.price_i64) for line in components)
            gg_i64 = max(max(line.start.price_i64, line.end.price_i64) for line in components)
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
        segments: list[tuple[str, dict[str, Any], int]] = []
        segment_lines: list[LineObject] = []
        # 线段：参考算法返回线段语义对象；确认线段额外转成 LineObject 供线段中枢复用。
        for segment in reference_segments(self.bi, self.raw_bars):
            object_id = _stable_id(
                "segment",
                self.bi[segment.start_index].object_id,
                "up" if segment.up else "down",
            )
            segments.append(
                (
                    object_id,
                    {
                        "start_bar_index": segment.start_bar_index,
                        "start_time": segment.start_time,
                        "start_price_i64": segment.start_price_i64,
                        "end_bar_index": segment.end_bar_index,
                        "end_time": segment.end_time,
                        "end_price_i64": segment.end_price_i64,
                        "direction": "up" if segment.up else "down",
                        "confirmed": segment.confirmed,
                        "confirmed_at_bar_index": (
                            segment.known_at_bar_index if segment.confirmed else None
                        ),
                    },
                    segment.known_at_bar_index,
                )
            )
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
                segment_lines.append(
                    LineObject(
                        object_id,
                        start,
                        end,
                        direction,
                        segment.known_at_bar_index,
                        segment.known_at_bar_index,
                    )
                )

        # 第 18 课定义的初始中枢是开放价格区间（低点最大值、高点最小值）。
        # 仅点接触没有区间宽度，所以不能作为标准线段中枢；笔中枢扫描器保留该兼容情形。
        segment_centers = [
            center for center in reference_centers(segment_lines) if center.zd_i64 < center.zg_i64
        ]
        segment_center_ids: list[str] = []
        segment_center_values: list[tuple[str, dict[str, Any], int]] = []
        # 走势状态事件：记录中枢震荡、盘整和中枢迁移。
        movement_state_values: list[tuple[str, dict[str, Any], int]] = []
        # Z/Zn 监控事件：跟踪各线段相对中枢中轴的位置、强弱和迁移预警。
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
            dd_i64 = min(min(line.start.price_i64, line.end.price_i64) for line in components)
            gg_i64 = max(max(line.start.price_i64, line.end.price_i64) for line in components)
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

            zn_values: list[int] = []
            for component in components:
                # Zn 使用单个组成线段的价格中轴，与中枢 Z 比较判断相对强弱。
                low = min(component.start.price_i64, component.end.price_i64)
                high = max(component.start.price_i64, component.end.price_i64)
                zn_i64 = (low + high) // 2
                zn_values.append(zn_i64)
                relative = "above" if zn_i64 > z_i64 else "below" if zn_i64 < z_i64 else "equal"
                strength = (
                    "strong"
                    if (component.direction == "up" and zn_i64 > z_i64)
                    or (component.direction == "down" and zn_i64 < z_i64)
                    else "weak"
                    if zn_i64 != z_i64
                    else "neutral"
                )
                warning = None
                if len(zn_values) >= 3:
                    # 最近三个 Zn 单调变化时输出迁移预警，但不直接生成交易信号。
                    tail = zn_values[-3:]
                    warning = (
                        "up"
                        if tail[0] < tail[1] < tail[2]
                        else "down"
                        if tail[0] > tail[1] > tail[2]
                        else None
                    )
                center_monitor_values.append(
                    (
                        _stable_id("center-monitor", object_id, component.object_id),
                        {
                            "bar_index": component.end.bar_index,
                            "time": component.end.time,
                            "z_i64": z_i64,
                            "zn_i64": zn_i64,
                            "range_high_i64": high,
                            "range_low_i64": low,
                            "component_direction": component.direction,
                            "relative_position": relative,
                            "strength": strength,
                            "migration_warning": warning,
                            "analysis_level": "segment",
                            "reference_object_id": object_id,
                            "confirmed": True,
                            "confirmed_at_bar_index": component.known_at_bar_index,
                        },
                        component.known_at_bar_index,
                    )
                )

        divergence_specs = chan_divergences(
            segment_lines, segment_centers, segment_center_ids, self._macd_histogram
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

        trade_point_values: list[tuple[str, dict[str, Any], int]] = []
        # 买卖点：消费标准线段中枢和背驰对象，生成一二三类买卖点事件。
        for signal in chan_trade_points(
            segment_lines, segment_centers, segment_center_ids, divergence_objects
        ):
            object_id = _stable_id(
                "trade-point",
                signal.signal_type,
                segment_lines[signal.segment_index].object_id,
                signal.reference_object_id,
            )
            trade_point_values.append(
                (object_id, _signal_payload(signal), signal.known_at_bar_index)
            )
        self._sync_objects("zhongshu", centers, known_at_bar_index)
        self._sync_objects("segment", segments, known_at_bar_index)
        self._sync_objects("segment_zhongshu", segment_center_values, known_at_bar_index)
        self._sync_objects("movement_state", movement_state_values, known_at_bar_index)
        self._sync_objects("center_monitor", center_monitor_values, known_at_bar_index)
        self._sync_objects("divergence", divergence_values, known_at_bar_index)
        self._sync_objects("trade_point", trade_point_values, known_at_bar_index)
        get_runtime_logger().debug(
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
                "movement_state_count": len(movement_state_values),
                "center_monitor_count": len(center_monitor_values),
                "divergence_count": len(divergence_values),
                "trade_point_count": len(trade_point_values),
                "event_count": len(self.emitter.events),
            },
        )

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

    def result_rows(self) -> dict[str, list[dict[str, Any]]]:
        """导出当前对象快照，按各对象的图形起点排序，供 Parquet 写入或 API 返回。"""
        return {
            "fractals": sorted(self.emitter.current("fractal"), key=lambda item: item["bar_index"]),
            "bi": sorted(self.emitter.current("bi"), key=lambda item: item["start_bar_index"]),
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
            "bi": [_line_state(item) for item in self.bi],
            "stroke_start_id": self._stroke_start.object_id if self._stroke_start else None,
            "pending_fractal_ids": [item.object_id for item in self._pending_fractals],
            "provisional_bi_id": self._provisional_bi_id,
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
        fractals = {item.object_id: item for item in engine.fractals}
        engine.bi = [_line_from_state(item, fractals) for item in state["bi"]]
        start_id = state["stroke_start_id"]
        engine._stroke_start = fractals.get(start_id)
        engine._pending_fractals = [fractals[value] for value in state["pending_fractal_ids"]]
        engine._provisional_bi_id = state["provisional_bi_id"]
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
        "confirmed": True,
        "confirmed_at_bar_index": signal.known_at_bar_index,
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
    )
