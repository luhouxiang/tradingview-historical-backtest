# 背驰独立力度与走势级别证据：Codex 实施说明

版本：1.0；整理日期：2026-10-01（Asia/Singapore）。

目标仓库：https://github.com/luhouxiang/tradingview-historical-backtest

讨论中核对的代码基线：`36cddeffd967973009d386d7395cd4225fccd379`，算法版本 `19.3.2`。

文档性质：本次讨论的整理和实施建议。没有因为导出本文件而修改仓库、运行新回测或验证新模型。实际实施时必须核对当前 HEAD；不能把基线观察当作最新代码事实，也不能把建议字段当作已经存在的接口。

## 0. 给 Codex 的使用说明

先读目标仓库的 `AGENTS.md`、当前路线图、相关 contracts 和本文件。先报告当前代码相对本基线的变化，然后按照路线图逐个里程碑实现。

首次实施仅执行 M1：拆分结构候选与力度接口，完整封装旧版规则，建立旧版行为等价验证。M2、M3、M4 和递归级别证明是后续独立任务。

本文件不授权自动切换默认策略、不授权发布或推送仓库、不授权把工程模型改名为原文完整定义。不需要为了普通可逆实现步骤反复要求确认；按用户实际任务与仓库规则推进。

## 1. 用户目标与讨论结论

用户希望：

1. 再次核对缠论 108 课关于背驰的原始定义，明确 MACD 是辅助。
2. 对照本仓库的背驰实现，说明与原文的差距。
3. 判断能否去掉 MACD，依靠价格、时间和结构给出背驰判断。
4. 现阶段优先补齐独立的力度判定和走势级别证明。
5. 得到可供 Codex 分阶段实施、复核的方案。

讨论结论：

- MACD 不是背驰定义本身。取消它的硬门槛，必须补上明确的独立力度口径。
- 结构形态存在、价格创新高/低、中枢迁移，不能单独证明力度减弱。
- 力度减弱和级别资格是两类不同证据。
- BI、SEGMENT 是工程对象层；不能直接当作已证明的走势级别。
- c 内有两个局部中枢，并不能单靠数量证明完整的次级别走势关系。
- c 内小级别背驰，不等于父级 b/c 已经背驰。
- 价格—时间模型可以作为不依赖指标的工程模型，但不是已证明与 108 课全部等价的定义。

建议判定关系：

```text
可比较结构成立
AND 主力度模型判定减弱
AND 所要求的级别证据成立
→ 按所声明 decision_profile 输出背驰结论
```

每个结论都必须同时标明力度模型、级别证明范围和因果可用时间。

## 2. 原文依据与适用边界

以下页面为作者原文的整理存档，并非作者当前维护的官方网站。实施时应检查页面中的原始出处；若内容与其他原文存档冲突，记录差异后核对，不采用二手算法总结替代原文。

### 2.1 第 15 课：均线面积与平均力度

来源：https://eczsc.com/originals/15

原文在均线“吻”的语境中定义趋势力度：以前一吻结束和后一吻开始之间两条均线形成的面积衡量；前后两个同向运动力度减弱形成背驰。运行中的平均力度用截至当下的面积除以时间观察。MACD 等技术指标可作为辅助。

工程注意：

- “均线吻区间”与“结构段 b/c 区间”不是天然同一窗口。
- 把 MA 面积截在 b/c 中可以形成另外的工程模型，但必须单独命名。
- 完成区间和运行中区间需要分别输出状态；运行中减弱可能随后消失。
- 第 15 课早期均线语境与后续中枢、走势类型的级别语境需要一起理解，不能抽一句公式覆盖后续全部定义。

### 2.2 第 37 课：趋势背驰的级别资格

来源：https://eczsc.com/originals/37

围绕 `a + A + b + B + c` 的关键要求：

- 该整体必须是趋势，A、B 必须为同级别中枢。
- c 需要满足次级别要求，包含对 B 的第三类买卖点。
- b 的级别不能大于 c；不应把 b、c 必须完全同级写成普遍原文条件。
- 上涨时 c 创新高，下跌时 c 创新低。
- 文中讨论 c 内至少两个中枢；这两个中枢构成次级别趋势，是最标准、最常见的情况。

不能扩写为：所有合法 c 的两个内部中枢都必须满足某一唯一分离模式。尚未实现的分支应输出未知原因。

### 2.3 第 43 课：内部背驰与父级背驰

