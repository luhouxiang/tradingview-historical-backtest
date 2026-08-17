# 测试用例索引

| ID | 类型 | 核心期望 | 依据 |
|---|---|---|---|
| `L62_F1_TOP_FRACTAL` | 分型 | 输出顶分型，价格取中间K高点 | 62图1 |
| `L62_F2_BOTTOM_FRACTAL` | 分型 | 输出底分型，价格取中间K低点 | 62图2 |
| `L62_F3_SHARED_K_INVALID_BI` | 成笔拒绝 | 端点分型共用K线，不成笔 | 62图3 |
| `L62_F4_NO_FREE_K_INVALID_BI` | 成笔拒绝 | 无独立K线，不成笔 | 62图4、77课 |
| `L62_F5_MINIMAL_VALID_DOWN_BI` | 成笔 | 最小有效向下笔 | 62图5 |
| `L62_F5_MINIMAL_VALID_UP_BI` | 成笔 | 图5的方向镜像 | 62图5 |
| `L62_F6_UP_INCLUSION` | 包含 | 向上取较高高低点 | 62图6、65课 |
| `L62_F6_DOWN_INCLUSION` | 包含 | 向下取较低高低点 | 62图6、65课 |
| `L62_F7_THREE_K_CLASSIFICATION` | 分型 | 上升/顶/下降/底四分类 | 62图7 |
| `L77_KEEP_LATER_HIGHER_TOP` | 同类取舍 | 后顶更高，淘汰前顶 | 77课 |
| `L77_KEEP_LATER_LOWER_BOTTOM` | 同类取舍 | 后底更低，淘汰前底 | 77课 |
| `L77_EQUAL_TOPS_KEEP_FIRST_ON_OPPOSITE` | 同类取舍 | 同价顶在反向底出现后取先顶 | 77课 |
| `L69_PROVISIONAL_ENDPOINT_REVISION` | 当下状态 | 未完成图形的候选端点可修订 | 69课及月线图 |
| `L79_TOP_FRACTAL_NOT_NECESSARILY_BI` | 当下状态 | 有顶分型不等于已有向下笔 | 79课 |
| `L81_EXACT_TICK_COMPARISON` | 精度 | 0.08差异保留为8个最小跳动 | 81课 |
| `L91_STATE_TRANSITION_MATRIX` | 状态机 | 只允许原文列出的四状态转移 | 91课 |
| `L78_SEGMENT_RULE_MUST_NOT_RELAX_BI` | 模块边界 | 段的极值例外不得放宽笔 | 78课 |
| `L83_BI_CANNOT_BUILD_CENTER` | 模块边界 | 笔数组不能直接组成最低中枢 | 83课 |

视觉金样另见 `docs/visual_oracles.md`，其中 V01—V09 与上述自动化用例对应，V10 专门测试笔/线段模块隔离。

