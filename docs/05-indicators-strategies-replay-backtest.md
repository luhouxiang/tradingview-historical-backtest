# 05 指标、策略、回放、回测与调优

## 1. 分层

~~~mermaid
flowchart TD
    D["标准 K 线"] --> I["Indicator<br/>MA、MACD、ATR、分型、笔、段、中枢"]
    I --> S["Strategy<br/>组合指标与状态机"]
    S --> E["Event Stream<br/>阶段信号、交易信号、图形事件"]
    E --> X["Execution Model<br/>订单、成交、持仓"]
    X --> M["Metrics<br/>收益、回撤、胜率"]
    O["Optimizer<br/>参数建议与目标"] --> S
    M --> O
~~~

硬规则：

- Indicator 不决定交易。
- Strategy 不直接修改 UI。
- Execution Model 不重新解释策略。
- Optimizer 只调用标准回测入口，不进入策略内部。

## 2. 指标定义

每个指标提供不可变定义：

~~~json
{
  "indicator_id": "ma",
  "algorithm_version": "1.0.0",
  "source_hash": "sha256:...",
  "input": "bars.v1",
  "parameter_schema": {
    "period": {"type": "integer", "minimum": 1, "default": 20}
  },
  "outputs": [
    {"name": "ma", "type": "float64", "pane_role": "price"}
  ],
  "warmup": {"kind": "bars", "value": 19},
  "causal": true
}
~~~

普通密集指标按 bar_index 写 values.parquet。结构指标写语义对象和事件表。

## 3. 缠论引擎

模块建议：

- include_merge：包含关系处理。
- fractal_detector：分型。
- bi_builder：笔。
- zhongshu_builder：中枢。
- segment_builder：段。
- checkpoint：连续状态序列化。
- event_emitter：upsert/delete 事件。

已确认规则之一：

- `E:\work\py\kline-chart\klinechart\algo\chanlun\c_bi.py` 是包含、分型和笔的权威参考算法。
- 分型由左、中、右三根去包含后的独立 K 线构成，中间 K 线的高低点同时严格高于两侧为顶、同时严格低于两侧为底；右侧独立 K 线出现即封存。
- 笔至少跨越 5 根独立 K 线；端点选择必须执行参考算法的同类分型极值替换及后继异类分型区间确认，不使用 ATR 振幅门槛。
- 相邻笔必须共享端点且方向严格交替，不允许连续两笔向上或连续两笔向下。
- 笔中枢按 `algo-ui` 的 `compute_bi_pivots/process_down_up` 扫描同奇偶位置笔的区间交集；后续同奇偶笔相交时延长时间范围而不改变 ZD/ZG。
- 段按 `algo-ui` 的 `_NCHDUAN` 及其首段发现、正反向确认、临时段修订函数计算。
- 笔中枢和段的完整规则、金样与因果约束见 `docs/13-chan-bi-center-segment-algorithm.md`。

Python 输出时间、价格、确认状态和修订，不输出像素。

增量修订协议可表达：

~~~json
{
  "replace_from_time": 1785502800000,
  "upserts": [
    {
      "id": "bi-1842",
      "start_time": 1785502800000,
      "start_price_i64": 2351,
      "end_time": 1785506400000,
      "end_price_i64": 2386,
      "confirmed": true,
      "revision": 27
    }
  ],
  "delete_ids": ["bi-1841"],
  "data_revision": "sha256:..."
}
~~~

首期批量历史模式的权威记录仍是 events.parquet；上述结构用于 Go 按范围向 UI 返回。

## 4. 策略接口

策略必须实现概念接口：

~~~text
metadata() -> StrategyDefinition
initialize(context) -> State
on_bar(state, bar, indicator_snapshot) -> Transition
finalize(state) -> FinalOutput
~~~

Transition 可包含：

- 新状态。
- 零到多个阶段信号。
- 零到多个交易信号。
- 零到多个图形事件。
- 诊断字段。

策略不得：

- 读取 future bars。
- 读取当前屏幕范围。
- 直接创建订单成交。
- 写最终 run 文件；由引擎统一写。

首版示例 `ma20_retest_short` 使用 `waiting_break → waiting_retest →
waiting_retest_failure → short_open` 状态机；MA 下破、反抽触及、反抽失败和重新站上 MA
分别使用稳定 reason_code。它与回放共享同一个 `on_bar` 事件源，执行引擎只消费
`trade_signal`，策略本身不生成订单或成交。

## 5. 状态和信号

状态变化记录：

~~~json
{
  "known_at_bar_index": 10542,
  "state_from": "waiting_retest",
  "state_to": "waiting_retest_failure",
  "reason_code": "PRICE_TOUCHED_MA20"
}
~~~

