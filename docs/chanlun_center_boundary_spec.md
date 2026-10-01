# 中枢边界与连接走势：可实施规范 v1.0

适用：TVBT 笔中枢与基础线段中枢的识别、显示和回放。日期：2026-09-14。

## 1. 核心决策与适用限制

采用“固定核心价格区间、每个模式/层级一个活动中枢、离开后首次反向单元确认分界、分界后重新找种子、连接角色单独存储”的工程口径。

本规范是明确选择一种可重复的局部分解，不声称它是108课唯一分解。笔中枢是软件简化结构；基础线段中枢是选定底层图后的基础结构。两者不能自动命名为严格的某分钟级别中枢。严格递归的走势类型、次级别盘整回试和高级别中枢识别需要另一个模块。本规范不能用“一条向下笔”替代任意级别的完整回试走势。

进入/离开不是排他的所有权。一个单元可同时作为前中枢的离开、后中枢的进入；特殊情况下也可与已确认种子单元重合。不能为了画出空隙删掉种子。

## 2. 原文依据与工程新增的分界

以下为原文内容的摘要，链接为108课转载页，不是作者原博客。

| 来源 | 原文要点 | 工程含义 |
|---|---|---|
| [第18课](https://chanlun108.cn/chanzhongshuochan108ke/18.html) | 三个完成的连续次级别走势重叠建立中枢；中枢破坏涉及离开及随后回抽；回抽也可能是盘整 | 种子必须完成；价格瞬间越界不立即关闭中枢；本规范单元回试属于局部实现范围 |
| [第20课](https://chanlun108.cn/chanzhongshuochan108ke/20.html) | 初始三个走势确定核心区间；延伸与中枢间关系需区分；核心分离但外围波动重叠可能产生更大级别结构 | 固定ZD/ZG；把核心区间、外围波动和时间边界分开保存 |
| [第33课](https://chanlun108.cn/chanzhongshuochan108ke/33.html) | 走势存在合理的多种释义，外部连接部分不总能单独存在；讨论延伸及级别变化 | 固定分解版本；不得强制每个中枢前后都有独立隔离段；父级变化单独处理 |
| [第35课](https://chanlun108.cn/chanzhongshuochan108ke/35.html) | a+B+b可按结合律重新分解，连接走势未必与中枢内部构成走势同级别 | 不把“进入必是一根同层笔/段”冒充通用理论 |

工程新增：最早种子优先、严格正宽度重叠、触边算返回、按相邻反向单元确认局部分界、分界后不复用旧种子、固定矩形横向绘制口径。这些均是版本化的软件规则。

## 3. 截图能确认的内容

截图右侧勾选bi与zhongshu，未勾选segments、segment_zhongshu和level_centers。推断主要显示笔及笔中枢，须以实际数据源确认。画面中约19—20日及26—28日的矩形横向交叠，属于需要检查的位置；单凭截图无法判定是共用构成笔、候选矩形、右边界延伸、时间戳错误，还是不同层级混画。

不得从截图臆造精确笔ID、端点或认定某一个框必然错误。

## 4. 输入契约

每个流由(symbol, timeframe, unit_kind, structural_level, algorithm_version, anchor_id)唯一标识。

unit_kind=BI或SEGMENT；同一流内单元时间连续、方向交替，端点相接。跨午休/夜盘不按墙钟时差判断断裂，用pivot_id或连续索引判断。数据缺失、换合约或复权版本改变必须显式分流或重算。

Unit字段：id、index、direction、start_pivot_id、end_pivot_id、start_time、end_time、start_price_tick、end_price_tick、low_tick、high_tick、confirmed_at、source_revision。

价格一律转最小报价单位整数。low/high来自该模式定义的完整单元范围，不只取收盘价；标准笔/段应验证端点极值假设。未确认尾笔/尾段只进入预览流。确认不等于永不纠错，上游修订必须触发显式重放版本。

## 5. 四种边界

1. 核心价格边界core=[ZD,ZG]：只用种子三单元计算。
2. 核心形成时间seed_start/seed_end：第一种子起点、第三种子终点。
3. 局部主体绘图区间body_start/body_end：用于矩形横向宽度，是工程显示约定。
4. 确认时间formed_at/break_confirmed_at：知识何时可用；不等于端点时间。

另保存observed_low/high作为本模块观察到的波动外包络。它不是ZD/ZG，也不能未经走势分解核对就命名为第20课的DD/GG。

## 6. 种子选取

从固定扫描起点scan_floor开始，依次检查连续三单元U[i:i+3]：

    ZD = max(low[i], low[i+1], low[i+2])
    ZG = min(high[i], high[i+1], high[i+2])
    有效 := 三者已确认 AND ZD < ZG

选取第一个有效窗口，赋予固定center_id和seed_ids。不先跳过第一单元，不以“需要进入段”为理由后移。区间仅一点重合不建正宽度中枢，是工程约定。

首个中枢靠近数据左边界时标记left_context_incomplete。增量加载更早历史不得悄悄改变anchor；用户要求完整重算时建立新run版本。

中心存在期间，禁止另起每个滚动三单元的中枢。它们只作为当前结构内部证据，或供独立的多义性分析层查看。

## 7. 状态机：SEEK、ACTIVE、PENDING_BREAK、CLOSED

### 7.1 ACTIVE

ZD/ZG保持不变。检查自第三种子开始的每一个完整单元L；最后一个种子也允许是离开候选，避免漏掉最短形态。

离开候选：

    向上：L.direction=UP   且 L.end_price > ZG
    向下：L.direction=DOWN 且 L.end_price < ZD

候选的意义仅为等待其紧随的反向单元R，不能宣布旧中枢终止。候选/回试未结束时保持PENDING_BREAK。

### 7.2 R确认后的判定

    向上离开成功：R.direction=DOWN 且 R.low > ZG
    向下离开成功：R.direction=UP   且 R.high < ZD

若等于边界，算触及/返回，不确认本模块的分离。该规则比“不跌破/不升破”的字面用法严格，必须在设置中公开。

若回到旧核心：本次候选失败，L/R归入本中枢震荡历史，继续原中枢。失败R若反向穿越另一边界，也可立即作为反方向L，等待下一单元；不能跳过它。每一对相邻单元按时间检查一次即可。

若成功：旧中心进入CLOSED，记录exit_id=L.id、first_retest_id=R.id、break_direction及break_confirmed_at=R.confirmed_at。本模块可以发出LOCAL_B3/LOCAL_S3；严格理论B3/S3由完成级别验证的上层发出。

所有临时触边一旦出现即可使当前候选失效；最终状态消费已确认单元，预览流显示即时失效，不发可交易的确认信号。

### 7.3 新中枢寻找

成功分界后，scan_floor=R.index。从R开始的后续连续三单元重新扫描种子，不把L塞入新种子，不复用旧中心种子。R可以成为新中枢第一构成单元。

要重放R及当前已缓存后续确认单元。结束一个中心不代表新中心已形成，中间可以只有连接区。若长期没有合格三单元，保持SEEK，不凑框。

此非复用约定用于获得单一、可读的局部划分，不宣称穷尽理论允许的所有分解。

## 8. 进入、离开、桥接关系

E=新中心第一种子的前一单元。若E连续、向上起于ZD之下且终于ZG之上或等于ZG，记录local_entry=FROM_BELOW；向下对称。否则entry_id=null，不删除这个合法中心。

若新种子从旧中心回试R开始，则E可能恰好就是旧中心的离开L：同一unit_id标两个角色，不能复制两份单元。

若新种子晚于R开始，旧exit L不必等于新entry E。连接关系保存ordered_unit_ids，从旧exit到新种子前一单元（含两端），可能有多个单元。中间单元不是没人管理的垃圾，属于connection上下文。

Connection字段：id、from_center_id、to_center_id可空、unit_ids、exit_unit_id、entry_unit_id可空、first_retest_id、confirmed_at、roles_overlap_seed。

不要强制“连接区恰好一笔/一段”，也不要强制进入方向等于后续离开方向。

## 9. 矩形时间边界：优先清晰且不删结构

body_start = 第一种子的start_time。

关闭后：

    body_end = max(seed_end, L.start_time)

正常情况下，矩形截止于离开单元起点，L作为独立连接线显示。若L就是第三种子，body_end不能早于seed_end；允许L兼任种子与离开，标roles_overlap_seed=true，不强制制造空白。

新矩形起点 = 新第一种子的start_time。按本规范scan_floor规则，有：

    old.body_end <= old.exit.end_time <= new.body_start

闭合矩形不得遮盖未来形成时间。形成时solid seed框，活动/候选延长部分用虚线或低透明度；分界确认后虚线部分可以收回，这是候选更新，不得伪装成历史已知。

矩形使用半开时间区间[start,end)消除共同端点的像素重复；形成核心范围需保留完整seed覆盖。缺口或特例允许零长度连接，不能靠横向挪动数据造间隔。

确认时间画独立竖标，不用它拉长实体框；回测成交最早发生在confirm之后。

## 10. 核心重叠与高级别结构

两个局部中心C1/C2的核心关系：

    C2.ZD > C1.ZG -> CORE_ABOVE
    C2.ZG < C1.ZD -> CORE_BELOW
    其余 -> CORE_TOUCH_OR_OVERLAP

这些只是矩形关系，不是趋势成立判据。

核心分离但外围波动接触时，不标严格TREND；记录higher_level_review_required。核心重叠时也不删除两个局部中心、也不简单合成大包围盒。高级别模块须按完整次级别走势递归验证，才创建父中心parent_id。

第20课DD/GG计算应基于选定分解中的Z走势序列：GG=max(g_n)、DD=min(d_n)。本地all-unit外包络observed_low/high不能直接替代。未实现该分解的当前模块，trend_status=UNVERIFIED；最多输出“核心上移/下移”。

长时间活动或多轮震荡应向高级别模块持续投递原始单元，不能等局部中心关闭才开始递归。原文关于延伸数量/升级的讨论不能在BI/SEGMENT流中无条件套成“九笔必定某分钟级别”。父级算法版本须单独验证。

同层显示可要求实体框时间不交叠；不同层级父子框允许覆盖，但必须在独立图层显示。这种显示隔离不意味理论上价格区间不重合。

## 11. 数据对象与日志

Center至少保存：id、stream_key、rule_version、seed_ids、ZD_tick、ZG_tick、formed_at、body_start、body_end可空、status、pending_exit_id、exit_id、first_retest_id、entry_id、break_confirmed_at、parent_id可空、left_context_incomplete、source_revision。

日志事件：SEED_FOUND、EXIT_PENDING、RETEST_TOUCH、BREAK_CONFIRMED、SEARCH_RESTARTED、ENTRY_LINKED、CENTER_REVISED。

每条事件记录：center_id、unit_ids、ZD/ZG、比较实际值、event_time、known_at、rule_version、源文件/行号。回放按known_at消费。

对象树悬停必须显示构成三单元ID、前后连接ID、确认时刻、扫描起点、模式/层级。边界模糊问题优先靠这些可审计字段定位，而不是凭框的颜色猜测。

## 12. 顺序处理伪代码

    on_confirmed_unit(unit):
        append_to_stream(unit)
        if no active_center:
            search_earliest_seed_from(scan_floor)
            # 建成后从seed第三单元开始处理已有相邻单元对
        if active_center:
            for (L, R) in unprocessed_adjacent_pairs_from(seed_third_index):
                if departure(L, center) and separated_retest(R, center):
                    close(center, L, R)
                    scan_floor = R.index
                    active_center = null
                    replay_search_and_pairs_from(scan_floor)
                    break
                # 未成功时保持core不变，R仍可作下一对的L
            preview_last_unit_as_pending_exit()

pair游标必须防重复，重放从明确索引开始；emit事件按(center_id,event_type,unit_id,revision)幂等。所有计算只用confirmed_at<=当前回放时间的记录。

## 13. 可手工核对的完整案例

用相连单元端点序列：120,100,115,105,130,118,128,120。

S0=120->100，S1=100->115，S2=115->105：C1=[105,115]。

S3=105->130为L；S4=130->118为R，118>115。在S4确认时关闭C1，C1主体矩形结束于S3起点（也是S2终点）。

从S4开始找种子：S4=130->118、S5=118->128、S6=128->120，生成C2=[120,128]。

S3既是C1离开单元，也是C2进入单元；S4既是C1首次回试，也是C2第一构成单元。S3不用参加C2区间计算。

C1矩形主体、S3连接、C2矩形主体在时间上清楚分开；这并不保证两个中心的外围波动分离，因此不能仅凭本例宣布严格上涨趋势。

## 14. 验收用例

| 编号 | 输入/情形 | 预期 |
|---|---|---|
| A01 | 三个已确认单元有正宽度交集 | 形成一个中心，记录种子 |
| A02 | 在活动中心内，滚动窗口反复重叠 | 保持同一ID，不多画实体框 |
| A03 | C=[105,115]，向上到130，首次回到110 | 不关闭，不触发三买，新种子扫描不启动 |
| A04 | 同上首次最低=115 | 触边，工程口径不确认分离 |
| A05 | 同上首次最低=118且已确认 | 关闭，R为新扫描起点 |
| A06 | 最低暂为118但R未确认 | 只预览，不出确认事件 |
| A07 | C=[105,115]，下破到90、反抽最高100 | 对称关闭，局部三卖 |
| A08 | 成功分界后尚无三个新单元 | 只有连接/候选，无新实体框 |
| A09 | 前exit同时后entry | 一个unit_id、多角色引用 |
| A10 | exit就是第三种子 | 不删种子，body_end>=seed_end，允许角色重合 |
| A11 | 核心分离但外围交叠 | 只标核心位移，高级别检查待定 |
| A12 | 更高级别父框与子框重叠 | 分层显示，不能当作同层重复 |
| A13 | 数据首窗无更早单元 | entry=null、left_context_incomplete=true |
| A14 | 候选向上失败且R穿越下边界 | R仍可作向下候选，不漏反向分界 |
| A15 | 相同输入前缀重复运行 | ID/边界/事件一致，不读取未来 |
| A16 | 上游已确认笔被修订 | 新revision显式重算，不静默覆写已消费信号 |
| A17 | 起点或早期历史改变 | 新run/anchor版本，不声称两次划分必须相同 |

## 15. 给Codex的实施要求

先读取现有zhongshu与segment_zhongshu生成器，确认是否每个三单元窗口都在新增对象、extend时是否改ZD/ZG、矩形右边界是否用了confirm时间、同层是否存在多个活动中心。

实现一个共享的局部中心引擎，BI/SEGMENT分别运行；保留既有算法为legacy版本用于对照，不静默改变历史信号含义。

新增中心与连接数据对象；前端增加进入/离开/回试角色着色与构成ID悬停。默认只显示一个模式/层级的实体中心；候选与父级结构明确区分。

按A01—A17做针对性验证，特别验证未来信息、第三种子兼任离开、失败回试反向穿越及扫描重启。对用户截图对应原始数据导出逐单元解释表，才能最终确认每个框的边界是否正确。

当前文档完成了可实施规则设计；未访问用户工程代码，未声称已修复截图中的程序。

## 16. 15R 线段流进入段修订（算法 18.1.0）

上文第 7.3、8、9 节记录原 `local_center_boundary_v1` 实施口径；自算法 18.1.0 起，仅 SEGMENT 流按本节覆盖其中“分界后必从 R 重启”和“进入段只能是第一种子的前一段”两项。BI 流不变，已完成旧缓存和回测不回写。

前中心由离开段 L、紧随的反向首次回试段 R 确认分界后，如果 L 不是旧中心的第三种子，从 L 起顺序扫描新中枢的连续三条已确认线段。若 L/R/下一段实际区间有正宽度交集，L 既是前中心的离开段，也是新中心的第一种子及进入段：向上分界标 `FROM_BELOW`，向下分界标 `FROM_ABOVE`。新中枢价格仍只按这三条构件的 `ZD=max(low)`、`ZG=min(high)` 确定；没有合格三段时不得为匹配截图造框，并继续从后续窗口扫描。若 L 已是旧中心第三种子，则仍从 R 扫描，避免复用旧种子。

两种角色允许共享同一个线段 ID；连接对象保留这一重合，`SEARCH_RESTARTED` 记录实际扫描起点，分界知识仍只能在 R 已确认后使用。旧中心闭合矩形截至 L 起点，新中心可从同一时点开始；段端点与 K 线价格不移动。AOL9 实例：上移时 K33981 上行段进入，三段核心 2769–2859；下移时 K32257 下行段进入，三段核心 2602–2642。这是用户指定的局部分解规则，不把一条同周期线段宣称为原文严格递归的次级别走势类型。
