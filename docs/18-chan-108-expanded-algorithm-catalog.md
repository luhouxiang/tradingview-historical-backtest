# 108 课单标的、单周期算法清单与使用场景

研究日期：2026-09-05。配套：[校准报告](17-chan-108-recalibration-20260905.md)、[分步详细方案](19-chan-108-single-scope-plan.md)。

## 1. 范围与阅读方式

本清单聚焦一个 `dataset_id + data_revision + bar_timeframe`，结构来源固定为已有包含→分型→笔→线段→线段中枢事件链。**不开展多级别、多周期、跨标的、区间套或核心/机动双账本方案。**同一 K 线周期上的多条均线只是参数不同，属于本轮范围；它们不是多周期行情。原文的“次级别走势”在现系统中的已确认线段投影均明确标识，不冒充完整递归。

共梳理 **62 个可检验模块**：14 个结构模块、10 个信号/诊断模块、14 套已有交易程序、18 个辅助模块、6 个执行/研究模块。它们不是 62 套独立交易策略，也不是新增了 62 个生产算法。旧目录只有 27 个较粗条目，两种粒度不可直接相减。

“已有”表示代码存在；“校准”表示需补来源、边界或反例；“补充”表示后续可新增的输出或研究 profile；“A–E 已完成”表示本轮单周期计划已经落地。每行的 `GEO/SIG/STR/AUX/RISK` 缩写映射到旧目录 `ALG-*`，具体文件见第 8 节。原文定义、推导、辅助、经验和工程的区分见校准报告。

通用时间规则：端点时间、结构确认时间、最早可用时间分别保存。确认只消费当前输入前缀；来源修订在当时记录，不覆盖旧事件。正式策略只消费 confirmed 事实，默认确认 K 收盘后发信号、下一可交易 K 开盘撮合；辅助输出默认不交易。下面没有另写确认规则的行也必须遵守这些规则。

## 2. 结构模块（G01–G14）

| ID / 算法 | 依据与性质 | 规则与最早可用时刻 | 使用场景 | 当前状态 / 映射 |
|---|---|---|---|---|
| G01 顺序方向包含 | [62]、[65]，定义 | 向上合并取高高/高低，向下取低高/低低；初始未知方向独立保存，当前合成 K 可修订 | 消除包含后的结构输入；追溯原始极值 | 已有；GEO-001 |
| G02 严格分型及候选生命周期 | [62]、[77]，定义+工程 | 中间 K 的高低同时高于/低于两邻；右侧 K 未完成只候选，完成后确认或失效 | 回放观察正在形成的顶底 | 已有；GEO-002 |
| G03 唯一笔链与极值淘汰 | [77]，定义 | 相邻异类有效分型连接；同类取更极端；保证成员间隔与有效区间极值，尾链修订因果化 | 稳定笔锚点，避免穿过更高有效顶/更低底 | 已有并校准计数说明；GEO-003 |
| G04 单周期笔四状态 | [91]、[105]，定义+工程 | 上升延续、顶形成、下降延续、底形成；另设初始化；按当前结构推进 | 明确“正在成顶”与“下降已成立”的区别 | 已有；GEO-003 |
| G05 特征序列标准化 | [67]，定义 | 上段取反向笔区间为特征元素，下段镜像；按序处理包含后识别特征分型 | 线段判定前置；解释分段依据 | 参考扫描器已有；GEO-004，补显式证据 |
| G06 无缺口线段破坏 | [67]、[71]，定义 | 标准特征分型第一、二元素无缺口时按第一种规则判定，待相关元素完成 | 普通转折处确认前段结束 | 校准两侧包含边界；GEO-004 |
| G07 有缺口线段破坏 | [67]、[78]，定义 | 第一、二元素有缺口时继续观察反向走势的第二特征序列；不能见第一处分型就结束 | 跳跃式行情、复杂中继 | 校准确认链；GEO-004 |
| G08 笔破坏失败后的线段延续 | [78]、[81]，定义 | 破坏笔未发展成所需线段破坏、原方向恢复时保留延续；不得提前固化三段 | 震荡中“古怪线段”、修订解释 | 参考逻辑已有；补数值反例 |
| G09 线段实际区间标准化 | [78]，定义+工程 | 结构端点保留；另取全部已知组成部分的实际高低及来源供上层使用 | 修正中枢重叠、外围、三类点和 Zn 范围 | **A–E 已完成**；GEO-004/005；`constituent_bi_union_v1` |
| G10 三构件中枢及点中枢 | [17]、[54]，定义 | 三个完成构件交集 `ZD=max(low), ZG=min(high)`；非空即成立，单价位保留 | 形成冻结结构参照 | 已有线段投影；GEO-005；核初始扫描边界 |
| G11 冻结核心与延伸 | [20]，定义 | 核心形成后不滑动；符合延伸关系时扩展时间与外围，归属也须当时已知 | 避免中枢矩形随新走势漂移 | 已有；GEO-005；依赖 G09 |
| G12 相邻中枢关系 | [20]，定义 | 比完整外围：后 DD 高于前 GG 为上移，后 GG 低于前 DD 为下移，其余为重叠/扩展观察 | 趋势候选、停止旧震荡程序 | 已有；GEO-006；不在本轮生成高级别结构 |
| G13 固定结构层走势分类 | [17]、[89]，定义+投影 | 一个中枢为盘整，满足同向严格分离的多中枢为趋势；未完成另列，不能按涨幅大小分类 | 交易归因、筛选适合的单周期程序 | 已有；GEO-006；限固定层 |
| G14 价格缺口回补生命周期 | [77]，定义+工程 | 原始价格区间间隙记录为形成、部分回补、完全回补；输入 K 线断档后活动缺口转为未知 | 缺口行情复盘、避免将“尚未回补”当成永不回补 | **A–E 已完成**；`aux_price_gap_lifecycle`；区别于 G07 特征序列缺口；不填补缺失 K |

