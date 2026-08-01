# 仿 TradingView 历史行情与回测系统规范包

版本：1.0  
整理日期：2026-08-01  
状态：里程碑 0–9 已实现并通过自动化验收

## 1. 项目一句话定义

这是一个面向期货历史数据的类 TradingView Web 应用：Vue 3 负责图表与交互，Go 1.25.7 负责文件数据、目录索引、区间查询、任务编排和统一 API，Python 3.14 负责指标、缠论、回放、策略与回测。

第一阶段只做：

- 历史 K 线浏览与无限向左加载。
- 普通指标与缠论指标计算。
- 多窗格图表、画图工具、对象树和布局保存。
- 无未来函数的逐 K 线回放。
- 策略回测、结果查看与结果留档。
- 显式参数空间的 grid/seeded random 训练—验证优化与稳定性报告。
- Go、Python、Vue 3 的统一结构化日志。

第一阶段明确不做：

- 实时行情。
- 实盘下单。
- 数据库。
- WebSocket。
- 多用户登录与云同步。
- AI 自动选择策略、参数或优化目标；标准调优接口仅执行显式的 grid 或 seeded random Study。

## 2. 固定技术边界

| 层 | 固定选择 |
|---|---|
| Go | go1.25.7 |
| Python | Python 3.14 |
| Web | Vue 3、Composition API、TypeScript、Vite |
| 图表 | TradingView Lightweight Charts 5.x 系列；生成项目时锁定一个已验证的精确版本 |
| 浏览器到 Go | HTTP/JSON；长任务轮询 |
| Go 到 Python | 仅本机回环地址上的内部 HTTP/JSON |
| 大数据交换 | 共享 Parquet 文件；调用消息只传数据集引用、参数和任务 ID |
| 文件格式 | 原始 TXT、Parquet、JSON、NDJSON |
| 数据库 | 不使用 |

任何依赖都必须写入锁文件。不得使用未锁定的 latest 作为可重复构建依据。

## 3. 阅读顺序

Codex 开工前必须按以下顺序阅读：

1. AGENTS.md
2. DECISIONS.md
3. docs/01-scope-and-domain.md
4. docs/02-architecture-and-data-flow.md
5. docs/03-data-source-and-storage.md
6. docs/04-ui-layout-and-layers.md
7. docs/05-indicators-strategies-replay-backtest.md
8. docs/06-api-contracts.md
9. docs/07-logging-and-observability.md
10. docs/08-testing-and-acceptance.md
11. docs/09-implementation-roadmap.md
12. contracts/openapi.yaml 和 contracts/schemas

## 4. 规范包内容

~~~text
.
├── AGENTS.md
├── CODEX_BOOTSTRAP.md
├── DECISIONS.md
├── README.md
├── config/examples/
├── contracts/
│   ├── openapi.yaml
│   ├── examples/
│   └── schemas/
├── docs/
├── examples/
└── samples/                         # 原样只读的通达信测试数据
~~~

本包是规范，不含业务实现代码。实际项目建议采用：

~~~text
project/
├── cmd/chartd/                  # Go 主程序
├── internal/                    # Go 业务模块
├── web/                         # Vue 3
├── python/                      # Python 计算服务
├── contracts/                   # 跨进程契约
├── config/                      # 程序配置
├── scripts/
├── tests/
└── AGENTS.md
~~~

运行数据放在可配置的 data_root 中，不提交到源码仓库。

## 5. 最重要的五条原则

1. 原始历史文件只读，所有派生物可追溯到 data_revision。
2. Vue 只与 Go 通信，Python 不直接向 Vue 提供接口。
3. 可视范围变化只重绘；缓存范围变化才取数；输入或算法变化才重算。
4. 回放与回测共用同一套逐 K 线因果引擎，任何信号都记录 known_at_bar_index。
5. 滚动日志用于排错，runs 下的正式结果用于审计；两者不可互相替代。

## 6. 首个 Codex 任务