来源：https://eczsc.com/originals/43

原文明确区分：背驰级别等于当前走势级别，以及背驰级别小于当前走势级别。c 内小级别转折可能发生，而父级 c 对 b 并未背驰；后续还可能形成中枢后向原方向继续。

工程要求：

- 子级背驰可用于内部定位，不能代替父级力度比较。
- 不用后续价格结果回填当时的背驰判断。
- 不把一次小级别背驰直接解释为必然的大级别反转。

### 2.4 结构映射与讨论中的简化

```text
趋势背驰：a + A + b + B + c，重点比较同方向 b、c。
盘整背驰：a + B + c，重点比较同方向 a、c。
```

将 a/b/c 映射为线段、A/B 映射为线段中枢，方便工程实现，但须标记映射范围。中枢的形成方向、相对于前一中枢的迁移方向、离开方向分别记录，不合并成一个含糊的“中枢方向”。

中枢内部震荡比较与中枢外部 `a+B+c` 比较分别处理。它们不应因为都比较同向运动而使用同一信号枚举。

## 3. 已核对的仓库现状

以下路径均以基线提交为准。统一链接前缀：

https://github.com/luhouxiang/tradingview-historical-backtest/blob/36cddeffd967973009d386d7395cd4225fccd379/

### 3.1 力度判断

文件：`python/src/tvbt/chan/signals.py`

`_divergence()`：

- 比较参考段与当前段方向。
- 调用 `_macd_area()`，对上涨累计正柱，对下跌累计负柱绝对值。
- 必需样本缺失、参考/当前面积非正、当前面积不小于参考面积，返回 `None`。
- 新极值条件根据调用参数检查。
- DIFF/DEA 同向极值关系是审计字段，不是当前硬门槛。

因此当前 MACD 同方向柱面积收缩仍是生成背驰的必要条件，而不只是辅助展示。

`_center_baseline_span_contracts()`：

- 上涨比较实际高点减基准，下跌比较基准减实际低点。
- 当前跨度必须为正且严格小于参考跨度。
- 趋势调用中上涨基准为中枢 ZD，下跌基准为中枢 ZG。
- 它是项目额外门槛，代码明确没有宣称这是第 24 课的必选公式。

`_trend_legs_advance()`：检查 a/b/c 起点价格在趋势方向上严格推进。保留为旧 profile 的工程条件，不自动宣称为原文全部情形的必要条件。

### 3.2 当前趋势扫描

`chan_divergences()` 的趋势分支大致检查：

1. 相邻两个中枢均已离开，连接索引关系成立。
2. 中枢核心和比较外围满足严格迁移。
3. a 存在，a/b/c 同向，起点推进成立。
4. 中枢基准跨度收缩。
5. c 对此前整体包络创新高/低。
6. MACD 面积收缩。
7. 检查 c 内两个 BI 中枢与首次离开/回抽证据。

满足现有内部证据时输出 `standard_trend`；不足时输出 `segment_trend_candidate`。这里的 standard 是现有工程 profile，不能重新解释为已经具有完整递归原文证明。

形成中 c 使用 provisional 观察；可能撤销，不可当作已完成走势。

### 3.3 当前 c 次级别证据

`BiCenterEvidence` 当前只有：

```text
object_id / start_bar_index / end_bar_index / known_at_bar_index
```

`_trend_c_sublevel_proof()`：

- 筛选主体完全位于 c 内、判定时已知的 BI 中枢。
- 构造时间顺序链，允许共享边界，取前两个。
- 找首次同向 BI 跨出 B 的边界。
- 找紧邻的首次反向 BI，检查完整实际范围不回到边界内。
- 首回失败立即停止，不允许用后面一次成功回抽替换。

该方法证明的是明确的局部投影条件。它没有证明内部中枢构造层级、完整走势类型、A/B 递归同级、b 不高于 c 等完整关系。

### 3.4 可复用的权威结构与均线力度

`python/src/tvbt/chan/local_center.py::LocalCenter` 已有：

```text
unit_kind / structural_level / seed_ids / zd_tick / zg_tick
formed_at / body_start / body_end / observed_low / observed_high
exit_id / first_retest_id / break_confirmed_at / source_revision
left_context_incomplete 等
```

应从该对象投影证据，不复制中枢计算逻辑。