## 3. 信号与诊断模块（S01–S10）

| ID / 算法 | 依据与性质 | 规则与最早可用时刻 | 使用场景 | 当前状态 / 映射 |
|---|---|---|---|---|
| S01 趋势背驰结构资格 | [37]，定义+投影 | 先确认同级趋势、比较最后 b/c、新极值；原文还要求 c 的内部资格，当前只记录固定线段代理边界 | 拒绝把任意两段 MACD 缩短当标准趋势背驰 | 已有但需限定完整性；SIG-001 |
| S02 同向 MACD 面积力度 | [24]、[25]，辅助+工程 | 上段累计正柱、下段累计负柱绝对值；比较可比完成段，参考面积必须有效 | 力度衰减证据、信号解释 | 已有；SIG-001/002；不是唯一原文力度定义 |
| S03 盘整背驰 | [27]、[39]，推导 | 同中枢内或进入/离开同向段力度减弱；不强加趋势新极值条件，待反向确认 | 震荡回归、类一信号 | 已有线段投影；SIG-002 |
| S04 一类点生命周期 | [17]、[43]，定义+工程 | 趋势背驰端点为候选；现 profile 等首个已完成反向段确认，来源改变则失效 | 保守反转入场、测确认延迟 | 已有；SIG-001；不称最早区间套定位 |
| S05 一类点后二类点三分支 | [101]，定义+投影 | 一类点后首次反向段及首次回试；二三合一最强，不破前极值一般，破极值需自身盘背证据 | 按强弱分支研究仓位及后继表现 | 已有；SIG-003；不含无本级一类点的小转大分支 |
| S06 三类点首回试 | [20]、[54]，定义有冲突 | 完成离开后的第一次完成回试；当前等号允许，严格脱离另待 profile；第二次不补充首次资格 | 突破后的可解释入场 | 已有；SIG-004；K01 优先 |
| S07 二三类点共点与去重 | [21]，定义+工程 | 两种结构证据同锚点可共存；交易资格按语义来源只消费一次 | 防止最强二买触发两笔意外加仓 | 已有；SIG-003/004，交易层持续校验 |
| S08 后继跟随成功/失败 | [79]、[107]，推导 | 入场回试后首个同向完成段，与离开段极值比较；未创新或已背驰为失败，成功只支持继续观察 | B2/B3 的持有、退出、趋势交接 | 已有分散分支；STR-002/003，宜统一诊断输出 |
| S09 背驰后回归深度 | [29]，定义+工程 | 分别记录至核心和外围的回归关系与距离；随当前已确认信号冻结证据 | 辨别弱反弹、核定回归目标 | **A–E 已完成信号证据字段**；不能把外围可达替换成核心必达 |
| S10 反转方向力度比较 | [33]，推导+辅助 | 单层借用前下行与反弹的力度及起跌参照比较思路；同周期 MACD 跨正负侧比较，仅出弱反转提示 | 中枢回归失败、反弹空间不足 | **补充研究 profile**；未复现原例递归前提，不冒充同向背驰 |