建议先执行 CODEX_BOOTSTRAP.md 中的“里程碑 0”，只生成工程骨架、契约、配置加载、健康检查和三端日志，不要一开始同时实现图表、缠论和回测。

## 7. 已锁定的工程版本

里程碑 0 锁定以下非业务版本：

| 工具或依赖 | 精确版本 |
|---|---:|
| Go | 1.25.7 |
| Python | 3.14.x（当前配置为 Anaconda 环境 Python 3.14.4） |
| Node.js / npm | 22.23.2 / 10.9.8 |
| Vue / Vue Router | 3.5.40 / 5.2.0 |
| Vite / TypeScript / vue-tsc | 8.2.0 / 5.9.3 / 3.3.9 |
| Vitest / Vue Test Utils / jsdom | 4.1.10 / 2.4.11 / 30.0.1 |
| Go YAML / lumberjack | 3.0.1 / 2.2.1 |
| Parquet Go / Go text encoding | 0.30.1 / 0.40.0 |
| PyArrow | 25.0.0 |
| Lightweight Charts | 5.2.0 |

Go 依赖由 `go.mod` 和 `go.sum` 锁定；Python 运行时与开发依赖分别由
`python/requirements.lock` 和 `python/requirements-dev.lock` 锁定；前端依赖由
`web/package-lock.json` 锁定。TypeScript 保持在 5.9.3，因为契约类型生成器当前声明的兼容范围为 TypeScript 5.x。

## 8. 里程碑 0 开发命令

当前固定 Python 解释器为
`D:\ProgramData\anaconda3\envs\pydev3.14\python.exe`。首次准备环境：

~~~powershell
D:\ProgramData\anaconda3\envs\pydev3.14\python.exe -m pip install -r python/requirements-dev.lock
cd web
npm ci
~~~

从仓库根目录运行：

~~~powershell
# 一键准备样例、启动三端、导入行情并打开浏览器
./scripts/start-tvbt.ps1
# 也可直接双击仓库根目录的“启动程序.cmd”

# 检查固定运行时版本
./scripts/check-versions.ps1

# 不先执行全量构建，快速启动三端；Ctrl+C 按精确进程树停止
./scripts/start-all.ps1

# 单端通过完整门禁后启动
./scripts/start-go-checked.ps1
./scripts/start-python-checked.ps1
./scripts/start-web-checked.ps1

# 重新生成 Go/TypeScript 契约类型并校验 OpenAPI 和示例
./scripts/generate-contracts.ps1

# 运行里程碑 0 全部门禁
./scripts/test-all.ps1

# 实际启动三端并检查 Go → Python 健康链路与 Vue 日志上传
./scripts/smoke-milestone0.ps1
~~~

一键入口只在缺失时复制 `samples/30#AO2609.txt` 和运行配置，不覆盖已有历史文件；随后扫描、
导入并让前端自动选择第一个就绪数据集，因此页面打开后直接显示 K 线。底部选择“回测”并点击
“开始正式回测”即可完成回测。Ctrl+C 会停止本次启动的精确进程树。

VS Code 提供“Debug all services (fast)”和“Debug all services (checked)”两个复合启动项。
Go、Python、Vite 的调试输出进入 Debug Console；构建门禁任务使用 Terminal。Vite 会等待 Go 的
统一健康检查确认 Go/Python 均已就绪；若 catalog 为空，调试启动项只准备并通过 Go 扫描、导入
AO2609 样例。首个数据集就绪后，VS Code 调用系统默认浏览器打开 K 线页面；浏览器尚未运行时
会启动浏览器，已经运行时会再次打开一个新标签页。

## 9. 里程碑 1：历史数据导入

将只读通达信 TXT 放入 `storage.data_root/history`，并将
`config/examples/instruments.json`、`sessions.json` 和按实际交易日补全的
`trading_calendar.csv` 复制到 `storage.data_root/config`。前端数据集面板可发起扫描、轮询导入任务并查看数据集元数据；公共接口为：

