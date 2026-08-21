# 计算流水线与在线状态机

## 1. 事件时间，而不是最终图形

程序必须同时保存三个时间：

- `endpoint_time`：形态端点所在原始K线时间。
- `confirmed_at`：根据后续数据首次能够确认的时间。
- `available_at`：系统完成计算、可供下单模块使用的时间。

必须满足：

```text
endpoint_time <= confirmed_at <= available_at <= execution_time
```

历史图上把线画到 `endpoint_time` 是可视化；回测若也在该时点成交，就是未来函数。

## 2. 数据契约

原始K线至少包含：

```text
symbol, timeframe, timestamp, open, high, low, close, volume,
is_suspended, price_limit_state, adjustment_factor
```

价格进入几何模块前转换为整数最小价位 `ticks`。除权处理必须选择一种并写入配置：前复权、后复权或不复权；禁止在同一回测中途切换。

每个派生对象都保存：

```text
id, type, level, direction, start_ref, end_ref,
low_ticks, high_ticks, status, endpoint_time, confirmed_at,
source_refs, invalidation_reason, revision
```

## 3. 逐层计算

### 3.1 包含处理

1. 读取新K线。
2. 与上一个已处理K线比较是否包含。
3. 根据此前已确定方向进行向上或向下合并。
4. 若方向未定，等待第一对非包含K线确定方向；不得用未来方向回填。
5. 保存合并K线到原始K线的多对一映射。

### 3.2 分型

每加入一个无包含K线，只判断末尾三根：

- 中根高、低均高于左右：顶分型候选。
- 中根高、低均低于左右：底分型候选。
- 其他：上升/下降/不产生分型。

分型在第三根收盘后才可确认。相邻分型不能共享违反结合律的K线。

### 3.3 笔

状态：

```text
SEEK_FIRST_FRACTAL
UP_EXTENDING       = (1,1)
TOP_FORMING        = (1,0)
DOWN_EXTENDING     = (-1,1)
BOTTOM_FORMING     = (-1,0)
```

合法转换：

```text
UP_EXTENDING   -> TOP_FORMING
DOWN_EXTENDING -> BOTTOM_FORMING
TOP_FORMING    -> UP_EXTENDING | DOWN_EXTENDING
BOTTOM_FORMING -> DOWN_EXTENDING | UP_EXTENDING
```

异类分型只有满足独立K线间隔和区间分离条件才确认新笔。同类竞争按第77课步骤处理：向上过程中保留更高顶，向下过程中保留更低底；被替换的候选保留审计记录。

### 3.4 线段

1. 从至少三笔、奇数笔、前三笔有重叠的候选开始。
2. 构造与原线段方向相反的特征序列。
3. 对特征序列按方向处理包含。
4. 若前两个特征元素无缺口，特征序列分型确认端点。
5. 若有缺口，必须继续检查反向特征序列的反向分型。
6. 只有反向线段破坏成立后，原线段进入 `confirmed`。

线段内部出现更高/更低点不自动移动线段端点。若线段作为中枢组件，另算 `normalized_low/high`，不能篡改原端点。

### 3.5 中枢

输入必须是连续、完成、同级别、同组件类型的至少三个对象。前三组件：

```text
ZD = max(low_1, low_2, low_3)
ZG = min(high_1, high_2, high_3)
exists iff ZD <= ZG
```

中枢确认后 `ZD/ZG` 固定。后续组件只改变 `DD/GG` 和延伸计数，不允许滑动核心。如果已延伸6段（总计9段）则发出高级别中枢候选。

### 3.6 走势类型

- 1个本级中枢：盘整。
- 至少2个同向、核心及波动不重叠的本级中枢：趋势。
- 后一中枢在前一之上：上涨；在前一之下：下跌。
- 两同级中枢波动重叠：不是标准趋势，转入高级别中枢识别。

### 3.7 背驰与买卖点

先由结构模块给出可比段，再由力度模块比较。标准趋势背驰至少要求：

1. `a + A + b + B + c` 构成趋势结构。
2. `A/B` 为同级别中枢。
3. `c` 创方向新极值。
4. `c` 含 `B` 的第三类点，且为较低级别走势。
5. `force(c) < force(a)`；MACD仅作代理。
6. 末段内部转折已确认。

盘整背驰只比较同一中枢两侧同向段，不得标成标准趋势B1/S1。

## 4. 信号生命周期

```text
CANDIDATE
  -> CONFIRMED       条件全部满足
  -> INVALIDATED     候选端点被更极端端点替换、回试回中枢、结构升级等

CONFIRMED
  -> ACTIVE          可执行窗口开启
  -> CONSUMED        已执行或第一次回试资格已使用
  -> EXPIRED         超过策略允许结构阶段
  -> SUPERSEDED      更高级别结构取代，但历史记录保留
```

第三类点的“第一次回试”必须用 `consumed=true` 防止同一中枢重复发出B3/S3。

## 5. 中枢五状态

相对最近一个已确认同级别中枢，走势只有五个操作状态：

1. `INSIDE_CENTER`
2. `BELOW_NO_S3`
3. `BELOW_AFTER_S3`
4. `ABOVE_NO_B3`
5. `ABOVE_AFTER_B3`

状态转换由完成的次级别离开/回试事件触发，不由单根K线穿越触发。

```text
INSIDE_CENTER -> ABOVE_NO_B3 | BELOW_NO_S3
ABOVE_NO_B3   -> ABOVE_AFTER_B3 | INSIDE_CENTER | PROMOTED_CENTER
BELOW_NO_S3   -> BELOW_AFTER_S3 | INSIDE_CENTER | PROMOTED_CENTER
ABOVE_AFTER_B3 -> NEW_CENTER | DIVERGENCE_END | INSIDE_CENTER
BELOW_AFTER_S3 -> NEW_CENTER | DIVERGENCE_END | INSIDE_CENTER
```

## 6. 中阴（BARDO）状态

原走势背驰完成后，新走势类型未确认前进入 `BARDO`。它不是可预测方向，而是需要继续分类：

- 已形成新的更高级别首中枢，且位于原走势最后中枢范围：`BARDO_HEALTHY`。
- 回到原走势倒数第二或更早中枢：`BARDO_DANGEROUS_FOR_OLD_DIRECTION`。
- 形成相应B3/S3：结束中阴并确认新状态。
- 无第三点而延伸到9段：升级中枢。

BOLL收口、Zn越界等只发出 `aux_warning`，不能结束BARDO。

## 7. 多级别调度

等级不等于K线周期。工程上需显式提供 `level_graph`：每级由何种下级完成对象组成。建议至少同时维护三层：`L0/L1/L2`，并把周期映射只作为可配置观察窗口。

高级别状态变化必须由低级别事件先触发。调度器按以下顺序处理同一时间戳：

```text
bar close
-> inclusion/fractal
-> bi updates
-> segment updates
-> center/movement updates (low to high)
-> divergence and buy/sell events
-> risk filters
-> strategy decisions
-> order generation for next executable time
```

## 8. 修订与审计

- 不删除被替换候选；设置 `invalidated` 和原因。
- 已确认对象若因数据修复改变，生成新 `revision`，不得静默覆盖。
- 复权、坏点修复、缺失K线补齐必须令整个下游DAG失效重算。
- 图上标记应区分端点时间与确认时间，建议用实心/空心符号或两条竖线。

