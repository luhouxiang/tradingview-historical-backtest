from __future__ import annotations

import copy
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

"""缠论参考算法端口。

本文件只移植 `algo-ui/common/chanlun/c_bi.py` 中和“段、笔中枢、线段中枢”
有关的纯结构扫描逻辑。它通过 Protocol 读取 `engine.py` 的轻量对象，避免把
参考实现的数据模型复制到项目内。

重要约束：

- 不修改传入的笔对象；参考实现中 `_NCHDUAN` 会翻转 side，这里用等价判定替代。
- 返回的 `known_at_bar_index` 必须来自已经确认的笔/段，不能早于结构可知时刻。
- 本文件不生成事件、不写缓存；事件和稳定 ID 由 `engine.py` 管理。
"""


class BarLike(Protocol):
    """段算法读取原始 K 线极值所需的最小字段集合。"""

    @property
    def bar_index(self) -> int: ...

    @property
    def time(self) -> int: ...

    @property
    def high_i64(self) -> int: ...

    @property
    def low_i64(self) -> int: ...

    @property
    def close_i64(self) -> int: ...


class EndpointLike(Protocol):
    """笔或段端点的时间/价格锚点。"""

    @property
    def bar_index(self) -> int: ...

    @property
    def time(self) -> int: ...

    @property
    def price_i64(self) -> int: ...


class LineLike(Protocol):
    """笔和已确认线段共用的线性结构接口。"""

    @property
    def object_id(self) -> str: ...

    @property
    def start(self) -> EndpointLike: ...

    @property
    def end(self) -> EndpointLike: ...

    @property
    def direction(self) -> Literal["up", "down"]: ...

    @property
    def known_at_bar_index(self) -> int: ...


@dataclass
class ReferenceSegment:
    """参考线段扫描器的中间/输出结构。

    `start_index/end_index` 是在线性组件序列中的索引，不是原始 K 线索引；
    写入缓存前会补齐对应端点的 `bar_index/time/price_i64`。
    """

    # 在线性组件序列中的起止位置。
    start_index: int = 0
    end_index: int = 0
    # True 表示向上线段，False 表示向下线段；命名沿用参考实现的布尔方向。
    up: bool = False
    # 是否已经由后续反向结构确认。
    confirmed: bool = False
    # 该线段状态最早可知的原始 K 线位置。
    known_at_bar_index: int = 0
    # 端点锚点，算法结束后由 `_append_segment`/收尾逻辑补齐。
    start_bar_index: int = 0
    end_bar_index: int = 0
    start_time: int = 0
    end_time: int = 0
    start_price_i64: int = 0
    end_price_i64: int = 0


@dataclass(frozen=True)
class ReferenceCenter:
    """参考中枢结构。

    `base_index/seed_end_index/end_index/exit_index` 均为组件索引。笔中枢时组件
    是笔，标准线段中枢时组件是已确认线段。
    """

    # 中枢扫描基点，以及形成冻结核心的同奇偶第三个组件。
    base_index: int
    seed_end_index: int
    # 当前中枢延伸到的最后一个参与组件。
    end_index: int
    # 首个离开中枢的同奇偶组件；未离开时为 None。
    exit_index: int | None
    # 图形时间范围。
    start_bar_index: int
    end_bar_index: int
    start_time: int
    end_time: int
    # 冻结核心区间：[ZD, ZG]。标准线段中枢还会在 engine.py 过滤 `ZD < ZG`。
    zd_i64: int
    zg_i64: int
    # 中枢最早可知时刻。
    known_at_bar_index: int
    # confirmed=刚形成，extended=继续相交延伸，left=已有离开组件。
    status: Literal["confirmed", "extended", "left"]
    # 离开方向，只有 status=left 时有值。
    leave_direction: Literal["up", "down"] | None


def _low(line: LineLike) -> int:
    return min(line.start.price_i64, line.end.price_i64)


def _high(line: LineLike) -> int:
    return max(line.start.price_i64, line.end.price_i64)


def _flipped_side_up(line: LineLike) -> bool:
    # algo-ui/common/chanlun/c_bi.py::_NCHDUAN 会先翻转每个 stBiK.side。
    # 这里保留原始 line.direction，只在判断时使用等价的反向语义。
    return line.direction == "down"