- `POST /api/v1/datasets/scan`
- `GET /api/v1/source-files`
- `POST /api/v1/datasets/import`
- `GET /api/v1/jobs/{job_id}` 与取消接口
- `GET /api/v1/datasets`、`GET /api/v1/datasets/{dataset_id}?revision=...`

标准化结果写入 `normalized/{dataset_id}/{revision_short}/`。其中包含
`bars.parquet`、`quality.json`、`meta.json`，且仅在前三者全部提交后最后写入
`_SUCCESS`；没有 `_SUCCESS` 的目录不会进入可用数据集列表。原始 TXT 不会被修改。

从仓库根目录执行里程碑 1 的真实 API 验收：

~~~powershell
./scripts/smoke-milestone1.ps1
~~~

该脚本使用完整的 17,017 根样例，验证扫描、异步任务轮询、交易日历映射、
Parquet 写入、元数据 Schema、重复导入幂等性与原始文件哈希不变，并在结束后清理隔离的临时 data_root。

## 10. 里程碑 2：K 线与多窗格

K 线接口为 `GET /api/v1/datasets/{dataset_id}/bars`。`revision` 与
`generation_id` 必填；首次查询默认尾部 3000 根，向左查询使用
`before_bar_index` 并默认读取 1500 根，单次最多 5000 根。响应采用列式 JSON，
包含返回范围、`has_more_before` 和校验和；不同 revision 返回 409。

前端使用一个 Lightweight Charts 5.2.0 实例承载价格主图、MACD 占位副图和成交量副图。
三个 pane 共享时间轴、逻辑范围和垂直十字线，纵轴独立，默认比例 6:1:1；
分隔线仅调整相邻 pane，双击恢复默认。右侧和底部 dock 参与网格布局并压缩图表。
缩放和尺寸变化只重绘，只有接近左缓存边界时才向 Go 预取，不调用 Python。

执行完整样例的范围与性能验收：

~~~powershell
./scripts/smoke-milestone2.ps1
~~~

该脚本验证尾部 3000、前取 1500、revision 冲突和热缓存 HTTP p95 小于 200 ms。

## 11. 里程碑 3：普通指标

Python 发布不可变的 MA、MACD、ATR 定义与参数 Schema，读取 Go 指定的标准化
Parquet 并将完整历史结果写入 `cache/indicators/{cache_key}/`。目录包含
`values.parquet`、`manifest.json`，最后写 `_SUCCESS`。缓存键覆盖数据修订、算法种类、
ID、版本、source hash、规范参数、计算模式与引擎版本；相同键的并发请求只提交一次 Python 任务。

Vue 的指标面板创建、编辑和删除独立 `SeriesSource`。MA 叠加主图，MACD 与 ATR 使用
指标副图；可视范围变化只查询已完成缓存，不重新创建计算任务。公共接口为：

- `GET /api/v1/algorithms`
- `POST /api/v1/calculations`
- `GET /api/v1/calculations/{job_id}` 与取消接口
- `GET /api/v1/calculations/{job_id}/results?from_bar_index=...&to_bar_index=...`

执行完整 17,017 根样例的跨进程验收：

~~~powershell
./scripts/smoke-milestone3.ps1
~~~

该脚本验证三种指标均完成计算、范围结果与校验和有效、缓存目录符合 Schema，且等价 MA 请求立即命中缓存。

## 12. 里程碑 4：用户绘图、图层与工作区

图表支持趋势线、水平线、矩形、文本和测量工具。绘图锚点只保存 UTC 毫秒、
定点价格与价格缩放，不保存屏幕像素或当前数组下标；磁铁模式将锚点吸附到已缓存
K 线及其 OHLC。对象树可控制可见、锁定、重命名、顺序和删除，绘图历史支持撤销与
重做。锁定对象仍可选中，但控制点不可拖动。

布局和绘图分别通过以下公共接口读取与保存：