交易信号：

~~~json
{
  "signal_id": "SIG-10548-SHORT",
  "parent_stage_signal_id": "STAGE-10542-RETEST",
  "known_at_bar_index": 10548,
  "side": "short",
  "price_i64": 2350,
  "reason_code": "RETEST_FAILED_BREAK_OPEN"
}
~~~

所有 reason_code 必须是稳定枚举，可读文本只用于显示。

## 6. 回放

### 6.1 预计算模式

首期优先一次性生成完整因果事件流。Vue 回放时：

- replay_cursor 从 start_bar_index 向前移动。
- 显示 bar_index 小于等于游标的 K 线。
- 应用 known_at_bar_index 小于等于游标的事件。
- 后退时从最近 UI 快照恢复并重新应用事件，或从事件索引重新构建。

这比每前进一步都调用 Python 更稳定。

### 6.2 速度

支持：

- 单步。
- 0.25x、0.5x、1x、2x、5x、10x。
- 暂停。
- 跳到指定 bar_index。

速度只影响 UI 定时器，不影响算法输出。

## 7. 回测执行

### 7.1 默认成交语义

- on_bar 在当前 K 线完成后调用。
- 当前收盘产生的信号不得以当前 K 线的更早价格成交。
- 默认在下一根 K 线 open 成交。
- 若用户选择 close 成交，run.json 必须记录并在 UI 明示。
- 止损/止盈同根内同时触发时采用明确、可配置且保守的优先规则。

### 7.2 资金与合约

首期最少支持：

- 初始资金。
- 合约乘数。
- 每手固定或按金额比例手续费。
- 固定 tick 或基点滑点。
- 保证金比例。
- 单向净持仓模型。
- 开仓、平仓、多、空。

当前示例策略只发空头开平信号，但执行模型同时接受 `open_long`、`close_long`、
`open_short`、`close_short`。最后一根 K 线上的 next-open 信号以 `NO_NEXT_BAR` 拒绝，
不会伪造成交。

不支持的交易所细则必须在结果中标为模型限制，不能默认为真实撮合。

### 7.3 输出

- strategy_states：每次状态变化。
- stage_signals：阶段信号全生命周期。
- trade_signals：交易信号与过滤原因。
- orders：订单意图与状态。
- fills：模拟成交。
- trades：配对交易。
- positions：持仓快照。
- equity：权益、可用资金、保证金、回撤。
- summary：聚合指标。

## 8. 汇总指标

至少：

- 总收益和年化收益。
- 最大回撤及起止时间。
- Sharpe，明确无风险利率和年化因子。
- 交易次数。
- 胜率。
- 平均盈利、平均亏损和盈亏比。
- Profit factor。
- 最大连续盈利/亏损次数。
- 手续费和滑点总成本。
- 多头与空头分拆。

分母为零时写 null 和 reason，不写无穷大字符串。

## 9. 可复现性

相同：

- data_revision。
- 策略源码哈希。
- 参数。
- 引擎版本。
- 费用与成交模型。
- 随机种子。

应产生完全一致的信号、成交和汇总。文件元数据时间可不同，但事实表内容哈希应一致。

## 10. 参数优化

优化器位于独立模块，只能通过标准 BacktestRequest 调用回测。

定义：

- SearchSpace：参数名、类型、范围、步长或候选。
- Objective：收益、Sharpe、最大回撤、胜率、平均期望等。
- Constraint：最大回撤上限、最少交易次数等。
- Evaluation：参数、run_signature、指标和状态。
- Study：搜索算法、随机种子、预算和所有 Evaluation。

首期仅预留接口；后续先实现 grid 与 seeded random，再考虑贝叶斯或 AI。

多目标不得用模糊“最好”表达，必须显式：

- 主目标。
- 次级排序。
- 硬约束。
- 训练/验证区间。

禁止优化器直接访问 UI 当前窗口或修改策略源码。

## 11. 过拟合防护

后续优化最低要求：

- 时间顺序训练/验证拆分。
- Walk-forward。
- 参数稳定性分析。
- 交易次数下限。
- 手续费与滑点敏感性。
- 不把同一区间既用于筛选又用于最终报告。

## 12. 算法测试

- 普通指标对照黄金数据。
- 缠论对象与事件快照测试。
- 前缀不变性：仅输入前 N 根与输入完整数据后截取 N 时，N 之前已知事件一致。
- 检查点恢复结果与从头运行一致。
- 回放信号与回测信号逐条一致。
- 相同随机种子结果一致。
- 改变算法版本或参数后 cache_key 必须变化。
