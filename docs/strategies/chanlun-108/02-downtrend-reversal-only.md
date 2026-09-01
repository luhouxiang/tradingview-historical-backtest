# 下跌趋势一买反转

## 身份与出处

- 运行 ID：`downtrend_reversal_only`，版本 `1.1.0`。
- 理论母项：`ALG-SIG-001`“趋势背驰与第一类买卖点”的仅多头策略变体。
- 出处：[第15课](https://chanlun108.cn/chanzhongshuochan108ke/15.html)、[第17课](https://chanlun108.cn/chanzhongshuochan108ke/17.html)、[第21课](https://chanlun108.cn/chanzhongshuochan108ke/21.html)、[第24课](https://chanlun108.cn/chanzhongshuochan108ke/24.html)、[第27课](https://chanlun108.cn/chanzhongshuochan108ke/27.html)、[第37课](https://chanlun108.cn/chanzhongshuochan108ke/37.html)、[第43课](https://chanlun108.cn/chanzhongshuochan108ke/43.html)、[第50课](https://chanlun108.cn/chanzhongshuochan108ke/50.html)。核心是“没有趋势就没有标准趋势背驰”，先确定 `a+A+b+B+c` 和可比离开段，再比较力度。

## 当前算法

1. 缠论引擎先确认至少两个同级、完整包络严格不重叠的中枢，末段完成并创新低。
2. 结构固定后比较同向 MACD 面积；末段力度严格减弱时产生 B1 候选。MACD 不能单独产生 B1。
3. 等待其后首条反向低级别线段确认转折；只有 `confirmed=true`、`signal_class=standard` 的 `buy_1` 才进入策略。
4. 空仓遇标准 B1 开多；持仓遇标准 S1 平多。标准 S1 在空仓时被忽略，因此本策略永不主动做空。
5. `class_buy_1/class_sell_1` 盘整背驰、未确认候选和失效 revision 全部不交易。

## 可能命中的条件

信号层命中要求真实下跌趋势、末端创新低、力度收缩和反向完成段四项同时存在。较有利的市场形态是持续下跌后动能衰减，B1 确认后形成足够大的同级反弹；不利形态是强趋势连续延伸、V 型急转造成确认后追高，或 B1 后仅有很小反弹。

## 优点

- 只做下跌趋势后的标准一买，方向和风险暴露简单。
- 明确排除盘整背驰冒充趋势背驰，信号语义严格。
- 候选、确认、执行时点分离，具备前缀不变性和审计链。

## 缺点与风险

- 条件极严格，标准 B1 可能多年只出现很少几次。
- 反转确认天然滞后；趋势延伸或跳空时仍可能形成大幅浮亏。
- 只有标准 S1 才平仓，缺少独立止损/时间退出时可能长期留仓。
- 单方向策略无法利用标准 S1 后的下跌段。

## 当前证据

最近 AOL9 全量结果产生 1 次开多请求和成交，但没有闭合交易；期末按市值计价后总收益 -1.1863%，手续费 300、滑点成本 2,000（均为定点金额）。“0 笔”在这里是 0 个已闭合回合，不是从未入场。

