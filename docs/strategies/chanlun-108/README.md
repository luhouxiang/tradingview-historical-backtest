# 缠论 108 课策略说明索引

## 口径

本目录描述 2026-08-30 完成全量对比的 13 个策略，以及本次新增的 2 个“结构信号 + MACD 方向确认”组合策略。课程出处来自仓库内的 108 课算法集；链接统一指向 `https://chanlun108.cn/chanzhongshuochan108ke/{课次}.html`。

“课程原义”与“工程实现”必须分开理解：13 个运行项是 9 个目录算法的可执行实现或方向/持有变体，并不是原文逐字给出的 13 套成品交易系统。标准中枢、走势、背驰和买卖点只由缠论结构流水线生成；MA、MACD、BOLL、Zn 都不能独立制造标准买卖点。

2026-09-05 的 108 课复核后，单周期研究范围内的 14 套程序已按唯一入场、持有、失败退出、来源修订和不适用场景统一校准，见 [14 套单周期程序校准表](17-single-scope-program-alignment.md)。该表采用缠论 15.0.0 的线段实际区间和信号证据字段；旧结果继续只读。

最近一次 13 策略对比使用 `SHFE.AOL9.5m`、71,149 根 K 线、776 个交易日，信号在收盘确认、下一根开盘成交，含每手 3 元手续费和 1 tick 滑点。结果只能说明这一数据、区间和参数下的历史表现，不能外推为实盘收益保证。“交易数”是已闭合交易数；0 笔但收益非零表示期末仍有未平仓持仓及其浮动盈亏/成本。

## 现有 13 个策略

| # | 运行策略 | 理论母项 | 最近结果 | 说明 |
|---|---|---|---|---|
| 1 | [固定级别中枢决策树](01-fixed-level-centre-decision-tree.md) | ALG-STR-001 | 0 笔，0.0000% | 五状态框架 |
| 2 | [下跌趋势一买反转](02-downtrend-reversal-only.md) | ALG-SIG-001 | 0 笔闭合，-1.1863% | 仅多头 B1 |
| 3 | [趋势背驰双向反转](03-trend-divergence-reversal.md) | ALG-SIG-001 | 0 笔闭合，-1.1863% | B1/S1 双向 |
| 4 | [盘整背驰中枢回归](04-consolidation-divergence-centre-reversion.md) | ALG-SIG-002 | 59 笔，-1.8357% | 均值回归与三类点转换 |
| 5 | [三买三卖中枢迁移持有](05-third-point-centre-migration-hold.md) | ALG-STR-003 | 41 笔，+2.0934% | 当前 Pareto 候选 |
| 6 | [首中枢三买三卖轮动](06-first-centre-b3-rotation.md) | ALG-STR-003 | 10 笔，+0.1420% | 盈利但样本不足 |
| 7 | [只做第二类买点](07-second-buy-only.md) | ALG-STR-002 | 0 笔，0.0000% | 严格 B2 稀缺 |
| 8 | [只做第三类买点](08-third-buy-only.md) | ALG-STR-003 | 13 笔，-0.1598% | 仅多头 B3 |
| 9 | [中枢震荡差价](09-centre-oscillation-spread.md) | ALG-STR-004 | 50 笔，-0.9620% | 成本敏感 |
| 10 | [同级别分解机械程序](10-same-level-decomposition-program.md) | ALG-STR-005 | 5 笔，-0.0330% | 首次提升前样本少 |
| 11 | [三层级完全分类](11-three-level-complete-classification.md) | ALG-STR-007 | 38 笔，-1.8568% | 固定对象图投影 |
| 12 | [目标级别反弹/回调分段操作](12-target-level-rebound-segmented-operation.md) | ALG-STR-008 | 1 笔，-0.0046% | 标准 B1/S1 稀缺 |
| 13 | [底部/顶部构造状态机](13-bottom-top-construction.md) | ALG-STR-009 | 0 笔闭合，-1.1863% | 粗略层只观察 |

## MACD 与辅助策略复核

- [MACD 与均线辅助项为何未形成交易策略](14-macd-and-auxiliary-review.md)
- [三买三卖中枢迁移·MACD 方向确认](15-third-point-migration-macd-regime.md)
- [首中枢三买三卖轮动·MACD 方向确认](16-first-centre-b3-macd-regime.md)

新增两项保持原 13 项不变，作为独立的第 14、15 个可比较策略。2026-09-01 已在相同 AOL9 数据、资金、成本和风险口径下补跑：迁移 MACD 版 20 笔、+1.9320%、最大回撤 0.4095%、Sharpe 1.2955、利润因子 4.5255；首中枢 MACD 版 5 笔、-0.0910%。前者相对基础版少 21 笔且总收益略低，但回撤和单笔质量改善，值得跨数据验证；后者目前只是低样本负对照。结果保存在 `trading-data/comparisons/comparison-20260901-macd-composites-aol9`。
