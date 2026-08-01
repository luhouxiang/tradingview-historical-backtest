# 案例：跌破 MA20 后反抽失败做空

本案例用于验证指标、策略状态、阶段信号、交易信号、回放与回测的分离，不代表完整交易建议。

## 1. 语义

核心描述：

> 价格先有效跌破 MA20；随后反抽并触碰 MA20；之后再次下跌并跌破“触碰 MA20 的那根 K 线”的开盘价，生成做空交易信号。

需要区分上涨趋势中的普通或假回踩，不能只要碰到 MA20 就做空。

## 2. 参数

| 参数 | 类型 | 示例 |
|---|---|---:|
| ma_period | int | 20 |
| break_mode | enum | close_below |
| touch_tolerance_ticks | int | 1 |
| max_retest_bars | int | 20 |
| trend_filter | enum | ma_slope_and_structure |
| invalidate_on_close_above_ma_bars | int | 2 |
| entry_mode | enum | next_bar_open |

所有默认值必须进入 canonical_parameters。

## 3. 状态机

~~~mermaid
stateDiagram-v2
    [*] --> WAIT_BREAK
    WAIT_BREAK --> WAIT_RETEST: 收盘有效跌破 MA20
    WAIT_RETEST --> WAIT_FAILURE: 反抽触碰 MA20
    WAIT_RETEST --> WAIT_BREAK: 超时或重新转强
    WAIT_FAILURE --> SIGNALLED: 跌破触碰K线开盘价
    WAIT_FAILURE --> WAIT_BREAK: 失效
    SIGNALLED --> WAIT_BREAK: 信号处理完成
~~~

建议状态：

- WAIT_BREAK_BELOW_MA20。
- WAIT_RETEST_TOUCH。
- WAIT_RETEST_FAILURE。
- SIGNALLED。

## 4. 阶段信号

1. BREAK_BELOW_MA20_CONFIRMED。
2. RETEST_TOUCHED_MA20。
3. RETEST_INVALIDATED。
4. RETEST_FAILURE_CONFIRMED。

阶段信号有独立 stage_signal_id，后续交易信号引用其父 ID。

## 5. 交易信号

当当前完成 K 线满足：

- 状态为 WAIT_RETEST_FAILURE。
- low 或 close 按配置跌破 touch_bar_open_i64。
- 趋势过滤器仍允许做空。

生成：

~~~json
{
  "side": "short",
  "action": "open",
  "reason_code": "RETEST_FAILED_BREAK_TOUCH_BAR_OPEN",
  "known_at_bar_index": 10548,
  "reference_price_i64": 2350
}
~~~

默认在下一根 K 线开盘由执行模型成交。若当前已经是数据末尾，信号保留但订单不成交，原因 NO_NEXT_BAR。

## 6. 上涨趋势假回踩过滤

首版不要把“假回踩”写成一句不可测试的主观判断。至少显式选择一种过滤模式，例如：

- MA20 斜率必须小于等于阈值。
- 更高周期趋势不得为强上涨。
- 跌破前必须存在已确认的下降结构。
- 反抽期间不得连续多根收盘站上 MA20。

过滤器是独立 Indicator/Filter，不嵌入 MA20 数值实现。每次拒绝记录 reason_code。

## 7. 图表显示

- MA20：Price Indicator 层。
- 跌破和触碰阶段：Strategy Overlay 层的小标记。
- 交易信号：Trading Overlay 层。
- 触碰 K 线开盘价参考线：Strategy Overlay 层，状态失效后隐藏。
- 状态文本：数据窗口或策略参数面板，不长期遮挡 K 线。

## 8. 必测场景

- 跌破后从未反抽。
- 触碰后成功下跌并触发。
- 触碰后连续站上 MA20，失效。
- 多次触碰，以哪一根作为 touch_bar 明确定义。
- 零成交量 K 线。
- 数据末尾产生信号。
- 上涨趋势中的短暂跌破被过滤。
- 回放与回测在同一 bar_index 生成同一 signal_id。