def _find_first_segment(
    current: int,
    lines: Sequence[LineLike],
    bars: dict[int, BarLike],
    maximum: int,
    minimum: int,
    segment: ReferenceSegment,
) -> bool:
    """寻找第一段。

    `maximum/minimum` 记录候选窗口内的局部高/低笔位置。函数只在满足参考实现
    的隔三笔重叠和前后极值关系时写入 `segment`。
    """
    current_bar = bars[lines[current].start.bar_index]
    if _flipped_side_up(lines[current]):
        maximum_bar = bars[lines[maximum].start.bar_index]
        if current_bar.high_i64 > maximum_bar.high_i64:
            maximum = current
        if current - minimum < 3:
            return False
        previous = bars[lines[current - 2].start.bar_index]
        if current_bar.high_i64 > previous.high_i64:
            current_bar = bars[lines[current - 1].start.bar_index]
            previous = bars[lines[current - 3].start.bar_index]
            if current_bar.high_i64 > previous.high_i64:
                segment.start_index = minimum
                segment.end_index = current
                segment.up = True
                return True
    else:
        minimum_bar = bars[lines[minimum].start.bar_index]
        if current_bar.low_i64 < minimum_bar.low_i64:
            minimum = current
        if current - maximum < 3:
            return False
        previous = bars[lines[current - 2].start.bar_index]
        if current_bar.low_i64 < previous.low_i64:
            current_bar = bars[lines[current - 1].start.bar_index]
            previous = bars[lines[current - 3].start.bar_index]
            # This high-to-high comparison intentionally follows algo-ui exactly.
            if current_bar.high_i64 < previous.high_i64:
                segment.start_index = maximum
                segment.end_index = current
                segment.up = False
                return True
    return False


def _is_overlap(current: int, lines: Sequence[LineLike], bars: dict[int, BarLike]) -> bool:
    """检查当前组件是否与三笔前组件存在参考实现要求的重叠。"""
    if current < 3:
        return False
    current_bar = bars[lines[current].start.bar_index]
    previous = bars[lines[current - 3].start.bar_index]
    if _flipped_side_up(lines[current]):
        return current_bar.high_i64 >= previous.low_i64
    return current_bar.low_i64 <= previous.high_i64


def _confirm_low_segment(
    segment: ReferenceSegment,
    current: int,
    lines: Sequence[LineLike],
    bars: dict[int, BarLike],
) -> int:
    """确认向下线段是否被反向结构破坏。

    返回值沿用参考实现：`-1` 尚未确认，`0` 确认并直接反向，`1` 进入临时段。
    """
    current_bar = bars[lines[current].start.bar_index]
    end_bar = bars[lines[segment.end_index].start.bar_index]
    if not _flipped_side_up(lines[current]):
        if current_bar.low_i64 < end_bar.low_i64:
            segment.end_index = current
        return -1
    if current - segment.end_index < 3:
        return -1
    end_position = segment.end_index + 1
    for index in range(segment.end_index + 3, current + 1, 2):
        candidate = bars[lines[index].start.bar_index]
        end_candidate = bars[lines[end_position].start.bar_index]
        if candidate.high_i64 <= end_candidate.high_i64:
            end_position = index
            continue
        maximum = bars[lines[segment.start_index + 2].start.bar_index].high_i64
        minimum = bars[lines[segment.start_index + 1].start.bar_index].low_i64
        for cursor in range(segment.start_index + 3, segment.end_index, 2):
            first = bars[lines[cursor].start.bar_index]
            second = bars[lines[cursor + 1].start.bar_index]
            if minimum < first.low_i64:
                if maximum < second.high_i64:
                    minimum = first.low_i64
                maximum = second.high_i64
            else:
                minimum = first.low_i64
                maximum = second.high_i64
        return 1 if bars[lines[end_position].start.bar_index].high_i64 < minimum else 0
    return -1