`python/src/tvbt/chan/engine.py::_bi_sublevel_centers()` 目前只暴露 CLOSED、具有主体边界和 break_confirmed_at 的 BI 中枢，再压缩为上述简化证据。

`python/src/tvbt/auxiliary/ma_kiss.py::measure_ma_force()` 已实现右端点矩形累加：

```text
area = sum(abs(short_ma[i] - long_ma[i]))，i ∈ (start, end]
duration = end_position - start_position
average = area / duration
```

当前跳过缺失均线值，但 duration 仍使用整个窗口。新严格力度 profile 需拒绝缺样本窗口，不能因此制造减弱；旧 profile 行为保留。

吻分类已输出 `aux_ma_force_completed`、前一可比事件、面积比和平均力度比，默认辅助用途，不生成标准订单。

### 3.5 工程边界

`docs/19-chan-108-single-scope-plan.md` 当前范围：一个数据修订、一个 K 线周期；不加入多级别递归、多 K 周期和区间套。路线图历史上已有高层递归链删除记录，不能假设旧模块仍是有效生产链。

`python/src/tvbt/chan/algorithm.py::_source_hash()` 显式列出源码文件。新增模块及共享测量源码必须加入有效身份计算，不能留下“代码已变、旧缓存仍可复用”的缺口。

Go 负责文件、任务与 API；Python 负责权威计算；Vue 负责投影和展示。保持现有架构，不在 Go/Vue 复制力度或级别算法。

## 4. 目标架构

```python
context = build_divergence_context(structures, as_of)
strength = compare_strength(context, strength_profile, observations)
level_proof = verify_level(context, level_profile, evidence)
decision = decide_divergence(context, strength, level_proof, decision_profile)
```

四层责任：

| 层 | 回答的问题 | 禁止事项 |
|---|---|---|
| 结构上下文 | 哪些运动可以进入这个结构比较？ | 不提前用 MACD 删除比较机会 |
| 力度证据 | 按这个口径是否减弱？ | 不输出买卖点，不混用窗口 |
| 级别证据 | 所要求的级别关系是否已证明？ | 不用数量或对象类型替代证明 |
| 决策 | 按这个声明的规则能输出什么结论？ | 不把 unknown 当 passed |

价格跨度收缩、起点推进等旧工程门槛按其性质显式放入旧 profile。通用结构候选不应先被待比较的力度条件过滤；M1 通过旧 profile 恢复所有旧过滤顺序与结果。

### 4.1 DivergenceContext

建议数据：

```text
context_id / revision
dataset_id / data_revision / symbol / timeframe
structure_profile / boundary_profile / range_profile
kind: trend | consolidation | center_oscillation
direction
a_id / b_id / c_id / A_id / B_id（按类型允许 null）
reference_movement_id / current_movement_id
dependency_ids_and_revisions
start/end anchors、actual low/high、比较窗口
new_extreme_satisfied
structural_condition_results
known_at
```

对象 ID 与 revision 是依赖身份；数组下标只能作为内部定位，不能成为唯一持久化身份。

结构候选属于研究/审计对象，不自动进入交易信号。候选类别与交易消费者的过滤规则需在 contracts 明确。

### 4.2 StrengthEvidence

```text
evidence_id / revision / context_id
strength_profile / parameter_hash / measurement_domain
reference_id_and_revision / current_id_and_revision
window_profile / time_unit / price_unit
reference_measurements / current_measurements
relation: weaker | stronger | equal | conflict | unknown
completion_status: forming | completed
comparability_status / reason_codes
known_at
```

不得复用已有含义不同的 strength 字段，除非先核对其 schema 和所有消费者。

未知与不减弱必须区分：缺少数据不是 stronger，级别无法证明不是 failed。

### 4.3 LevelProof

```text
proof_id / revision / context_id
level_profile
scope: local_projection | recursive_verified
status: passed | failed | unknown
requirements[]: requirement_id / status / evidence_ids / reason_codes
dependency_ids_and_revisions
known_at
```

scope 与 status 是两个维度。local_projection 的 passed 仅表示投影规则通过，不表示原文递归条件全部通过。

### 4.4 决策与交易资格

```text
结构不满足 → 不发布该类型背驰结论，保留筛选原因。
结构满足、力度不是 weaker → 保留比较证据，不发布主模型背驰。
力度 weaker、必需级别条件 unknown → 候选，列 unmet_requirements。
力度 weaker、级别条件 failed → 拒绝升级，列违反条件。
所选 decision_profile 所需条件全部 passed → 发布该 profile 的结论。
```