- `GET/PUT /api/v1/workspaces/{profile_id}/layouts/{layout_id}`
- `GET/PUT /api/v1/workspaces/{profile_id}/drawings/{layout_id}/{dataset_id}`

PUT 必须携带 `If-Match` 当前 revision；首次创建使用 0。Go 在 `data_root/workspaces`
内以同目录临时文件加原子重命名提交，过期 revision 返回 409 和当前 revision。布局保存
窗格权重、dock 尺寸、对象顺序及指标 `SeriesSource`，重启恢复时用原算法引用重新取得
不可变缓存。

执行完整的跨进程验收：

~~~powershell
./scripts/smoke-milestone4.ps1
~~~

该脚本同时验证导入、普通指标、工作区 Schema、定点绘图锚点、原子持久化、恢复与
乐观并发冲突。

## 13. 里程碑 5：缠论

Python 缠论引擎按 `docs/structures.py`、`docs/multilevel.py` 和
`docs/engineering.py` 的规则实现包含关系、严格分型、笔和中枢；按 C-015 不实现线段。
计算使用 `causal_events` 模式，所有对象携带稳定 ID、修订号、确认位置和
`known_at_bar_index`。中枢保留 confirmed、extended、left 生命周期及离开方向。

结果原子写入 `cache/chan/{cache_key}/`：`fractals.parquet`、`bi.parquet`、
`zhongshu.parquet`、`events.parquet`、版本化检查点、`manifest.json` 和最后提交的
`_SUCCESS`。Vue 将每个缠论算法实例作为一个 `StrategySource`，对象树只建立分型、笔、
中枢三个类别节点；图表使用单个批量 `ChanPrimitive`，范围变化只向 Go 查询既有缓存。

执行完整 17,017 根样例的跨进程和因果缓存验收：

~~~powershell
./scripts/smoke-milestone5.ps1
~~~

该脚本验证缠论任务、范围对象查询、事件序号和对象修订、检查点版本、缓存命中、
StrategySource 工作区恢复，并拒绝任何 `segments.parquet` 线段产物。前缀不变性、检查点
恢复一致性和 10,000 对象批量几何构建另由 Python/Vue 自动测试覆盖。

## 14. 里程碑 6：回放

回放通过 `POST /api/v1/replays` 创建或复用完整因果事件缓存，状态由
`GET /api/v1/replays/{replay_id}` 轮询，事件通过
`GET /api/v1/replays/{replay_id}/events` 按 `known_at_bar_index` 范围读取。
Python 从数据集起点逐 K 线累计到回放终点，原子写入 `cache/replay/{cache_key}/`；
Go 校验数据修订、范围、算法引用和所有 data_root 内文件引用。

Vue 首次载入事件后在浏览器内维护递增事件索引。单步、后退、0.25x–10x 播放、暂停和
跳转只移动 `replay_cursor`：未来 K 线不写入图表，晚于游标的 upsert/delete 不应用，
因此不会产生新的 Python 任务。当前请求、速度和游标按 dataset revision 保存，刷新后
通过同一不可变缓存恢复。

执行完整 17,017 根样例验收：

~~~powershell
./scripts/smoke-milestone6.ps1
~~~

该脚本验证事件缓存 Schema 与校验和、游标前无未来事件、后续事件按游标出现、等价请求
立即命中缓存，以及回放目录不混入分型、笔、中枢或线段终态文件。

## 15. 里程碑 7：策略与回测

Python 发布 `ma20_retest_short` 示例策略，按标准
`metadata/initialize/on_bar/finalize` 接口产生状态变化、阶段信号、交易信号和图表事件。
回放与回测调用同一 `run_strategy`，因此同一事实集的信号 ID、顺序和
`known_at_bar_index` 完全一致。