def _confirm_up_segment(
    segment: ReferenceSegment,
    current: int,
    lines: Sequence[LineLike],
    bars: dict[int, BarLike],
) -> int:
    """确认向上线段是否被反向结构破坏，返回值语义同 `_confirm_low_segment`。"""
    current_bar = bars[lines[current].start.bar_index]
    end_bar = bars[lines[segment.end_index].start.bar_index]
    if _flipped_side_up(lines[current]):
        if current_bar.high_i64 > end_bar.high_i64:
            segment.end_index = current
        return -1
    if current - segment.end_index < 3:
        return -1
    end_position = segment.end_index + 1
    for index in range(segment.end_index + 3, current + 1, 2):
        candidate = bars[lines[index].start.bar_index]
        end_candidate = bars[lines[end_position].start.bar_index]
        if candidate.low_i64 >= end_candidate.low_i64:
            end_position = index
            continue
        maximum = bars[lines[segment.start_index + 1].start.bar_index].high_i64
        minimum = bars[lines[segment.start_index + 2].start.bar_index].low_i64
        for cursor in range(segment.start_index + 3, segment.end_index, 2):
            first = bars[lines[cursor].start.bar_index]
            second = bars[lines[cursor + 1].start.bar_index]
            if maximum > first.high_i64:
                if minimum > second.low_i64:
                    maximum = first.high_i64
                minimum = second.low_i64
            else:
                maximum = first.high_i64
                minimum = second.low_i64
        return 1 if bars[lines[end_position].start.bar_index].low_i64 > maximum else 0
    return -1


def _confirm_segment(
    segment: ReferenceSegment,
    current: int,
    lines: Sequence[LineLike],
    bars: dict[int, BarLike],
) -> int:
    """按当前段方向分派确认逻辑。"""
    if segment.up:
        return _confirm_up_segment(segment, current, lines, bars)
    return _confirm_low_segment(segment, current, lines, bars)


def _append_segment(
    target: list[ReferenceSegment],
    segment: ReferenceSegment,
    lines: Sequence[LineLike],
    known_at: int,
) -> None:
    """复制线段到结果集，并记录本次释放的可知时刻。"""
    value = copy.deepcopy(segment)
    value.known_at_bar_index = known_at
    _anchor_segment(value, lines)
    target.append(value)


def _anchor_segment(value: ReferenceSegment, lines: Sequence[LineLike]) -> None:
    """根据组成笔补齐线段端点，已确认段只需执行一次。"""
    start_line = lines[value.start_index]
    end_line = lines[value.end_index]
    value.start_bar_index = start_line.start.bar_index
    value.end_bar_index = end_line.start.bar_index
    value.start_time = start_line.start.time
    value.end_time = end_line.start.time
    if value.up:
        value.start_price_i64 = _low(start_line)
        value.end_price_i64 = _high(end_line)
    else:
        value.start_price_i64 = _high(start_line)
        value.end_price_i64 = _low(end_line)


def _update_segment(
    current: int,
    segment: ReferenceSegment,
    temporary: ReferenceSegment,
    lines: Sequence[LineLike],
    result: list[ReferenceSegment],
    bars: dict[int, BarLike],
) -> None:
    """用当前组件推进已存在段和临时段。

    这是 `_NCHDUAN` 中最核心的状态转移：当前组件可能只是延伸现有段，也可能
    确认现有段、确认临时段，或让临时段取代当前候选段。
    """
    known_at = lines[current].known_at_bar_index
    if temporary.start_index == temporary.end_index:
        status = _confirm_segment(segment, current, lines, bars)
        if status == -1:
            return
        if status == 0:
            segment.confirmed = True
            _append_segment(result, segment, lines, known_at)
            segment.start_index = segment.end_index
            segment.end_index = current
            segment.confirmed = False
            segment.up = not segment.up
        else:
            temporary.start_index = segment.end_index
            temporary.end_index = current
            temporary.confirmed = False
            temporary.up = not segment.up
        return

    status = _confirm_segment(temporary, current, lines, bars)
    if status == -1:
        current_bar = bars[lines[current].start.bar_index]
        temporary_start = bars[lines[temporary.start_index].start.bar_index]
        if _flipped_side_up(lines[current]) and not temporary.up:
            if current_bar.high_i64 > temporary_start.high_i64:
                segment.end_index = current
                temporary.start_index = temporary.end_index = 0
        elif temporary.up and current_bar.low_i64 < temporary_start.low_i64:
            segment.end_index = current
            temporary.start_index = temporary.end_index = 0
        return

    segment.confirmed = True
    _append_segment(result, segment, lines, known_at)
    segment.confirmed = False
    if status == 0:
        temporary.confirmed = True
        _append_segment(result, temporary, lines, known_at)
        segment.start_index = temporary.end_index
        segment.end_index = current
        segment.up = not temporary.up
        temporary.start_index = temporary.end_index = 0
    else:
        segment.start_index = temporary.start_index
        segment.end_index = temporary.end_index
        segment.up = temporary.up
        temporary.start_index = segment.end_index
        temporary.end_index = current
        temporary.up = not segment.up