## 4. 单周期交易程序（T01–T14）

这些是可以逐一回测比较的已有程序及其校准目标。表内“做空”镜像只在品种执行规则允许时使用；所有参数、成本与退出方式应在 run 中固定。不要把不同退出逻辑合并成一个策略结果。

| ID / 程序 | 依据 | 入场 → 持有/退出 | 适合研究的行情 | 现有 algorithm_id |
|---|---|---|---|---|
| T01 中枢五状态决策树 | [49]，推导 | 当前中枢关联标准 B3/S3 才开仓；回归核心或越向另一侧退出。当前程序没有实现五状态下的全部理论操作 | 区间离开与回归频繁切换 | `fixed_level_centre_decision_tree` |
| T02 下跌反转只做多 | [17]、[29]，推导 | 标准 B1 开多，标准 S1 平多；不以类一代替，不反手做空 | 有完成下跌结构的反弹研究 | `downtrend_reversal_only` |
| T03 趋势背驰双向反转 | [15]、[37]，推导 | 标准 B1/S1 入场；相反已确认一类点先平再反向 | 可双向交易、较大摆动 | `trend_divergence_reversal` |
| T04 只做二买 | [86]、[101]，推导 | 选定强弱分支的标准 B2 入场；后继不创新高/背驰退出，满足 B3 与跟随条件才交接持有 | 愿意放弃最低点、等待首次回试 | `second_buy_only` |
| T05 只做三买并跟踪趋势 | [79]、[107]，推导 | 标准 B3 入场；后继失败退出，成功可穿过后续新中枢持有至规定反向/回归事件 | 向上中枢迁移；较低换手 | `third_buy_only` |
| T06 盘背中枢回归 | [27]、[33]，推导 | 类一入场，目标为来源冻结核心；反向标准三类点先确认则回归失败并按既有规则切换 | 无标准趋势的一中枢震荡 | `consolidation_divergence_centre_reversion` |
| T07 中枢震荡差价 | [38]、[39]、[92]，推导 | 中枢内完成线段盘背触发，Zn 与成本限制参与；离开/升级风险按现规则停止 | 宽度足够、成本较低的横向往复 | `centre_oscillation_spread` |
| T08 三类点后迁移一程 | [49]，推导 | B3/S3 开仓；新同级中枢确认或规定反向一类点退出 | 获取两个中枢之间的连接走势 | `third_buy_centre_migration_hold` |
| T09 首中枢三类点优先 | [49]、[79]，推导 | 当前方向周期首个标准三类点参与，后续同向过滤；新中枢/反向点退出 | 控制追逐较晚趋势信号；单标的周期内选择 | `first_centre_B3_rotation`；名称 rotation 不授权跨标的 |
| T10 固定线段同级分解程序 | [38]、[39]、[40]，推导+投影 | 固定奇偶，比较 Ai/Ai+2；不创新或盘背时切换，按 Ai+3 位置分类 | 按固定结构节奏机械执行 | `same_level_decomposition_program`；本轮仅 L0，升级后等待是既有边界 |
| T11 目标反弹分段 | [47]、[107]，推导+投影 | 标准 B1/S1 启动；首段部分兑现、反向回试守住来源极值后回补；首中心三类点与跟随后才交接 | 反弹不保证第二段创新高的行情 | `target_level_rebound_segmented_operation`；仅已有线段映射 |
| T12 底/顶构造 | [88]、[108]，推导+投影 | 一类点启动，关联首中枢首次同向三类点成功、反向三类点失败；成功后连接走势按既有规则持有 | 底部反复、顶部构造、失败识别 | `bottom_top_construction`；粗分型分支不交易 |
| T13 迁移程序加 MACD 状态过滤 | [49]、[103]，工程组合 | 复用 T08，DIFF/DEA 同向且达连续根数才放行；被过滤资格立即消费，MACD 不触发退出 | 测趋势状态过滤是否减少逆向信号 | `third_point_migration_macd_regime`；已有，不是原文单列策略 |
| T14 首中枢程序加 MACD 过滤 | [79]、[103]，工程组合 | 复用 T09 及同一 MACD 放行规则；不等未来指标转向补单 | 更少进场的首中枢研究 | `first_centre_B3_macd_regime`；已有 |

