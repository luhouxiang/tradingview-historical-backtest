# 单周期缠论语义 profile

里程碑 14A 固定两个显式 profile。生产与回测只启用 `chan108_single_scope_v1`；第 54 课图例对应的严格触边解释只保留为 `chan108_lesson54_strict_touch_research_v1`，必须另起有版本的研究对照，不能由缺省参数选中。

权威机器可读定义见 [`chan-single-scope-profiles/profiles.json`](chan-single-scope-profiles/profiles.json)，边界反例见 [`counterexamples.json`](chan-single-scope-profiles/counterexamples.json)。

当前 profile 作出三项决定：

1. 三类点沿用 C-041 和第 20 课闭边界：B3 回试低点 `>= ZG`，S3 回试高点 `<= ZD`；只消费离开后的第一次已完成回试。
2. 线段实际区间取线段 `start_index..end_index`（均含）全部组成笔实际区间的并集；不包含相邻线段成员。结构端点继续用于画线，不能被区间极值替换。
3. 结构端点、结构确认、事件可知和成交分别计时，满足 `endpoint <= confirmed <= known < default execution`；极值来源 K 与结构端点分开保存。

缺口强弱预测、分型综合评分、均线受压确认期限仍无唯一原文公式。它们在 profile 中明确为 `enabled=false`，任何目录、信号或订单代码都不得通过缺省值让这些规则生效。