@dataclass
class _SegmentScanState:
    """保存处理完某个笔前缀后的线段扫描状态，供尾部修订时回滚。"""

    minimum: int
    maximum: int
    status: int
    segment: ReferenceSegment
    temporary: ReferenceSegment
    result_count: int


class ReferenceSegmentAccumulator:
    """增量执行线段扫描，并按未变化笔前缀恢复已确定状态。"""

    def __init__(self) -> None:
        self._minimum = -1
        self._maximum = -1
        self._segment = ReferenceSegment()
        self._temporary = ReferenceSegment()
        self._status = 0
        self._result: list[ReferenceSegment] = []
        self._processed_count = 0
        self._raw_bar_count = 0
        self._bars: dict[int, BarLike] = {}
        self._states = [self._state()]

    def _state(self) -> _SegmentScanState:
        """复制当前小状态，不复制已经确认且不可变的线段结果前缀。"""
        return _SegmentScanState(
            minimum=self._minimum,
            maximum=self._maximum,
            status=self._status,
            segment=copy.deepcopy(self._segment),
            temporary=copy.deepcopy(self._temporary),
            result_count=len(self._result),
        )

    def _restore(self, preserved_line_count: int) -> None:
        """恢复共同笔前缀结束处的状态，并丢弃其后的候选线段。"""
        if not 0 <= preserved_line_count <= self._processed_count:
            raise ValueError("preserved line count is outside processed segment state")
        state = self._states[preserved_line_count]
        self._minimum = state.minimum
        self._maximum = state.maximum
        self._status = state.status
        self._segment = copy.deepcopy(state.segment)
        self._temporary = copy.deepcopy(state.temporary)
        del self._result[state.result_count :]
        del self._states[preserved_line_count + 1 :]
        self._processed_count = preserved_line_count

    def update(
        self,
        lines: Sequence[LineLike],
        raw_bars: Sequence[BarLike],
        preserved_line_count: int,
    ) -> list[ReferenceSegment]:
        """只重放共同前缀后的笔，并返回与全量扫描完全相同的线段快照。"""
        for bar in raw_bars[self._raw_bar_count :]:
            self._bars[bar.bar_index] = bar
        self._raw_bar_count = len(raw_bars)
        self._restore(min(preserved_line_count, len(lines)))
        for current in range(self._processed_count, len(lines)):
            if current >= 3:
                self._consume(current, lines)
            self._processed_count += 1
            self._states.append(self._state())
        return self._materialize(lines)

    def _consume(self, current: int, lines: Sequence[LineLike]) -> None:
        """使用一条新增笔推进参考线段状态机。"""
        if self._status == 0:
            if not _is_overlap(current, lines, self._bars):
                self._minimum = self._maximum = -1
                return
            if self._minimum == -1:
                self._minimum = self._maximum = current - 3
                for cursor in range(current - 2, current):
                    candidate = self._bars[lines[cursor].start.bar_index]
                    maximum = self._bars[lines[self._maximum].start.bar_index]
                    minimum = self._bars[lines[self._minimum].start.bar_index]
                    if candidate.high_i64 > maximum.high_i64:
                        self._maximum = cursor
                    if candidate.low_i64 < minimum.low_i64:
                        self._minimum = cursor
            if not _find_first_segment(
                current,
                lines,
                self._bars,
                self._maximum,
                self._minimum,
                self._segment,
            ):
                return
            self._status = 1
            self._minimum = self._maximum = 1
            return
        _update_segment(
            current,
            self._segment,
            self._temporary,
            lines,
            self._result,
            self._bars,
        )

    def _materialize(self, lines: Sequence[LineLike]) -> list[ReferenceSegment]:
        """复制确认前缀和当前候选段，补齐端点锚点后生成只读快照。"""
        if len(lines) < 4:
            return []
        result = list(self._result)
        final_known_at = lines[-1].known_at_bar_index
        if self._segment.start_index != self._segment.end_index:
            _append_segment(result, self._segment, lines, final_known_at)
        if self._temporary.start_index != self._temporary.end_index:
            _append_segment(result, self._temporary, lines, final_known_at)
        if result:
            result.pop(0)
        return result