旧 decision_profile 保持旧输出和交易资格。新研究 profile 默认不改变生产交易。新 profile 如只具备局部投影，输出名称和展示明确使用工程映射含义；不得静默复用“已严格证明原文标准背驰”的文案。

MACD 可保留诊断字段，但在非 MACD 主模型中不参与 veto。子级背驰也不能替代父级力度条件。

## 5. 独立力度模型

### 5.1 Profile 目录

| 建议名称 | 主要语义 | 第一阶段定位 |
|---|---|---|
| legacy_19_3_2 | 原结构条件、价格跨度、MACD 面积及证明投影 | 默认兼容基准 |
| price_displacement_speed_v1 | 价格方向位移与每观察间隔速度 | M2 新研究模型 |
| ma_kiss_area_v1 | 吻结束至下一吻开始的均线面积 | 复用已有辅助对象 |
| segment_ma_spread_v1 | b/c 窗口内的均线面积 | 可选后续工程模型 |

名称可按仓库命名规范调整，但语义必须保持明确。一个运行只选一个主力度模型，其余作为并列诊断；不要把多个模型投票或加权合成没有依据的总分。

### 5.2 价格—时间力度：第一版建议

这不是 108 课原文的唯一公式，而是可检验的无指标工程假设。

对运动 m：

```text
s = +1（up），-1（down）
D(m) = s * (end_price_i64 - start_price_i64)
N(m) = end_raw_position - start_raw_position
V(m) = D(m) / N(m)
```

要求 D > 0、N > 0、观察窗口完整、方向一致、范围语义明确。

N 使用原始 K 线序列的观察间隔数，不使用墙钟分钟数，也不假设任意外部 bar_index 连续。周末和夜盘停盘不虚增时长。

比较参考 b 与当前 c：

```text
weaker:
    D_c <= D_b AND V_c <= V_b，并且至少一个严格小于
stronger:
    D_c >= D_b AND V_c >= V_b，并且至少一个严格大于
equal:
    D_c == D_b AND V_c == V_b
conflict:
    一个维度升、另一个维度降
unknown:
    必需数据/可比性/窗口条件不足
```

速度比较可用整数交叉乘积 `D_c * N_b` 与 `D_b * N_c`，避免浮点边界。注意 Python/Arrow/跨语言输出的整数范围；不得未经检查把大乘积压回 int64。

第一版不自动优化阈值、不引入任意加权分数。若未来需要容差，作为独立 versioned profile 规定 tick 容差与等号语义，不能悄悄改变 v1。

例子：b 位移 100、间隔 20、速度 5；c 位移 60、间隔 30、速度 2，判 weaker。若 c 位移 60、间隔 5、速度 12，则判 conflict。

价格位移 D 与中枢基准跨度 R 分别记录：

```text
R_up = actual_high - baseline
R_down = baseline - actual_low
```

旧 profile 的上涨 ZD/下跌 ZG 基准保持原样。不要把 R 改名为运动自身位移，也不要把实际极值替代结构端点而不声明口径。

可比性说明：第 37 课允许 b 级别小于 c，因此不能把“b/c 必须完全同级”加入所有 profile。新价格模型需要明确它比较的是同一父级结构角色下的运动窗口，并记录级别关系和模型覆盖范围；尚未支持的混合级别测量返回 unknown，不将工程限制宣称为原文禁止条件。

### 5.3 均线面积复用

```text
A = Σ abs(MA_short[i] - MA_long[i])，i ∈ (start, end]
N = end_position - start_position
F_avg = A / N
```

建议抽出不依赖辅助事件类的共享测量函数，保持依赖方向无环。不要让 chan 主引擎反向依赖整套吻事件分类器；可以调用共享底层测量模块。

严格 profile：任何必需均线样本缺失或非有限值 → unknown；零时长 → unknown；参考测量为零时不计算比值，不通过除零制造结论。

吻窗口使用既有因果吻确认时间。后一次吻开始位置可能早于它被确认的时间；证据 known_at 必须使用确认时间，不能回填到开始位置。

如增加 segment_ma_spread_v1，需单独规定区间、同向可比性、面积或平均面积哪个是主判断值。不能直接用“任意 MA 吻区间”代替 b/c，也不能把 running 平均力度与 completed 面积混为一个度量。

## 6. 局部级别证据：M3 的范围

