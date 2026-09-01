# 三买三卖中枢迁移·MACD 方向确认

## 身份与出处

- 运行 ID：`third_point_migration_macd_regime`，版本 `1.0.0`。
- 组合目录：`ALG-STR-003` + `ALG-AUX-002`。
- 结构出处：[20](https://chanlun108.cn/chanzhongshuochan108ke/20.html)、[21](https://chanlun108.cn/chanzhongshuochan108ke/21.html)、[49](https://chanlun108.cn/chanzhongshuochan108ke/49.html)、[79](https://chanlun108.cn/chanzhongshuochan108ke/79.html)、[86](https://chanlun108.cn/chanzhongshuochan108ke/86.html)、[107](https://chanlun108.cn/chanzhongshuochan108ke/107.html)；MACD 证据出处：[24](https://chanlun108.cn/chanzhongshuochan108ke/24.html)、[25](https://chanlun108.cn/chanzhongshuochan108ke/25.html)、[50](https://chanlun108.cn/chanzhongshuochan108ke/50.html)、[103](https://chanlun108.cn/chanzhongshuochan108ke/103.html)。

这是本工程新增的组合研究策略，不是 108 课原文中单独命名的一套策略。它保持 `third_buy_centre_migration_hold` 的结构和退出语义，只增加入场门控。

## 当前算法

1. 缠论结构引擎独立确认标准 B3/S3；闭区间边界仍为 B3 `low >= ZG`、S3 `high <= ZD`。
2. 用权威 MACD 实现计算 DIFF/DEA，不复制指标公式。观察周期由 `minimum_timeframe_minutes` 固定，并必须等于数据集 K 线周期。
3. B3 确认时，DIFF 和 DEA 必须同时严格高于正零轴缓冲，且连续达到 `reclaim_confirm_bars`，才开多。
4. S3 确认时，两线必须同时严格低于负缓冲，且连续达到 `risk_off_confirm_bars`，才开空。
5. 等于边界、只有一条线越界、MACD 尚在预热或连续根数不足时，消费该结构机会并发布 `*_MACD_*_NOT_CONFIRMED` 过滤状态，不下单。MACD 不会等待未来某根再补开原来的三类点。
6. 入场后退出规则与原迁移持有一致：新同级中枢或相反标准一类点确认时退出；MACD 反向本身不平仓。

## 参数

- 结构：`checkpoint_interval`。
- MACD：`fast_period`、`slow_period`、`signal_period`，且快线周期必须小于慢线周期。
- 边界：`zero_axis_buffer_ticks`，使用数据集 tick 换算定点价格。
- 连续确认：多头使用 `reclaim_confirm_bars`，空头使用 `risk_off_confirm_bars`。
- 周期锁定：`minimum_timeframe_minutes` 必须与 `Nm/Nh/Nd` 数据周期完全一致；它只是辅助观察周期，不定义结构级别。

## 可能命中的条件

只有标准 B3 与多头 MACD 区域同在一个确认时点，或标准 S3 与空头区域同在一个确认时点时命中。它倾向保留“结构突破方向已获得中期动量支持”的三类点，过滤回试虽守住但动量尚未转向的机会。

更可能改善结果的场景是原基准亏损主要来自逆动量 B3/S3；更可能恶化的场景是三类点常在 MACD 过零前率先启动，过滤会错过早期高收益交易。由于原基准多空归因强烈不对称，必须分别统计 B3/S3 保留率。

## 优点

- 保持结构信号为唯一触发源，没有把 MACD 金叉/过零冒充买卖点。
- 与原迁移持有只有一个变量差异，适合严格 A/B 对比。
- 边界、连续根数、指标值和过滤原因全部进入因果对象。

## 缺点与风险

- 过滤会进一步减少只有 41 笔的基准样本，可能让统计更不稳定。
- 零轴方向可能滞后于 B3/S3，尤其在反转初期漏掉有效信号。
- MACD 周期与缓冲高度可调，若在同一数据上反复挑参会产生过拟合。
- MACD 只过滤入场、不管理退出，不能被理解为完整的 MACD 交易系统。

## 当前证据

2026-09-01 使用与 13 策略基准相同的 AOL9 数据、区间、资金、成本和风险设置补跑，得到 20 笔、总收益 +1.9320%、胜率 50%、最大回撤 0.4095%、Sharpe 1.2955、利润因子 4.5255、每笔期望 96,600。对照基础迁移持有的 41 笔、+2.0934%、最大回撤 0.9180%、Sharpe 1.1665、利润因子 2.5973，过滤版牺牲少量总收益和一半样本，换来更低回撤与更高交易质量。

方向上仍不均衡：4 笔 B3 多头全亏、净亏 238,400；16 笔 S3 空头胜率 62.5%、净赚 2,170,400。这说明改善仍可能来自 AOL9 样本期的空头结构偏置，现阶段可列为“继续研究候选”，不能列为可靠策略。正式结果位于 `trading-data/comparisons/comparison-20260901-macd-composites-aol9`。自动化测试另覆盖多空正例、方向不一致过滤、缓冲等号、连续确认和前缀不变性。