def reference_segments(
    lines: Sequence[LineLike], raw_bars: Sequence[BarLike]
) -> list[ReferenceSegment]:
    """按参考 `_NCHDUAN` 规则从已确认笔生成线段。

    输入必须是方向严格交替、首尾相接的笔序列。返回结果包含已确认段和末尾
    当前段/临时段；调用方再决定哪些段可参与标准线段中枢。
    """
    return ReferenceSegmentAccumulator().update(lines, raw_bars, 0)


def reference_centers(
    lines: Sequence[LineLike],
    *,
    start_base: int = 1,
    minimum_line_count: int = 5,
) -> list[ReferenceCenter]:
    """按参考 `compute_bi_pivots/process_down_up` 扫描同奇偶组件中枢。

    从 `base=1` 开始，使用 `base` 与 `base+2` 的价格交集冻结 `ZD/ZG`，
    后续同奇偶组件只延长中枢时间范围，不改变冻结核心；首个不相交组件记录为
    `exit_index` 和 `leave_direction`。遗留笔中枢保留至少 5 条输入线的门槛；
    标准线段中枢传入 4，使基点前导线加三条已完成构件即可确认。
    """
    result: list[ReferenceCenter] = []
    if len(lines) < minimum_line_count:
        return result
    base = start_base
    while base < len(lines) - 2:
        seed_end = base + 2
        if max(_low(lines[base]), _low(lines[seed_end])) > min(
            _high(lines[base]), _high(lines[seed_end])
        ):
            new_base = min(base + 2, len(lines))
        else:
            low = max(_low(lines[base]), _low(lines[seed_end]))
            high = min(_high(lines[base]), _high(lines[seed_end]))
            cursor = base + 4
            end_index = seed_end
            while cursor < len(lines) and max(low, _low(lines[cursor])) <= min(
                high, _high(lines[cursor])
            ):
                end_index = cursor
                cursor += 2
            leave_direction: Literal["up", "down"] | None = None
            status: Literal["confirmed", "extended", "left"] = (
                "extended" if end_index > seed_end else "confirmed"
            )
            if cursor < len(lines):
                status = "left"
                leave_direction = "up" if _low(lines[cursor]) > high else "down"
            used = lines[base : end_index + 1]
            result.append(
                ReferenceCenter(
                    base_index=base,
                    seed_end_index=seed_end,
                    end_index=end_index,
                    exit_index=cursor if cursor < len(lines) else None,
                    start_bar_index=lines[base].start.bar_index,
                    end_bar_index=lines[end_index].end.bar_index,
                    start_time=lines[base].start.time,
                    end_time=lines[end_index].end.time,
                    zd_i64=low,
                    zg_i64=high,
                    known_at_bar_index=max(line.known_at_bar_index for line in used),
                    status=status,
                    leave_direction=leave_direction,
                )
            )
            new_base = cursor
        base = new_base - 1 if base == new_base - 2 else new_base - 2
    return result


def update_reference_centers(
    lines: Sequence[LineLike],
    previous: list[ReferenceCenter],
    changed_component_index: int,
    *,
    minimum_line_count: int = 5,
) -> list[ReferenceCenter]:
    """保留离开位置早于变化点的中枢，只从首个不确定中枢基点继续扫描。"""
    stable: list[ReferenceCenter] = []
    restart_base = 1
    for center in previous:
        if center.exit_index is None or center.exit_index >= changed_component_index:
            break
        stable.append(center)
        new_base = center.exit_index
        restart_base = new_base - 1 if center.base_index == new_base - 2 else new_base - 2
    return [
        *stable,
        *reference_centers(
            lines,
            start_base=max(restart_base, 1),
            minimum_line_count=minimum_line_count,
        ),
    ]
