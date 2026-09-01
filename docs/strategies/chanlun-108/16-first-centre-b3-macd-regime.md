# 首中枢三买三卖轮动·MACD 方向确认

## 身份与出处

- 运行 ID：`first_centre_B3_macd_regime`，版本 `1.0.0`。
- 组合目录：`ALG-STR-003` + `ALG-AUX-002`。
- 结构课程：[20](https://chanlun108.cn/chanzhongshuochan108ke/20.html)、[21](https://chanlun108.cn/chanzhongshuochan108ke/21.html)、[49](https://chanlun108.cn/chanzhongshuochan108ke/49.html)、[79](https://chanlun108.cn/chanzhongshuochan108ke/79.html)、[86](https://chanlun108.cn/chanzhongshuochan108ke/86.html)、[107](https://chanlun108.cn/chanzhongshuochan108ke/107.html)；MACD 课程：[24](https://chanlun108.cn/chanzhongshuochan108ke/24.html)、[25](https://chanlun108.cn/chanzhongshuochan108ke/25.html)、[50](https://chanlun108.cn/chanzhongshuochan108ke/50.html)、[103](https://chanlun108.cn/chanzhongshuochan108ke/103.html)。

这是 `first_centre_B3_rotation` 的独立 MACD 过滤变体，用于检验“首中枢优先”和“零轴方向支持”叠加后是否比各自单独使用更稳健。

## 当前算法

1. 一个方向周期只消费首个已确认标准 B3/S3，后续同向三类点仍按 `LATER_CENTRE_THIRD_POINT_FILTERED` 排除。
2. 首个 B3 还必须满足 DIFF/DEA 连续严格站在正缓冲上方；首个 S3 必须连续严格位于负缓冲下方。
3. 如果首个结构点没有通过 MACD，它仍被视为该方向周期已消费，不会等待 MACD 后来转正/转负，也不会改用第二个同向三类点补开。这保证没有事后挑选。
4. 相反严格三类点会重置前一方向周期，再按相反方向的首点和 MACD 条件判断。
5. 入场后沿用首中枢轮动退出：新同级中枢、相反标准趋势背驰或相反盘整背驰确认时平仓；MACD 自身不触发退出。
6. 所有 MACD 参数、严格边界和周期锁定规则与迁移 MACD 版本相同。

## 可能命中的条件

必须同时出现“方向周期首个标准三类点”和“同方向 MACD 连续确认”。它最适合第一中枢突破发生在动量已经越过零轴并保持的趋势启动；若优质首点往往领先 MACD 过零，则策略会完全错过该方向周期。

## 优点

- 同时限制趋势阶段和动量方向，预期交易数最少、换手最低。
- 未通过 MACD 的首点不会在未来补单，因果语义严格。
- 可与 10 笔基准首中枢策略逐笔对照，定位被过滤交易的贡献。

## 缺点与风险

- 原策略只有 10 笔闭合交易，再过滤后极可能低于任何有效统计门槛。
- 首点被过滤后整个方向周期没有第二次机会，机会成本高。
- 首中枢定义仍是单级别工程周期，不等于完整递归走势的绝对第一中枢。
- 方向确认和首点规则可能高度相关，叠加不一定提供独立信息。

## 当前证据

2026-09-01 的同口径 AOL9 补跑只剩 5 笔，总收益 -0.0910%、胜率 20%、最大回撤 0.2652%、Sharpe -0.2749、利润因子 0.4596。基础首中枢策略是 10 笔、+0.1420%、最大回撤 0.4226%、利润因子 1.6223；过滤虽降低回撤，却把低样本正收益变成负收益，当前不具备候选价值，只保留为比较和检验组合假设的负对照。

自动化测试仍证明其因果实现正确：B3/S3 与 MACD 同向才交易、边界等号不交易、被过滤首点不会未来补单，并保持前缀不变。正式结果位于 `trading-data/comparisons/comparison-20260901-macd-composites-aol9`。
