# 趋势背驰双向反转

## 身份与出处

- 运行 ID：`trend_divergence_reversal`，版本 `1.1.0`。
- 理论母项同为 `ALG-SIG-001`。
- 课程依据与仅多头版本相同：[15](https://chanlun108.cn/chanzhongshuochan108ke/15.html)、[17](https://chanlun108.cn/chanzhongshuochan108ke/17.html)、[21](https://chanlun108.cn/chanzhongshuochan108ke/21.html)、[24](https://chanlun108.cn/chanzhongshuochan108ke/24.html)、[27](https://chanlun108.cn/chanzhongshuochan108ke/27.html)、[37](https://chanlun108.cn/chanzhongshuochan108ke/37.html)、[43](https://chanlun108.cn/chanzhongshuochan108ke/43.html)、[50](https://chanlun108.cn/chanzhongshuochan108ke/50.html)。

## 当前算法

标准 B1/S1 的结构生成流程与“下跌趋势一买反转”完全相同。差别在执行状态机：

1. 空仓遇已确认标准 B1 开多，遇已确认标准 S1 开空。
2. 多仓遇 S1 时先平多，再由同一个确认事件进入空头；空仓/空仓转换逻辑保持单向净持仓。
3. 空仓遇 B1 时镜像反转为多头。
4. 盘整背驰类一买卖、候选信号、未完成末段和 MACD 单独交叉全部过滤。

## 可能命中的条件

程序命中要求同级趋势在两端分别形成标准背驰和确认转折。较有利的是波段清晰、上下趋势交替、反转后有足够延续的市场；较差的是长期单边无反向标准点、窄幅盘整，或结构确认延迟大于后继可交易空间。

## 优点

- 多空镜像，可以利用趋势两端，不依赖人为方向判断。
- B1/S1 都来自同一权威结构算法，避免两套不一致的多空规则。
- 反转事件、来源对象和确认时点完整留档。

## 缺点与风险

- 标准趋势背驰稀缺，样本量可能不足以估计胜率和尾部风险。
- 反手会产生双倍换手；在震荡边缘或成交不连续时成本敏感。
- 退出依赖相反标准一类点，缺少独立保护性退出时可长期持仓。
- 当前实现是已确认层级对象的策略消费，不是跨所有级别递归寻找最优反转点。

## 当前证据

最近 AOL9 结果与仅多头版本相同：1 次开多成交、0 个闭合回合，期末总收益 -1.1863%。这表示样本期内没有后续标准 S1 完成反手，不能据此比较双向设计是否优于仅多头设计。

