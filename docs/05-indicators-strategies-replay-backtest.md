# 05 指标、策略、回放、回测与调优

## 1. 分层

~~~mermaid
flowchart TD
    D["标准 K 线"] --> I["Indicator<br/>MA、MACD、ATR、分型、笔、段、中枢、背驰、买卖点"]
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
- segment_zhongshu_builder：标准线段中枢。
- divergence_and_trade_point_builder：趋势/盘整背驰和一二三类买卖点。
- checkpoint：连续状态序列化。
- event_emitter：upsert/delete 事件。

已确认规则之一：

- `E:\work\py\kline-chart\klinechart\algo\chanlun\c_bi.py` 仅作为包含和分型实现的参考；笔端点必须另外满足本项目的 `processed_k` 全区间极值断言。
- 分型由左、中、右三根去包含后的独立 K 线构成，中间 K 线的高低点同时严格高于两侧为顶、同时严格低于两侧为底；右侧独立 K 线出现即封存。
- 笔至少跨越 5 根独立 K 线；上涨笔必须由区间最低底连接到区间最高顶，下降笔必须由区间最高顶连接到区间最低底。同类分型按后顶更高、后底更低替换，不使用 ATR 振幅门槛。
- 相邻笔必须共享端点且方向严格交替，不允许连续两笔向上或连续两笔向下。
- 笔中枢按 `algo-ui` 的 `compute_bi_pivots/process_down_up` 扫描同奇偶位置笔的区间交集；后续同奇偶笔相交时延长时间范围而不改变 ZD/ZG。
- 段按 `algo-ui` 的 `_NCHDUAN` 及其首段发现、正反向确认、临时段修订函数计算。
- 笔中枢和段的完整规则、金样与因果约束见 `docs/13-chan-bi-center-segment-algorithm.md`。
- 标准线段中枢只接受正宽重叠；背驰、MACD 力度和一二三类买卖点的完整口径见 `docs/14-chan-108-segment-center-divergence-trade-points.md`。

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

里程碑 10 的 `centre_oscillation_spread` 同样通过上述统一入口运行。它只消费
Python 缠论信号层已经确认的活动线段中枢、中枢内盘整背驰、Zn 监视和标准三类点事件：
底/顶背驰分别形成 `swing_buy/swing_sell`，Zn 只调整强弱仓位或触发风险过滤；
标准 B3/S3、中枢离开、九构件升级风险或来源修订形成 `stop_oscillation`。
独立策略在三类点确认时平掉震荡仓并发布 `handoff_to_trend`，不在策略内部复制趋势算法。
预估往返成本、最小净震荡空间、快速执行能力和单中枢进场次数上限都是显式参数并随 run 留档。

`same_level_decomposition_program` 也复用同一因果入口。当前单级别 profile 只把 Python
前一层确认的连续交替线段作为固定操作级别的规范分解单元，不从 K 线重画结构，也不把它冒充
已经完成的递归多级别走势。奇偶方向由 `odd_direction_is_down` 在运行开始前固定；同向
`Ai/Ai+2` 后段未严格创新方向端点（等号也算未创新）或端点确认盘整背驰时反向操作，
创新极值且无背驰时持有，并等待 `Ai+3` 分类为“等待新同级结构”或“继续围绕原中枢”。
已消费结构修订会平仓并从修订时点重建；九构件只发布高级别中枢候选并停用小级别实例，
直到跨级别对象图能够确认并承接真实的级别迁移。

`three_level_complete_classification` 使用运行前固定的 `segment_center_chain_v1` 对象图，
而不是把 1/5/30 分钟 K 线直接拼成三个级别。低层只消费已确认线段背驰或标准一类点，
中层只消费标准线段中枢、关联三类点和确认回归段，高层只消费已确认中枢迁移链。
状态必须按低层转折、中层三类点或中枢延续、高层变化候选的顺序推进；提前出现的高层事件
不会留待未来补用。回归恰好接触 `ZD/ZG` 继续属于闭区间三类点，严格进入核心才是中枢延续。
三种合法后续分支的处理能力作为参数写入 run；任一能力关闭时参与上限为 0，但分类事件仍会
进入对象树和主图，便于审计为什么没有下单。当前高层输出只是 `candidate`，不等同于已确认
高级别中枢。

`target_level_rebound_segmented_operation` 使用固定对象图 `segment_rebound_rhythm_v1`。
目标级别只由已确认标准 B1/S1 启动，类一买/类一卖不满足前提；次级别节奏映射为与
转折端点首尾连续的已确认线段。第一条目标方向段允许按配置数量部分兑现，第一条反向段
完成且未严格穿过来源极值时回补；相等不算破坏。首个标准线段中枢形成后，关联 B3/S3
及完整离开/回试对象链决定成功或失败；目标方向三类点后的首条跟随段必须严格创新极值且
端点无已确认背驰，才能把剩余仓位移交趋势持有。状态与图表事件始终记录
`assume_second_directional_leg_new_extreme=false`，不得用预测替代已完成结构。来源转折、
线段、中枢、三类点或跟随对象发生修订时，从修订已知时点退出并重置。

