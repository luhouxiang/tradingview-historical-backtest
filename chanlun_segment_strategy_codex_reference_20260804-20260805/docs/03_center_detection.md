# 03 线段中枢识别与状态机

## 1. 输入契约

标准算法只接收：

- 按时间排序；
- 方向交替；
- 已完成；
- 具有整数tick高低点；
- 同一 `analysis_level` 和 `component_kind=segment` 的组件流。

未完成线段可以进入候选层，但不能进入确认中枢的前三个组件。

## 2. 中枢形成

对连续完成组件 `s[i-2], s[i-1], s[i]`：

```text
ZD = max(low[i-2], low[i-1], low[i])
ZG = min(high[i-2], high[i-1], high[i])
```

若 `ZD <= ZG`：

1. 新建 `centre_candidate`；
2. 第三个组件的 `confirmed_at` 到达后确认中枢；
3. 冻结 `[ZD,ZG]`；
4. 初始化 `DD=min(low)`、`GG=max(high)`；
5. 记录三条证据组件。

滑动窗口搜索必须避免同一组件被不受控地重复归入多个“标准分解”。走势
多义性可以保留为候选分解，但生产策略必须选定一个稳定、可审计的分解策略。

## 3. 延伸规则

核心区闭区间交集函数：

```text
overlaps_core(component, centre) =
    component.high_ticks >= centre.ZD_ticks
    and component.low_ticks <= centre.ZG_ticks
```

后续与中枢形成方向一致的 `Z` 走势段若满足交集，中枢延伸。工程实现若对
每一个线段而非 `Z` 走势段检查交集，必须在配置中明确，并用图例测试差异。

延伸时：

- `[ZD,ZG]` 不变；
- 更新 `DD/GG`；
- 追加证据组件；
- 清除已经失败的离开候选；
- 若累计组件达到9个，发出 `promotion_candidate`。

## 4. 离开与第一次回试

### 4.1 向上离开

完成组件形成向上离开，且其运动使价格完全脱离核心上方，进入
`up_breakout_candidate`。随后只考察第一个完成的反向次级别回试：

- `retest_low > ZG`：确认 B3，原中枢结束；
- `retest_low == ZG`：触边，离开失败，中枢延伸；
- `retest_low < ZG`：重新进入核心，中枢延伸。

### 4.2 向下离开

镜像规则：

- `retest_high < ZD`：确认 S3；
- `retest_high == ZD`：触边，延伸；
- `retest_high > ZD`：重新进入，延伸。

### 4.3 必须是第一次

第一次回试一旦进入或触及核心，该离开候选已经失败。不得跳过它，再把后续
某一次未触及核心的回试标成同一中枢的 B3/S3。

## 5. 至少五个次级别组件

一个标准 B3/S3 至少需要：

```text
component 1 + component 2 + component 3  → centre
component 4                              → departure
component 5                              → first retest
```

若组件4或5尚未完成，只能发候选事件。

## 6. 中枢提升/扩展

第33课给出的实用限制是：原三段形成中枢后，再出现六段延伸，即总计至少
九段次级别走势时，可以解释为更高级别中枢。项目规则：

```text
if centre.component_count >= 9:
    centre.state = promotion_candidate
    stop unbounded same-level extension
    invoke higher-level centre recognizer
```

这里不意味着立即删除原中枢。应保留：

- 原级别中枢事件；
- 高级别候选的证据映射；
- 采用何种分解的版本号。

## 7. 建议状态机

| 状态 | 进入条件 | 允许输出 |
|---|---|---|
| `building` | 少于3个完成组件 | 仅组件候选 |
| `active` | 3个完成组件有重叠 | `centre_confirmed`、震荡候选 |
| `up_breakout_candidate` | 完成向上离开 | `B3_candidate` |
| `down_breakout_candidate` | 完成向下离开 | `S3_candidate` |
| `awaiting_first_retest` | 离开后反向组件展开 | 仅候选更新 |
| `terminated_by_B3` | 第一次完成回试低点严格高于ZG | `B3_confirmed` |
| `terminated_by_S3` | 第一次完成回抽高点严格低于ZD | `S3_confirmed` |
| `promotion_candidate` | 延伸组件数达到9或扩展关系成立 | 高级别识别请求 |

## 8. 伪代码

```python
def on_completed_component(c, state):
    assert c.completed
    assert c.prices_are_integer_ticks

    if state.centre is None:
        state.window.append(c)
        if len(state.window) >= 3:
            a, b, c3 = state.window[-3:]
            zd = max(a.low_ticks, b.low_ticks, c3.low_ticks)
            zg = min(a.high_ticks, b.high_ticks, c3.high_ticks)
            if zd <= zg:
                state.centre = confirm_centre(a, b, c3, zd, zg)
        return

    z = state.centre

    if state.awaiting_first_retest:
        if not is_completed_reverse_retest(c, state.departure):
            return
        if state.departure.direction == "up":
            if c.low_ticks > z.ZG_ticks:
                confirm_B3(z, c)
            else:
                resume_extension(z, c)  # equality also lands here
        else:
            if c.high_ticks < z.ZD_ticks:
                confirm_S3(z, c)
            else:
                resume_extension(z, c)
        return

    if is_completed_departure(c, z):
        state.departure = c
        state.awaiting_first_retest = True
        emit_candidate(c, z)
        return

    if overlaps_core(c, z):
        extend_centre(z, c)
        if z.component_count >= 9:
            emit_promotion_candidate(z)
```

`is_completed_departure` 与 `is_completed_reverse_retest` 不能仅看单根K线；
它们必须引用项目的完成走势/线段算法。

## 9. 不变量

1. `ZD <= ZG`。
2. 确认后 `ZD/ZG` 不变。
3. `DD <= ZD <= ZG <= GG`。
4. `confirmed_at >= endpoint_time`。
5. 任何确认中枢的前三个证据组件均已完成。
6. B3/S3证据中只有一个“第一次回试”。
7. B3满足一tick以上严格分离；S3同理。
8. 九段以上延伸不会继续被当作无限同级中枢而不触发升级。
9. 不同 `analysis_level` 或 `component_kind` 的组件不得进入同一中枢。

## 10. 失败案例

- 三个区间只有浮点误差造成的伪重叠；
- 用未完成线段的临时高低点确认中枢；
- 突破核心立即确认三买，没有等待回试；
- 第一次回试触边失败，跳过后把第二次回试算成三买；
- 以旧中枢而不是当前分解中最近同级中枢计算三买；
- 中枢核心随震荡高低点扩张，导致历史边界漂移；
- 同一套结果混合笔中枢与线段中枢。