第一轮仍在当前单周期对象体系内，补齐证据和未知状态，不冒充完整递归。

### 6.1 扩展中枢投影

从 LocalCenter 投影：

```text
center_id / source_revision / stream_key
unit_kind / structural_level（标签与证明分开）
seed_ids / body_member_ids_and_revisions
ZD / ZG / actual_low / actual_high
formed_at / closed_at / known_at
exit_id / first_retest_id
left_context_incomplete
```

不要只增加字段名而缺少成员来源。成员、实际范围和构造 profile 必须与权威中枢一致。

### 6.2 要求清单

| requirement | 验证内容 | 当前不足时 |
|---|---|---|
| same_source | 数据、修订、算法与构造来源一致 | unknown/failed 分原因 |
| A_B_same_layer | A/B 属于同一构造对象层 | 可验证局部层 |
| A_B_same_movement_level | A/B 具有同级别构造证明 | 没有递归证据则 unknown |
| c_internal_centers | 内部中枢有效、不同且时间顺序合法 | 不以 count 代替成员证据 |
| c_internal_relation | 分离、重叠、延伸、扩展的明确关系 | 未覆盖分支 unknown |
| c_type3_projection | 首离开、首回抽的局部边界证据 | 保留现有首次失败语义 |
| c_type3_movement | 离开和回抽达到所需走势级别 | BI 投影不能自动通过 |
| c_required_level | c 满足要求的完整走势级别 | unknown 或证据验证 |
| b_not_above_c | b 的级别不大于 c | 缺证据则 unknown |
| causal_dependencies | 依赖都在当前判定时可用 | 违规失败，不回填 |

在 M3 的输出中，哪些是局部可验证项、哪些是尚待递归证明项必须清楚展示。unknown 不是算法异常，也不应隐藏。

边界等号、实际范围、共同 seed/entry 等语义遵循现有声明的 boundary/range profile；本任务不顺便重写它们。

## 7. 后续完整相对级别证明：独立里程碑

进入此阶段前更新路线图，明确扩展当前“不加入递归”的范围。可以仍使用同一份 K 线，暂不增加多周期数据流。

### 7.1 MovementEvidence

```text
movement_id / revision / construction_profile
child_movement_ids_and_revisions
center_ids_and_revisions
direction / movement_kind
start/end / actual_range
level_lower_bound / level_upper_bound
completion_status
completion_rule / completion_evidence_ids
known_at
```

底层单位必须明确。例如以已确认 BI 或 SEGMENT 作为工程底座，应注明仅构造相对级别，不能直接命名为 1m/5m/30m 原文级别。

### 7.2 构造顺序

1. 固定底层单位、包含处理、范围与完成规则。
2. 用已完成且已知的下一层运动形成中枢，验证连续性和交集。
3. 处理延伸、离开、回抽、扩展及级别上移。
4. 用中枢与连接运动构造当前层走势。
5. 验证覆盖范围、成员连接及完成证据，再暴露给上层。

这里的“完成”不能简单定义为看见一个反向对象，也不能仅用三个单位或两个中枢计数替代。具体完成规则必须先有原文依据、工程映射说明和反例集；未完成的分支保持未知。

相对级别验证证明的是声明构造 profile 下的关系，不自动证明全部原文解释唯一。输出不得使用无边界的“严格缠论完全证明”文案。

### 7.3 级别上下界

若 b 的级别范围为 [L_b,U_b]、c 为 [L_c,U_c]：

```text
U_b <= L_c → 可证明 b 不高于 c
L_b > U_c  → 可证明违反 b 不高于 c
其他       → unknown
```

上下界比较仅在同一底座和构造规则下有效；不比较不同 profile 的裸整数。

A/B 同级需要相容且足够确定的构造证据，不能通过范围有交集就推定同级。

### 7.4 防止循环论证

禁止：父级背驰证明 c 完成 → c 完成证明父级级别 → 父级级别证明父级背驰。

采用证据依赖 DAG，检测直接/间接自依赖。候选和已证明对象分开；力证不足时不强行闭合。

对象修订发布当时已知的新证据 revision。历史信号和成交保持 as-of 事实，不因之后的结构调整回写。

## 8. 模块接入与持久化