`bottom_top_construction` 使用固定对象图 `segment_bottom_top_build_v1`，并把精确构造与
粗略分型区间观察分开。精确底部只接受已确认标准 B1，顶部只接受已确认标准 S1；类一、
候选和未确认点不能启动。策略随后只锁定从该一类点端点开始的首个已确认标准线段中枢：
底部以该中枢最先确认的标准 B3 为成功、S3 为失败，顶部以 S3 为成功、B3 为失败。
成功后保持已有仓位穿过底顶之间的连接走势，直到新的标准一类点移交下一构造；失败和
来源修订立即退出。所有判断都来自 Python 前一层事件，不在策略中重扫 K 线重建结构。

粗略层的 `processed_fractal_zone_v1` 使用分型中间处理后 K 线的
`[zone_low_i64, zone_high_i64]`。底部严格跌破下沿失败，确认后的连续收盘严格站上上沿
成功；顶部完全镜像。等号不完成也不失败，连续根数由 `coarse_effective_hold_bars` 显式
留档，默认 1。粗略层只产生状态、阶段和图表事件，`coarse_zone_executes_trade=false`，
不会开仓；回测交易只能由独立的标准 B1/S1 精确层产生。

`aux_ma_kiss_legacy` 是第 11、12、14、72 课早期均线系统的辅助适配器，不属于后续严格
中枢/走势几何。它从 Python 指标注册表取得短/长 MA 与可选 MACD：接近区外走平后恢复
原方向为 `aux_flying_kiss`，进入接近区后同侧离开为 `aux_lip_kiss`，期间穿越后离开为
`aux_wet_kiss`。同一多头排列第一次同向吻只产生 `aux_legacy_B2_candidate`；最近空头吻后
出现更低低点且负 MACD 柱变弱只产生 `aux_legacy_B1_candidate`。所有阈值和周期写入 run，
等号边界有测试。该算法当前借用 strategy 因果事件通道以复用回放、不可变 run 和图表投影，
但不发布 `strategy_state`、`stage_signal` 或 `trade_signal`，界面也明确标记为非标准、不交易。

`aux_macd_zero_axis_defense` 是独立的辅助风险开关。数据集 K 线周期必须与运行前固定的
`minimum_timeframe_minutes` 相等；DIFF/DEA 两线同时严格低于负缓冲并完成连续确认后发布
`aux_macd_risk_off`，两线同时严格高于正缓冲并完成另一组连续确认后发布
`aux_macd_risk_on_candidate`。边界等号、单线越界和确认中断不触发。前者把未来组合层可消费的
`max_participation_multiplier` 降为 0，后者恢复为 1，但都不生成订单或持仓；本里程碑只把风险
事件写入不可变 run、辅助对象树和主图。MACD 是辅助工具，固定观察周期也不等于结构级别。

`aux_boll_bardo_warning` 复用普通指标注册表的 BOLL 上中下轨。收盘严格在轨外表示超强，
等于轨道已属于一般区；回轨后严格创新极值但连续不能有效重返同侧轨外时，发布非标准的
`aux_boll_superstrong_exit`。对应上轨向下、下轨向上转折只发布二卖阻力/二买支撑区域，
不冒充标准 B2/S2。连续严格缩口只有在前一层已确认趋势背驰启动且尚未被标准三类点、
中枢迁移、九构件升级或来源修订解决的结构上下文内，才发布
`aux_boll_bardo_end_or_promotion_warning`；该警告明确不能确认三类点。BOLL 周期是观察参数，
不是结构级别。所有事件进入不可变零交易 run、辅助对象树和主图，不产生订单或持仓。

`center_monitor` 是 `ALG-AUX-004` 的权威 Z/Zn 结果，继续属于同一个缠论 `StrategySource`，
不创建第二套中枢。标准线段中枢冻结核心 `[ZD,ZG]` 映射为 `[A,B]`；组成线段闭区间中点依次
形成 Zn。`z_twice_i64/zn_twice_i64` 保存半 tick 精度，强弱、A/B 严格越界和三点严格单调
楔形全部使用该整数口径。前三点直到中枢确认才可见，后续延伸也按两条构件成组确认；每个中枢
最多九点。主图绘制 Z 中轴和 Zn 连线，越界/楔形使用独立标记，对象树明确显示“辅助、不确认
三类点”。标准 B3/S3 仍只来自结构模块。

`aux_daily_30m_classification` 是 `ALG-AUX-005` 的固定日盘经验分类器。它只接受
`Asia/Shanghai + trading_day + bar_end + 30m`，并要求每个交易日严格包含
`10:00/10:30/11:00/11:30/13:30/14:00/14:30/15:00` 八根 K 线；不聚合低周期，缺根、夜盘、
额外 K 线或会话模板变化均拒绝当日分类。相邻三根 K 线的闭区间价格交集形成“日内重叠区”，
第二重叠区还必须与第一重叠区严格不相交并保留至少一根单边分隔 K 线。最终一/双/无重叠区分类
只在第八根收盘可知，使用 `heuristic/HEURISTIC` 命名空间，不创建标准中枢或买卖点。主图按
时间/定点价格锚点绘制最多两个重叠区并显示最终标签，对象树保留强弱子类及非交易解释；正式 run
不产生状态、阶段、交易信号、订单或成交。