## 5. 辅助与经验模块（A01–A18）

| ID / 算法 | 依据与性质 | 可编码规则 / 尚需固定的参数 | 使用场景 | 当前状态 |
|---|---|---|---|---|
| A01 分型强反转轮廓 | [82]，辅助 | 顶分型第三根跌破第一根低点且收盘不高于其中点，底部镜像；只在分型可知后发布 | 对确认分型增加强弱解释 | 已有；GEO-002/C-058 |
| A02 分型形态特征向量 | [82]，辅助+工程 | 已输出处理后中间 K 的上/下影、实体、全幅和收盘位置；第一根中点、包含次数可后续另加；不合成主观总分 | 按分型形态分组复盘，完善未分类样本 | **A–E 已完成基础向量**；`processed_bar_ohlc_v1`；P-030 继续禁止完整评分 |
| A03 飞吻/唇吻/湿吻识别 | [11]，辅助 | 用同周期短长均线的走平、接近、穿越及延续关系；阈值照当前 P-023 固定 | 低复杂度观察、传统系统基线 | 已有；AUX-001 |
| A04 吻序号与旧买点程序 | [12]、[14]，辅助 | 区分多空排列、第一次/后续缠绕；旧 B1/B2 候选不映射标准点，“最后一次”不能事先认定 | 观察多头首次整理与空头后续整理 | 已有代理；AUX-001，力度仍可补 |
| A05 均线间面积力度与平均力度 | [15]，辅助+工程 | 在相邻完成吻边界间按 `(start,end]` 右矩形累加均线绝对距离，并输出时长、平均力度和同状态前段比值 | 与 MACD 面积并列解释衰减；检验旧系统 | **A–E 已完成**；`aux_ma_kiss_legacy@2.0.0`；只输出观察事件 |
| A06 MACD 零轴防守 | [103]，辅助 | DIFF/DEA 均在负缓冲下连续 N 根限制多头参与；均重上正缓冲 M 根只恢复候选资格 | 操作能力有限时的风险过滤 | 已有；AUX-002；空头规则须镜像明确 |
| A07 MACD 回零与线高证据 | [24]、[25]、[50]，辅助 | 对已选结构段记录 DIFF/DEA 的峰值、回零次数、柱面积；本周期显示不清标证据不足，不改选段 | 审核“只有面积变小”的背驰 | **补充结构绑定输出**；普通 MACD 已有 |
| A08 BOLL 超强区退出 | [90]，辅助 | 轨外→回轨内→价格创新极值但未有效重返轨外；当前连续确认参数留档 | 强势衰减、中阴候选预警 | 已有；AUX-003 |
| A09 BOLL 二类点区域提示 | [90]，辅助 | 在既定上下文中跟踪上下轨首次转向及回试区；不生成标准 B2/S2 | 标出支撑/阻力观察区域 | 已有；AUX-003 |
| A10 BOLL 缩口提示 | [90]，辅助 | 活动中阴投影内宽度连续收缩；只发结束/变化提醒，不确认高级别或三类点 | 对震荡末端提高观察优先级 | 已有单周期投影；AUX-003 |
| A11 Z/Zn 中轴强弱 | [92]，辅助 | `2Z=ZD+ZG`、`2Zn=区间低+高`，直接用整数和比较强弱；来源为完成线段投影 | 中枢内买卖两侧强弱与仓位过滤 | 已有；AUX-004；依赖 G09 范围校准 |
| A12 Zn 越界/楔形预警 | [92]，辅助+工程 | 越过核心边界或连续中点抬高/降低且未越界；当前三点严格单调是工程选项，最多九点有原文依据 | 震荡方向偏移的提醒 | 已有；AUX-004；不研究次级末中枢分支 |
| A13 粗分型底/顶区间 | [108]，辅助 | 使用中间处理后 K 区间；越极值失败，另一侧有效站住仅报粗观察成功；“有效”需连续根数 | 无精确买点时区分区间试探结果 | 已有；STR-009 粗分支，不交易 |
| A14 单层旧中枢回撤深度 | [99]，推导+辅助 | 信号冻结来源中枢序号、同链更早中枢数及核心/外围回归深度；不确认多级别中阴 | 辨别原趋势被破坏程度、回撤归因 | **A–E 已完成证据输出**；`fixed_level_confirmed_centres_v1`；非完整 BARDO |
| A15 单标的均线攻克等级 | [106]，经验 | 显式指定观察起点和方向；截至当前记录曾被严格收盘攻克的均线，按最短未攻克周期分层；等号不算攻克 | 观察本标的反弹推进位置 | **A–E 已完成**；`aux_single_instrument_ma_observation` |
| A16 同趋势压制均线一致性 | [106]，经验 | 价格触及均线后，在显式 `pressure_confirmation_bars` 窗口结束时检查是否仍被收盘拒绝，并记录首次受压均线与一致性 | 提醒反弹强度与前次不同 | **A–E 已完成确定性观察器**；均线不确认结构级别 |
| A17 八根 30 分钟日内分类 | [46]、[47]，经验 | 原课日盘八根模板内，按连续三根区间重叠判一重叠区、双区或无区；全日收盘后确认 | 符合原课时段的日内类型复盘 | 已有；AUX-005；拒绝期货夜盘/非 30m，不新做会话映射 |
| A18 日内强弱位置细分 | [46]，经验 | 使用重叠区出现顺序、日高低与收盘位置分类；“接近”不凭空设 epsilon | 在 A17 合法输入下区分平衡/转折/偏强弱日 | 已有；AUX-005/P-027；非标准中枢 |