| 文件/区域 | 改动建议 |
|---|---|
| 新增 chan/strength.py | 纯力度计算、可比性和比较结果 |
| 新增 chan/evidence.py | 上下文、局部级别证据、验证器 |
| chan/signals.py | 拆分筛选和决策，legacy 适配 |
| chan/engine.py | 提供 raw 窗口、revision 与权威证据，统一调度 |
| auxiliary/ma_kiss.py 与共享底层模块 | 复用 MA 测量，保留旧行为，增加严格 profile |
| chan/algorithm.py | profile schema、有效 source_hash、定义和输出注册 |
| chan/events.py / storage.py | 对象 revision、证据表/事件、原因列表 |
| chan/checkpoint.py | 参数身份、恢复与证据缓存一致性 |
| contracts / Go / Vue | schema 贯通、证据展示，不复制计算 |
| strategy.py | 按 profile、证明范围和 confirmed 状态显式消费 |
| 后续新增 movement 模块 | 仅在递归里程碑实施 |

信号建议增加：

```text
strength_profile / strength_relation / strength_evidence_id
level_proof_id / level_proof_status / level_proof_scope
unmet_requirements / decision_profile
```

详细测量值和依赖可单独存证据表，信号只保存引用。具体采用独立表还是初期字段嵌入，由当前 contracts 决定；不需要一次增加未使用的完整高层表集合。

缓存身份至少考虑：dataset/data_revision、算法/源码身份、参数 profile、对象 ID/revision、窗口与范围 profile、判定时点。仅 ID 和端点不够；成员变化但端点相同时也要失效。

证据 known_at 取所有依赖可用时间和本证据发现时间的最大值。极值锚点时间不能替代可用时间。

价格持久化使用已有定点语义；不要混用 local center 的 tick 与 price_i64。必要转换通过唯一权威转换逻辑，并在证据标注单位。

## 9. 现有策略消费者的关联核对

基线 `python/src/tvbt/strategy.py::_run_centre_oscillation_spread()` 约第 4668 行仍检查 `divergence_kind == "consolidation"`；信号生成已存在 `center_oscillation`。

实施时核对当前 HEAD 是否已经修复，并追踪该策略真正应消费哪类信号。不能为了让测试通过，把所有 consolidation 和 center_oscillation 混为同类。

这项可作为独立小修复或显式纳入对应里程碑，不能在 M1 无记录改变旧策略结果后仍声称完全等价。

同时检查：趋势候选对盘整分支的去重/抑制，forming 对象的修订删除，标准一类点对 confirmed 和 profile 的过滤。新研究候选不能意外改变原正式信号去重或触发订单。

## 10. 分阶段实施和验收

### M1：接口拆分与旧版等价

做：

- 提取结构比较上下文；封装 legacy 力度和决策。
- 保持旧默认输出、信号 ID、确认时间、profile、去重和交易资格。
- 建立可记录全体结构比较机会的研究/审计通道，隔离交易消费。
- 必要的 schema 改动与事件/存储同步；不要提前实现全部新模型。

不做：切换默认主力度、完整递归、重新定义原有边界或三类点。

验收：旧 profile 在已有合法结构 fixtures 与已有可用数据上的信号、known_at、revision/事件语义、买卖点一致。基线比较应来自旧提交或固定的真实 fixture，而不是只调用同一份新实现两遍。

### M2：独立价格—时间力度

做：price_displacement_speed_v1，完整测量证据，MACD 诊断可选；严格缺样本处理。

验收：改变或移除 MACD 不改变新主力度结果；weaker/stronger/equal/conflict/unknown 均有覆盖；原价格和时间语义保持因果。

均线共享函数的严格化可作为 M2 子任务，仍保持一次只完成一个明确子任务。segment_ma_spread_v1 不是本阶段必选项。

### M3：局部级别证据

做：中枢和运动来源投影，逐项验证器，unknown 原因，证明 scope 与状态展示。

验收：局部映射能解释；未实现的递归要求为 unknown；不能仅因两个 BI 中枢就宣布原文完整级别成立。

### M4：同条件对照研究

做：使用相同数据修订、相同结构比较集合、相同成本与成交语义，对照旧版与新力度；按证明范围分组。

报告：候选总数、可判定比例、力度分歧、unknown 原因、确认延迟、后续价格路径和净绩效。

不做：以收益更高证明定义正确，或在同一测试段优化后声称获得样本外验证。

### M5 及以后：相对级别递归

独立更新范围、定义底座/完成规则/证据 DAG，再实现 Movement 构造和验证。不要在 M1–M3 恢复已删除高层生产链或把旧标签当证明。

