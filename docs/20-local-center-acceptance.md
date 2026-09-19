# 里程碑 15D 验收记录

状态：已完成。

## 已验证的真实成交

数据集 `SHFE.AOL9.5m`，revision `sha256:2904362e62173a418d63feaf8855a3aef4b61ce5b0027e7801785baf9d8302fe`。
保留 run `job-20260913T022904000000000-f841eea6ab73c66bf8af`，成交 `TRADE-5101-5103`。

| 事实 | 权威记录 |
|---|---|
| 来源中枢 | `segment-zhongshu-7f07eec9ff00be0036cb`，K3479–K4784，ZD=2926 / ZG=2970 |
| 向上离开段 | `segment-c2867c32f736f1af78c3`，K4861–K4944，3049→3153 |
| 首次回试段 | `segment-f00aa7d337047c60c958`，K4944–K5017，3153→3085 |
| 回试结束 | 2023/09/06 09:50（Asia/Shanghai），实际低点 3085，较 ZG 高 115 |
| 确认可知 | K5100，2023/09/07 01:00，成交量 1153 ≥ 门槛 0 |
| 开仓成交 | K5101，2023/09/07 09:05，成交价 3103（下一根开盘加一跳滑点） |

以上来自保留 run 的因果事件，不是由当前图形倒推。局部实体中枢 profile 与该 run 的标准三买来源中枢不同，不得用当前局部矩形改写历史交易含义。

## 可重复验收

先启动三端，然后执行：

```powershell
./scripts/accept-milestone15.ps1
$env:TVBT_E2E_BASE_URL='http://127.0.0.1:5173'
$env:TVBT_MILESTONE15_REAL_DATA='1'
cd web
npm exec playwright test e2e/milestone15-third-buy-evidence.spec.ts
```

生成 `trading-data/acceptance/milestone15-aol9-center-boundary.json`，包含 288 个目标附近笔/线段单元的起止、完整区间和角色解释，以及中心、连接和审计事件。
浏览器截图为 `trading-data/acceptance/milestone15-aol9-third-buy-evidence.png`。
历史 run 的目录身份只在视觉测试中固定；公共 bars、run 和事件接口均读取真实文件，生产环境的旧算法隔离不放宽。

目标区间已验证 K5100 的 23369 条事件与后续 K5200 的事件前缀一致；真实 JSON 检查点续算事件和规范化终态表一致；同流闭合及活动主体不交叠。
全量门禁（2026-09-15 23:07）：24 个契约样例、Go test/vet、Python 302 项测试/ruff/mypy、Vue 134 项测试/typecheck/build 均通过。

规范第 10 节的关系字段已接入生产中心事件及 Parquet：`previous_center_id`、`core_relation`、`higher_level_review_required`、`trend_status=UNVERIFIED`。按同流相邻主体排序，核心分离且 observed 外包络接触时要求高级别检查；不创建父中心、不改变标准交易信号。生产引擎测试覆盖首中心空关系、后中心核心上移及外围触及，并校验 Parquet 往返；真实 K5200 终态关系以独立比较门禁复核。对象树中文解释通过组件测试；指标设置区公开严格不等式、触边算返回及与旧标准三类点的区别，枚举请求值保持不变。

2026-09-15：源码哈希 `8dedbc417c3716e2a444dbca47abf9e4ba9428c05ae3041cebdc54a22bdf1955` 的全历史任务完成，缓存 `cache/chan/233b5298552821067ac3bc401e031b4ba111965f8555d185d59dcc7bcc632576`。71149 根 K 线产出 408 个 BI 局部中心、57 个 SEGMENT 局部中心；全历史种子固定核心、闭合 body_end 公式、严格回试分界及同流主体不交叠检查通过。Node 缓存契约校验与 Python 完整缓存校验通过（366942 个事件、69 个检查点）。此证据仅证明上述边界门禁，不证明未确认预览和高级别关系输出已实现。

## 最终验收

- 最终源码身份 `sha256:16303b7324a88246392baef9fcacf4e65e1407c437b88ba8d21c923afc433556`；完整缓存 `cache/chan/1106c49b78afab7625c014e6d0a648f0a66faf1339ec20da58c1e29d60120f5a`。71149 根 K 线计算完成，峰值私有内存 888.09 MiB；缓存契约、69 个检查点和 377920 个因果事件校验通过。
- 全历史共有 408 个 BI 局部中枢、57 个 SEGMENT 局部中枢、463 条连接和 3558 个确认结构单元。逐单元解释表、全部中心与连接保存在 `trading-data/acceptance/milestone15-aol9-center-boundary.json`；固定种子核心、body_end、严格紧邻回试、扫描重启、种子非复用、连接完整顺序、角色重合、核心关系及同流不交叠均由独立门禁通过。
- 截图所涉 2026-08-19—28 日不再混画滚动候选实体：BI 实体依次为 8 月 18 日 21:15—20 日 10:35、21 日 11:00—25 日 09:30、25 日 14:00—26 日 11:30、26 日 13:55—27 日 09:55；27 日 09:55 后仅有一个活动/候选实体。共同端点按 `[start,end)` 裁剪，不横向挪动数据。
- tick=5 回归确认 SEARCH_RESTARTED 保存单元索引而非价格；第三种子兼任离开时，连接持续保留 `roles_overlap_seed`。扫描起点在 UI 明示为单元索引；悬停分别显示前向/后向连接、形成确认和分界确认。
- 公共 API 真实回放通过，K5100/K5200 未来信息门禁、检查点续算和 425 次预览 upsert/163 次撤下均通过；所有 PREVIEW_UPDATED 均为不可交易事实。Chromium E2E 验证来源中枢、离开段、首次回试终点、成交居中和 K5173 触边预览，两个截图已人工复核。
- 原始 TXT、完成 run 和旧缓存均未覆盖。严格递归的高级别走势类型、DD/GG 和父中枢仍按规范第 1、10 节属于独立模块；当前只输出 `higher_level_review_required` 与 `trend_status=UNVERIFIED`，不冒充已验证趋势。