正式回测默认在当前 K 线收盘产生信号、下一根开盘成交；执行模型支持多空开平、单向净
持仓、合约乘数、保证金、固定或比例手续费，以及 tick 或 bps 滑点。最后一根信号明确以
`NO_NEXT_BAR` 拒绝。每次非重试请求创建新的 `runs/{run_id}`，完整事实表、汇总、日志和
`run.json` 提交后最后写 `_SUCCESS`；成功 run 不可覆盖。公共 API 提供状态、汇总、交易
分页、权益曲线和图表事件。

Vue 底部面板提供回测参数、汇总、交易表和权益曲线；策略回放的阶段/交易图形只在其
因果事件到达游标后绘制。

执行完整样例验收：

~~~powershell
./scripts/smoke-milestone7.ps1
~~~

该脚本验证下一根开盘成交、run manifest、全部正式事实文件、幂等重试、等价新 run 的
相同 signature 与事实哈希、成功目录不可覆盖，以及回放/回测交易信号逐条一致。

## 16. 里程碑 8：可靠性、性能与运维

Go 任务摘要持久化到 `data_root/tasks/jobs`。服务重启时，原先处于 queued、running 或
cancelling 的任务会在对外服务前原子更新为 `interrupted`，并保留回测 signature 或缓存键，
因此通用任务及计算、回放、回测状态接口仍可解释旧任务。超过
`storage.tmp_retention_hours` 的已知临时目录会在启动时移入
`data_root/trash/interrupted`，不会递归删除未知路径。

缓存清理工具默认 dry-run，只识别 indicators、chan 和 replay 下以 64 位小写十六进制
SHA-256 命名、带 `_SUCCESS` 的直接子目录；执行时移入 trash，支持恢复：

~~~powershell
# 预览 30 天前的全部正式缓存
go run ./cmd/cachectl -config config/app.yaml -kind all -older-than 720h

# 确认后实际移入 data_root/trash/cache
go run ./cmd/cachectl -config config/app.yaml -kind all -older-than 720h -dry-run=false
~~~

完整门禁、跨进程样例、恢复/清理和可重复性能基准分别执行：

~~~powershell
./scripts/accept-milestone8.ps1
# 可选生成 bin/marketdata.cpu.pprof
./scripts/benchmark-milestone8.ps1 -CpuProfile
~~~

安装、启动、备份、故障恢复和安全边界详见
`docs/10-installation-and-operations.md`。

## 17. 里程碑 9：参数优化接口

独立 Python 优化模块只通过标准回测入口执行候选参数，不读取 UI 窗口，也不修改或复制策略
源码。`SearchSpace` 支持类型、候选值或有限范围/步长；`Study` 显式记录有序主次目标、硬约束、
grid 或 seeded random、预算和随机种子。训练区间严格早于验证区间，每个候选分别生成两个不可
覆盖的正式 `runs/{run_id}`，Evaluation 保存两个 run 的 ID、signature、指标、约束状态和排名。

Study 原子提交到 `studies/{study_id}`，包含 `study.json`、`evaluations.json`、
`stability.json`、`log.ndjson` 与 `_SUCCESS`。稳定性报告给出所选候选的训练/验证排名、主目标
绝对差距、前三训练候选的验证表现、训练/验证排名相关性和约束可行数。公共 API 为：

- `POST /api/v1/studies`
- `GET /api/v1/studies/{study_id}`
- `POST /api/v1/studies/{study_id}/cancel`
- `GET /api/v1/studies/{study_id}/evaluations`

Vue 底部“优化”面板提供搜索空间、方法、预算、目标和最少交易次数，并显示训练/验证结果
对比。该接口不包含 AI 决策；调用方必须显式提供策略、参数空间、目标、约束和时间区间。

执行全仓门禁与完整样例验收：

~~~powershell
./scripts/accept-milestone9.ps1
~~~

验收覆盖 3 个候选、6 个正式训练/验证 run、固定种子可复现、Study Schema/原子提交、结果排名、
硬约束与稳定性报告。详情见 `docs/12-milestone9-acceptance-report.md`。