`aux_ma_sector_rotation` 是 `ALG-AUX-006` 的跨标的经验排名器。输入固定为显式复权的日线
数据集集合和点时成员表；公共请求只保存 universe、成员 revision、复权模式/revision、反弹
episode 起点/可用时间和成员有效区间，Go 再从 catalog 解析受 PathGuard 保护的实际文件引用。
Python 复用唯一 MA 注册表，默认按 `5/13/21/34/55/89/144/233` 判断从 episode 起已经严格
站上的连续均线前缀，等号仍是未攻克；最大周期未预热时不发布。板块均值只聚合同一日线时间戳
且当时已公开有效的成员，并保留 sum/count/coverage。轮动候选只由标准趋势顶背驰触发，候选的
趋势底背驰同样来自权威 Chan 事件；等级落后、容量门槛和排序均显式留档。它不产生交易信号。
主图只显示当前 dataset 的等级变化和来源轮动提示，其他标的价格锚点被过滤；全部板块均值作为
共享时间轴副图曲线展示。当前 5 分钟期货数据不满足输入前提，界面会提前拒绝。

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
  "action": "open_short",
  "quantity": 1,
  "price_i64": 2350,
  "reason_code": "RETEST_FAILED_BREAK_OPEN"
}
~~~

所有 reason_code 必须是稳定枚举，可读文本只用于显示。
`quantity` 是正整数手数；旧策略或旧缓存缺省该字段时，执行模型按 1 手解释。
单向净持仓模型中的 `close_*` 平仓信号始终平掉当前方向的全部持仓；需要残余仓位时必须
显式使用后述 `reduce_*`，不能用 `close_*` 的信号数量制造残余仓位。

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

执行模型接受 `open_long`、`open_short`、`close_long`、`close_short`、`add_long`、
`add_short`、`reduce_long` 和 `reduce_short`。`close_*` 始终全平对应方向，忽略信号中的
部分数量；`reduce_*` 必须给出不超过当前持仓的正整数数量并按 FIFO 入场批次生成已完成
交易，`add_*` 只允许增加当前同向持仓。手续费、滑点、保证金、已实现和未实现盈亏均按
实际数量计算，入场成本按被减批次比例分配；`side=long` 表示买入，`side=short` 表示卖出。
最后一根 K 线上的 next-open 信号以 `NO_NEXT_BAR` 拒绝，不会伪造成交。

不支持的交易所细则必须在结果中标为模型限制，不能默认为真实撮合。

### 7.3 输出

- strategy_states：每次状态变化。
- stage_signals：阶段信号全生命周期。
- trade_signals：交易信号与过滤原因。
- risk_decisions：统一风险覆盖层的批准、降仓、阻断与熔断决定。
- orders：订单意图与状态。
- fills：模拟成交。
- trades：配对交易。
- positions：持仓快照。
- equity：权益、可用资金、保证金、回撤。
- summary：聚合指标。

### 7.4 统一风险与执行覆盖层

`unified_risk_execution_overlay` 是策略与订单执行器之间唯一的 `risk_filter`。正式回测和参数
Study 可附带同一 `risk_overlay`；AlgorithmRef、规范化参数和 `RiskContext` 共同进入签名与
manifest。旧 API 调用可省略它以保持兼容，但当前 Vue 默认启用。

风险参数使用整数 ppm 或定点 money-i64，不用浮点 epsilon。覆盖层在拟执行 K 线上读取当时
已知的组合权益、持仓、成交量和市场 observation；`available_at_bar_index` 不得晚于
`effective_from_bar_index`，执行时点不得早于策略信号的 `known_at_bar_index`。未处理合法分支和
开仓信号年龄只约束开仓/加仓；数据 revision 不一致或无可用成交量会阻断当次执行。停牌和涨跌停
按订单方向判断；开仓重试受最大信号年龄与回测终点限制，平仓/减仓则持续重试到回测终点。

持仓、板块、事件风险、单笔压力损失、成交量参与率和无杠杆限制只会保持或减少请求手数。
默认禁止杠杆，任何放开都要求独立 approval id。revision 变化、陈旧/缺口超限、日内亏损或
策略回撤触及阈值会激活本 run 内不可逆熔断；熔断阻止新增风险，但不阻止数据事实兼容且可执行的
平仓和减仓。全平意图受流动性限制时，已获批部分当根减仓，剩余退出继续调度；开仓或加仓被
减量时不自动补足。
每次决定写入 `risk_decisions.parquet` 和正式日志，并作为带 `known_at_bar_index` 的
`risk_decision` 因果事件进入对象树与主图。当前组合状态仍是单标的、单向净持仓，板块上限只
约束本标的暴露，不宣称已经具备多标的相关性或对冲模型。

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
