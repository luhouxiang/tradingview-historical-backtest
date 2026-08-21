# 推荐实现路线

## 里程碑0：数据与审计骨架

交付：整数ticks、原始/处理K线映射、对象ID、三类时间、revision日志。

验收：

- 同一输入可重复得到相同对象ID。
- 数据修订能使下游对象失效并重算。
- 不存在浮点边界比较。

## 里程碑1：包含—分型—笔

实现：`BAR-INCLUDE-*`、`FX-*`、`BI-*`、四状态。

验收：

- 第62、69、77、81课图的人工核对。
- 同类分型更高/更低替换。
- 最小有效笔、共享K线、无独立K线反例。
- 用户回归图能明确显示对象类型和端点依据。

## 里程碑2：线段

实现：特征序列、包含、无缺口/有缺口两类破坏、非极值端点、标准化区间。

验收：

- 第67、71、78、79、81课边界图。
- 笔破坏不误判线段破坏。
- 结构端点与全段极值不混字段。

## 里程碑3：标准中枢与走势

实现：中枢核心、延伸、9段升级、两中枢位置、盘整/趋势。

验收：

- 拒绝笔作为标准组件。
- `ZD/ZG`确认后不滑动。
- 点中枢 `ZD==ZG`、无交集、等号边界。
- 高级别升级与同级新中枢互斥。

## 里程碑4：买卖点与背驰

实现：B1/B2/B3及镜像、盘整背驰、趋势背驰、区间套。

验收：

- B3/S3闭区间等号测试。
- “第一次回试”只触发一次。
- 趋势背驰至少两个中枢。
- 指标关闭不改变结构对象。

## 里程碑5：状态机策略

优先顺序：

1. `STR-001` 固定级别五状态。
2. `STR-002` 只做二买。
3. `STR-003` 只做三买。
4. `STR-004` 中枢震荡。
5. `STR-005/007` 同级分解与三层级分类。
6. `STR-008/009` 反弹和底部构造。

验收：每个策略对所有合法后续分支都有动作：`enter/reduce/hold/exit/wait/disable`，不得留下隐式“预测”。

## 里程碑6：辅助与排序

加入MACD、BOLL、Zn、分型强弱、均线等级。所有输出使用 `aux_*` 命名空间，只能：

- 调整候选优先级。
- 限制仓位。
- 发预警。
- 为力度提供代理值。

不能创建或删除标准中枢、笔、线段。

## 里程碑7：回测、组合与上线

按 `docs/04_risk_and_backtest_protocol.md` 推进。至少完成：

- 逐bar事件回放。
- 成交约束。
- 样本外/走步验证。
- 策略、形态、级别分层绩效。
- 风险熔断和影子运行。

## 建议模块接口

```text
BarNormalizer.push(raw_bar) -> ProcessedBarEvent[]
FractalEngine.on_bar(event) -> FractalEvent[]
BiEngine.on_fractal(event) -> BiEvent[]
SegmentEngine.on_bi(event) -> SegmentEvent[]
CenterEngine.on_component(event, level) -> CenterEvent[]
MovementEngine.on_center(event) -> MovementEvent[]
DivergenceEngine.on_movement(event, force_provider) -> SignalEvent[]
StrategyEngine.on_events(events, portfolio_state) -> Decision[]
RiskEngine.evaluate(decisions, market_state) -> ApprovedOrderIntent[]
ExecutionSimulator.execute(intents, bars) -> FillEvent[]
```

每个模块只能依赖前一层确认事件，禁止策略直接扫描原始K线“自己再画一遍”。

## 完成定义

程序不是“能画线”就完成。最低完成标准：

- 形态识别唯一且可追溯。
- 在线确认无未来函数。
- 原课图、合成边界和真实数据三类测试都通过。
- 标准与辅助/实验对象完全隔离。
- 回测结果可复现并包含全部成本。
- 没有任何收益保证性字段或文案。