## 6. 执行与研究模块（R01–R06）

以下六项是由课程操作纪律导出的工程支持，不能计为作者给出的六套精确交易公式。复用当前历史回测执行器，不扩展实盘、柜台、审批或实时监控。

| ID / 模块 | 依据与性质 | 规则 | 使用场景 | 当前状态 |
|---|---|---|---|---|
| R01 费用后可参与空间 | [35]、[107]，工程 | 信号时只用已知价格/预设成本估空间；真实下一开盘仅撮合时复核，不回填选单条件 | 小振幅中枢、迟确认信号 | 部分已有；补统一空间诊断 |
| R02 成交容量与不可成交 | [16]、[50]，工程 | `volume_cap_ioc` 下零量零成交；`unlimited` 是显式理想撮合假设；当根总量限开盘撮合属于历史模型，不是开盘前已知量 | 小容量行情、压力回测 | 既有执行与风险层；RISK-001；须区分模式 |
| R03 资金预算只降不增 | [31]、[74]、[95]，工程 | 固定初始预算；不因亏损追加来美化表现；数量受风险上限约束并记录资金流 | 统一比较手数和回撤 | 既有 RISK-001，新增资金流须另立契约 |
| R04 完全分类与能力门禁 | [12]、[68]、[100]，推导+工程 | 对本程序合法后续预先规定持有/退出/等待；缺少处理能力则不参与 | 避免只写入场没有失败分支 | 风险层已有；逐策略审计 |
| R05 入场理由失效退出 | [13]、[41]，推导+工程 | 来源修订、关键结构被破坏时按当时已知事实退出；不能以浮盈/浮亏替代结构事实 | 候选失败、结构重画、反弹失败 | 部分策略已有；统一 reason 与来源链 |
| R06 确认延迟与前缀审计 | [32]、[50]，工程 | 测端点→确认→可用→成交延迟和价格偏移；前缀运行/恢复与事件重放一致 | 判断理论空间是否在可成交前已经消耗 | 因果基础已有；**补充统一诊断报表** |

