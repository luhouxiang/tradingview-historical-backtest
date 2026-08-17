# 02 领域模型：级别、线段、中枢与走势

## 1. 对象层级

讨论采用的实现视角是：

```text
行情数据
→ 包含处理后的K线
→ 分型
→ 笔
→ 线段
→ 中枢
→ 走势类型
→ 结构信号
→ 策略/仓位/执行
```

但在严格理论表达中，中枢由“至少三个连续次级别走势类型”的重叠构成。
第53课给出的实用视角是：分析某一级别时，把完成的次级别走势视为没有
内部结构的线段。因此，本项目做如下工程映射：

- 标准模式：`component_kind = segment`，以已完成线段构造中枢；
- 递归模式：`component_kind = lower_level_movement`，以已完成次级别走势
  构造中枢；
- 两种模式的信号必须带上 `component_kind`，不得混用统计结果。

## 2. 级别是所有结构的必填属性

任何对象必须带 `analysis_level`。以下表达均不完整：

- “这里是中枢”；
- “出现背驰”；
- “这是三买”；
- “走势结束了”。

完整表达应类似：

```text
30m analysis_level 上，以已完成 5m movement/segment 为组件形成的中枢；
5m analysis_level 的趋势底背驰；
30m analysis_level 的 B3，由第一个已完成 5m 回试确认。
```

时间周期只是级别的常用代理，不应把“30分钟K线图”自动等同于“30分钟
递归级别”。宿主项目必须在配置中明确采用固定周期级别还是严格递归级别。

## 3. 基础数据对象

### 3.1 Component

构造中枢的最小完成组件：

| 字段 | 含义 |
|---|---|
| `component_id` | 不变标识 |
| `analysis_level` | 该组件所属级别 |
| `component_kind` | `segment` 或 `lower_level_movement` |
| `direction` | `up` / `down` |
| `start_time`, `end_time` | 时间范围 |
| `start_price_ticks`, `end_price_ticks` | 端点价格 |
| `high_ticks`, `low_ticks` | 全区间最高/最低 |
| `completed` | 是否可用于确认上级结构 |
| `confirmed_at` | 该组件何时成为已完成事实 |

连续组件应方向交替并在时间上不倒退。是否允许共享端点由底层线段算法决定，
但必须在全项目保持一致。

### 3.2 Centre

若三个连续、已完成组件 `A, B, C` 的价格区间为：

\[
[L_A,H_A], [L_B,H_B], [L_C,H_C]
\]

则候选核心区：

\[
ZD=\max(L_A,L_B,L_C),\qquad
ZG=\min(H_A,H_B,H_C)
\]

当 `ZD <= ZG` 时重叠非空，中枢成立。项目把核心区视为闭区间
`[ZD, ZG]`。`ZD == ZG` 是点状/退化中枢，不自动丢弃；如果宿主市场需要
最小宽度，应作为显式配置并单独统计。

第一批三个完成组件确认后：

- `ZD`、`ZG` 冻结；
- `DD` 是围绕中枢运动的最低点；
- `GG` 是围绕中枢运动的最高点；
- 后续震荡可更新 `DD/GG`，但不能回写核心区；
- 所有边界价格使用整数最小跳动单位。

建议字段：

| 字段 | 含义 |
|---|---|
| `centre_id` | 中枢不变标识 |
| `analysis_level` | 中枢级别 |
| `component_kind` | 构造组件口径 |
| `component_ids` | 按时间顺序的证据组件 |
| `ZD_ticks`, `ZG_ticks` | 冻结核心区 |
| `DD_ticks`, `GG_ticks` | 震荡外包络 |
| `started_at` | 第一个组件起点 |
| `confirmed_at` | 第三个组件完成并可见的时间 |
| `state` | `building/active/breakout_candidate/terminated/promotion_candidate` |

## 4. 走势与走势类型

### 4.1 走势

“走势”是图上发生的价格运动，且有级别。它是数据对象，不等同于已完成的
分类标签。

### 4.2 走势类型

同一级别的已完成走势类型分为：

- `consolidation`：只包含一个同级别中枢；
- `uptrend`：至少包含两个依次向上、互不重叠的同级别中枢；
- `downtrend`：至少包含两个依次向下、互不重叠的同级别中枢。

“上涨”和“下跌”是趋势；“盘整”不是趋势。任何级别的走势可以分解为
上涨、下跌、盘整的连接。

## 5. 中枢关系

### 5.1 延伸

后续组件/相应 `Z` 走势段区间与冻结核心 `[ZD,ZG]` 仍有交集，当前中枢
继续延伸。触及边界也算交集。

### 5.2 离开候选

一个完成的次级别组件完全位于核心外，只说明出现离开候选。还需要随后
第一个完成的反向回试才能判定：

- 回试进入或触及核心：离开失败，中枢继续；
- 回试完全在核心上方：B3；
- 回试完全在核心下方：S3。

### 5.3 新生、扩张与级别提升

- 新生：形成新的同级别中枢，并与前一同级别中枢满足趋势分离条件；
- 扩张：前后同级别中枢的外围波动发生重叠，形成更高级别中枢；
- 延伸累计到九个以上次级别组件时，必须进入高级别识别，不得无限延长
  原中枢。

“同级别中枢不重叠”到底以核心区还是完整震荡包络判断，是实现中最重要的
未决口径之一。原文更接近使用外围波动包络；现有部分简化实现只用核心区。
在用户确认前，不得混用，见 `08_open_questions.md`。

## 6. 信号状态

### 6.1 Candidate

结构正在形成但关键组件尚未完成，或反向回试尚未结束。例如：

- `divergence_candidate`
- `breakout_candidate`
- `b2_candidate`
- `b3_candidate`

候选可以被更新或失效，不能作为无未来函数回测成交依据。

### 6.2 Confirmed

所有结构前提和低级别结束确认均已满足。必须保存：

- 理论端点 `endpoint_time`；
- 可知时点 `confirmed_at`；
- 理论价格 `signal_price_ticks`；
- 若模拟成交，则保存 `executable_price_ticks`。

### 6.3 Invalidated

候选因新数据破坏条件而失效。不得删除候选历史；应发出版本化的失效事件，
以便审计实时识别表现。

## 7. 术语别名

| 中文 | 建议代码名 |
|---|---|
| 中枢 | `centre`（或项目已有 `center`，二选一保持一致） |
| 中枢核心下沿 | `ZD_ticks` |
| 中枢核心上沿 | `ZG_ticks` |
| 外围最低/最高 | `DD_ticks` / `GG_ticks` |
| 第一类买/卖点 | `B1` / `S1` |
| 第二类买/卖点 | `B2` / `S2` |
| 第三类买/卖点 | `B3` / `S3` |
| 趋势背驰 | `trend_divergence` |
| 盘整背驰 | `consolidation_divergence` |
| 区间套 | `nested_level_refinement` |
| 中枢震荡 | `centre_oscillation` |
| 中枢上/下移 | `centre_migration_up/down` |