## 11. 测试清单

优先复用：

```text
python/tests/test_chan_signals.py
python/tests/test_chan_engine.py
python/tests/test_chan_algorithm.py
python/tests/test_chan_storage.py
python/tests/test_chan_infrastructure.py
python/tests/test_chan_actual_ranges.py
python/tests/test_chan_single_scope_profiles.py
python/tests/test_auxiliary_ma_kiss.py
```

新增测试应验证有意义的行为，不只镜像函数实现。

| 场景 | 应有结果 |
|---|---|
| legacy 重构前后 | 信号和交易语义一致 |
| 同结构、同价格窗口，改变/移除 MACD | 新价格力度结果不变 |
| 位移和速度相反 | conflict，不强行背驰 |
| 缺 MA 或 raw 观察样本 | unknown，不制造弱化 |
| 零时长、参考值零、非法方向 | 明确原因，不除零 |
| 周末/休市跨度 | 时间按观察间隔计，不膨胀 |
| 合法正比例价格缩放 | 非阈值 profile 的关系保持，单位一致 |
| 内部有两个中枢但无级别证明 | 仅投影或 unknown，不原文升级 |
| 首次回抽失败、后次成功 | 仍保持首次失败 |
| 子级背驰、父级力度未弱 | 不发布父级主模型背驰 |
| 相同 ID/端点但成员 revision 变化 | 证据与缓存正确失效 |
| forming 后力度恢复 | 发布当时修订/撤销，不回填 |
| 完整计算 vs 每个前缀 | as-of 可见事实一致 |
| 检查点恢复 vs 连续推进 | 事件和证据一致 |
| 证据自依赖或循环 | 拒绝通过 |
| 新审计候选出现 | 不改变 legacy 正式去重和成交 |

生成测试 fixture 时保证合法 OHLC 和结构关系。若仓库要求 Python 3.14 或特定本地解释器，遵循当前 AGENTS，不用其他版本通过后声称完成规定检查。无法运行的检查清楚列出。

## 12. 工程应用与解释界面

### 12.1 图形复盘

点击背驰对象应能看到：

- 比较的是哪两个运动、窗口和对象 revision。
- 主力度指标的参考值/当前值及 relation。
- MACD 是一致、冲突还是缺失，明确仅为辅助。
- A/B、c、b<=c 各项证明结果和不足原因。
- 极值发生时间与证据 known_at 的差距。

图层可分：结构候选、工程力度弱化、满足指定证明规则的背驰、子级定位点。不把所有点绘制成同一个“确定买卖点”。

### 12.2 回测与策略

策略显式声明消费的 decision_profile、proof scope 和形成/确认状态。沿用收盘获得信号、下一根开盘成交等当前已声明规则，不能把证据事后确认时间回移至极值。

弱化是判定证据，不等同盈利或确定反转幅度。策略仍按既有明确的入场、持有、失败退出处理；本任务不顺便增加任意止损、仓位或综合打分。

### 12.3 性能和可维护性

首轮优先保证清楚和确定性。后续可对完整且未修订窗口缓存测量，或使用前缀和，但须保持 revision 失效、缺样本计数和 as-of 语义。

级别证据按依赖修订重算受影响范围；不依赖每根 K 重扫全部历史作为唯一实现，但也不能为增量优化改变结果。先建立全量参考，再验证增量一致性。

不引入数据库、Redis、WebSocket 或额外服务来解决本次算法问题。

## 13. Codex 交付要求

每个里程碑交付：

1. 当前代码相对文档基线的差异和最终实施范围。
2. 代码、contracts、版本/缓存身份、必要文档的完整改动。
3. 实际执行的测试与结果；未执行项和原因。
4. 新旧行为差异，以对象/证据/known_at 为单位解释。
5. 不超过当前范围的后续任务列表。

未运行研究不得填写新的收益、信号数量或性能数字。不要把本文提议的名称、接口和字段说成现有功能。

## 14. 第一轮执行摘要

```text
先完成 M1。
建立结构上下文和力度接口。
将 19.3.2 全部现有条件封装到 legacy profile。
保持旧默认输出与交易结果。
审计候选不自动交易。
准备 M2 可接入的位置，但不实现全部后续阶段。
按仓库规范验证并提交可审阅结果。
```

完整启动任务见同目录 `CODEX_TASK.md`。