## 7. 不进入本轮详细方案的内容

区间套、小转大无本级一类点的二类点、三层级完全分类、跨 K 周期四态矩阵、递归高级中枢、父中枢外的次级趋势末中枢、跨标的轮动/板块强弱、指数相位领先、核心/机动双账本均不纳入上述 62 项详细实现范围。对它们的现有语义覆盖只在校准报告记录边界。

[第 72 课](https://chanlun108.cn/chanzhongshuochan108ke/72.html)还给出纯形态买卖段思路，但其离开须与中枢同级。不能将当前一条中枢组成线段直接替代这种完成走势；本轮仅登记该文义，不另造一个低配“标准二买”策略。

第 104 课没有提供可执行的能量公式；第 9 课的独立系统概率不能未经检验直接相乘；历史权证发行人案例、主力动机猜测和关于人生/修炼的叙述不硬拆成数值算法。数量以可检验的输入、规则和输出为准。

## 8. 与当前实现的文件对照

| 旧目录/能力 | 主要文件 | 本清单模块 |
|---|---|---|
| GEO-001/002/003 | [engine.py](../python/src/tvbt/chan/engine.py) | G01–G04、A01 |
| GEO-004/005/006 | [reference.py](../python/src/tvbt/chan/reference.py)、[level_graph.py](../python/src/tvbt/chan/level_graph.py)、engine.py | G05–G13、A02；递归边界见报告 |
| SIG-001–004 | [signals.py](../python/src/tvbt/chan/signals.py) | S01–S09、A14 |
| STR-001–005/008/009 及早期派生策略 | [strategy.py](../python/src/tvbt/strategy.py) | T01–T12、S08 |
| 10AB 组合 | strategy.py 及 MACD 权威指标 | T13–T14 |
| AUX-001 | [ma_kiss.py](../python/src/tvbt/auxiliary/ma_kiss.py) | A03–A05 |
| AUX-002 | [macd_zero_axis.py](../python/src/tvbt/auxiliary/macd_zero_axis.py) | A06 |
| AUX-003 | [boll_bardo.py](../python/src/tvbt/auxiliary/boll_bardo.py) | A08–A10 |
| AUX-004 | [zn.py](../python/src/tvbt/chan/zn.py) | A11/A12 |
| AUX-005 | [daily_30m.py](../python/src/tvbt/auxiliary/daily_30m.py) | A17/A18 |
| AUX-006 | [ma_sector_rotation.py](../python/src/tvbt/auxiliary/ma_sector_rotation.py) | A15 的多标的数学部分 |
| 单标的 MA 观察 | [single_instrument_ma.py](../python/src/tvbt/auxiliary/single_instrument_ma.py) | A15/A16；无需板块或横截面输入 |
| 价格缺口生命周期 | [price_gap.py](../python/src/tvbt/auxiliary/price_gap.py) | G14 |
| RISK-001 | [unified_overlay.py](../python/src/tvbt/risk/unified_overlay.py)、[backtest.py](../python/src/tvbt/backtest.py) | R01–R06 的部分基础 |

生产目录没有 G01/T01 这类新 ID。本清单 ID 只是本轮研究追踪编号，不进入 API、参数签名或旧覆盖统计。

[9]: https://chanlun108.cn/chanzhongshuochan108ke/9.html
[11]: https://chanlun108.cn/chanzhongshuochan108ke/11.html
[12]: https://chanlun108.cn/chanzhongshuochan108ke/12.html
[13]: https://chanlun108.cn/chanzhongshuochan108ke/13.html
[14]: https://chanlun108.cn/chanzhongshuochan108ke/14.html
[15]: https://chanlun108.cn/chanzhongshuochan108ke/15.html
[16]: https://chanlun108.cn/chanzhongshuochan108ke/16.html
[17]: https://chanlun108.cn/chanzhongshuochan108ke/17.html
[20]: https://chanlun108.cn/chanzhongshuochan108ke/20.html
[21]: https://chanlun108.cn/chanzhongshuochan108ke/21.html
[24]: https://chanlun108.cn/chanzhongshuochan108ke/24.html
[25]: https://chanlun108.cn/chanzhongshuochan108ke/25.html
[27]: https://chanlun108.cn/chanzhongshuochan108ke/27.html
[29]: https://chanlun108.cn/chanzhongshuochan108ke/29.html
[31]: https://chanlun108.cn/chanzhongshuochan108ke/31.html
[32]: https://chanlun108.cn/chanzhongshuochan108ke/32.html
[33]: https://chanlun108.cn/chanzhongshuochan108ke/33.html
[35]: https://chanlun108.cn/chanzhongshuochan108ke/35.html
[37]: https://chanlun108.cn/chanzhongshuochan108ke/37.html
[38]: https://chanlun108.cn/chanzhongshuochan108ke/38.html
[39]: https://chanlun108.cn/chanzhongshuochan108ke/39.html
[40]: https://chanlun108.cn/chanzhongshuochan108ke/40.html
[41]: https://chanlun108.cn/chanzhongshuochan108ke/41.html
[43]: https://chanlun108.cn/chanzhongshuochan108ke/43.html
[46]: https://chanlun108.cn/chanzhongshuochan108ke/46.html
[47]: https://chanlun108.cn/chanzhongshuochan108ke/47.html
[49]: https://chanlun108.cn/chanzhongshuochan108ke/49.html
[50]: https://chanlun108.cn/chanzhongshuochan108ke/50.html
[54]: https://chanlun108.cn/chanzhongshuochan108ke/54.html
[62]: https://chanlun108.cn/chanzhongshuochan108ke/62.html
[65]: https://chanlun108.cn/chanzhongshuochan108ke/65.html
[67]: https://chanlun108.cn/chanzhongshuochan108ke/67.html
[68]: https://chanlun108.cn/chanzhongshuochan108ke/68.html
[71]: https://chanlun108.cn/chanzhongshuochan108ke/71.html
[74]: https://chanlun108.cn/chanzhongshuochan108ke/74.html
[77]: https://chanlun108.cn/chanzhongshuochan108ke/77.html
[78]: https://chanlun108.cn/chanzhongshuochan108ke/78.html
[79]: https://chanlun108.cn/chanzhongshuochan108ke/79.html
[81]: https://chanlun108.cn/chanzhongshuochan108ke/81.html
[82]: https://chanlun108.cn/chanzhongshuochan108ke/82.html
[86]: https://chanlun108.cn/chanzhongshuochan108ke/86.html
[88]: https://chanlun108.cn/chanzhongshuochan108ke/88.html
[89]: https://chanlun108.cn/chanzhongshuochan108ke/89.html
[90]: https://chanlun108.cn/chanzhongshuochan108ke/90.html
[91]: https://chanlun108.cn/chanzhongshuochan108ke/91.html
[92]: https://chanlun108.cn/chanzhongshuochan108ke/92.html
[95]: https://chanlun108.cn/chanzhongshuochan108ke/95.html
[99]: https://chanlun108.cn/chanzhongshuochan108ke/99.html
[100]: https://chanlun108.cn/chanzhongshuochan108ke/100.html
[101]: https://chanlun108.cn/chanzhongshuochan108ke/101.html
[103]: https://chanlun108.cn/chanzhongshuochan108ke/103.html
[105]: https://chanlun108.cn/chanzhongshuochan108ke/105.html
[106]: https://chanlun108.cn/chanzhongshuochan108ke/106.html
[107]: https://chanlun108.cn/chanzhongshuochan108ke/107.html
[108]: https://chanlun108.cn/chanzhongshuochan108ke/108.html
