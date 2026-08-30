# 里程碑 13：实际可用性收口

## 范围

里程碑 13 只把现有单周期历史研究与回测产品收口到可重复启动、可解释执行、可发布验收的状态，不扩展交易域边界。

- 13A：任务恢复与一键启动性能。
- 13B：真实执行参数统一、规范化和版本化。
- 13C：浏览器 E2E、CI 发布门禁和运维文档。

明确排除多级别/多周期、实时行情、模拟柜台、订单状态机、对账、人工审批、熔断、监控、影子运行、WebSocket 和实盘下单。

## 13A 验收

任务恢复：

- 启动时只把 `queued`、`running`、`cancelling` 转成 `interrupted/PROCESS_RESTARTED` 并原子落盘。
- `completed`、`failed`、`cancelled`、`interrupted` 的历史文件不得被启动过程重写。
- 重启后仍可通过原 `job_id` 查询终态或中断诊断。

一键启动：

- 首次或行情/日历变化时完整重建准备状态；未变化时校验 SHA-256 后跳过 7 万根级源文件的 PowerShell 全量解析和日历重写。
- Go 输入未变化且已有 `bin/chartd.exe` 时跳过重建；`-ForceBuild` 可显式强制重建。
- 快路径准备和构建检查在验收机上分别不超过 1,500 ms。
- Python、Go、Vue 仍需完成健康检查，数据扫描/必要导入和精确进程树清理。

执行：

~~~powershell
./scripts/accept-milestone13a.ps1
./scripts/start-tvbt.ps1 -NoBrowser -HoldSeconds 1
~~~

2026-08-30 本机基线中，未缓存日历准备为 4,703.93 ms，校验后快路径为 18.04 ms；Go 重建为 7,075.42 ms，输入未变化的构建检查为 0.01 ms。数值是环境证据，不改变 1,500 ms 验收上限。

本次正式验收中，`accept-milestone13a.ps1` 报告日历快路径 15.44 ms、构建检查 17.73 ms；`test-all.ps1` 通过 Go 全部测试与 vet、233 个 Python 测试及格式/类型检查、24 个契约示例、30 个 Vue 测试文件共 110 个测试、Vue 类型检查和生产构建。公共契约未修改，未生成新的样例行情。真实一键入口的端口冲突前置门禁已验证；当时 5173/8080/8091 由健康的现有调试实例占用，因此未终止用户实例来重复启动第二套服务。

## 13B 验收

- `ExecutionRequest` 固定 `semantic_version=1.0.0`，严格区分固定每手/比例手续费、tick/bps 滑点、撮合时点、保证金、同根冲突和压力事实；资金币种、定点金额及 money scale 同时规范化。
- Vue 的回测、优化、策略比较共用手续费 300 money-i64（scale 100）、1 tick 滑点、保证金率 0.12；单数据集显示并断言 API 元数据中的品种乘数。多数据集研究不再发送虚假的乘数 1、手续费 0 和保证金率 0.1。
- Go 是执行事实边界：从数据 revision 已包含哈希的 `instruments.json` 解析权威乘数，补齐 baseline、成本倍数、额外滑点/延迟和 unlimited 撮合默认值；浏览器断言不一致、未知字段、未知语义版本或 money scale 不一致均拒绝。
- Research Study 为每个 dataset 保存独立 resolved execution；走步、压力场景、研究签名和正式子 run 均消费该 dataset 的乘数。顶层 manifest 只保存 `per_dataset_instrument_config` 策略，不伪造一个跨品种数值。
- Python 只接受 `semantic_version=1.0.0` 且 `contract_multiplier_source=instrument_config` 的内部执行事实。正式 run/study/comparison manifest 保存规范值；结果页展示版本、来源和乘数，旧 manifest 明确标为未版本化并保持不可覆盖。

执行：

~~~powershell
./scripts/accept-milestone13b.ps1
./scripts/test-all.ps1
~~~

2026-08-30 本机正式验收：13B 专项脚本通过 24 个契约样例、Go 执行/比较/优化/研究/导入/API 测试、22 个 Python 专项测试和 5 个 Vue 文件共 24 项测试；全量门禁通过 Go test/vet/格式、234 个 Python 测试及 Ruff/mypy、31 个 Vue 文件共 113 项测试、类型检查和生产构建。未生成或修改样例行情。公共契约增加执行语义版本和数据集 `contract_multiplier`；已知限制是旧 run 没有版本化执行事实，只按原 manifest 展示，绝不自动迁移或覆盖。

## 13C 验收

- Playwright 1.62.1 使用隔离端口 `15173/18080/18091` 启动真实 Vue、Go、Python，以脚本生成的 1,350 根 AOL9 K 线完成自动选择、图表可见、正式回测、轮询终态和页面刷新后恢复同一 `run_id`。测试不使用 API mock，不读写正式 `trading-data`。
- 最近正式回测以 `dataset_id + data_revision + algorithm_id + run_id + run_signature` 保存在浏览器本地。恢复时必须由 Go 返回相同签名、数据 revision 和策略身份；恢复仍读取不可变 run 的 summary、trades、equity 和 chart events，不复制或改写正式结果。
- 应用内浏览器复核得到 `MA20 Retest Failure Short` 的已完成结果：67 笔交易、执行语义 v1.0.0、合约乘数 20，刷新后恢复同一 `job-20260830T133611000000000-20262bc0a8decb15d51c`。
- GitHub Actions 的基础 job 继续覆盖精确版本、Go/Python/Vue 格式、静态检查、单元/组件、契约生成和生产构建；后续 Windows job 安装锁定 Chromium，执行真实全栈 E2E，再构建并冒烟发布 ZIP。
- `build-release.ps1` 生成不含行情和 Parquet 的 `tvbt-0.1.0-windows-x64.zip` 与 SHA-256 manifest。`smoke-release.ps1` 从解压目录校验文件身份、拒绝数据混入，实际启动打包后的 Python、Go API 和生产 Web UI，并在有 13C 隔离夹具时通过发布启动器完成扫描、导入和 AOL9 自动就绪。

执行：

~~~powershell
./scripts/accept-milestone13c.ps1
$release = (./scripts/build-release.ps1 | Select-Object -Last 1) | ConvertFrom-Json
./scripts/smoke-release.ps1 -Archive $release.archive
./scripts/test-all.ps1
~~~

2026-08-30 本机专项验收：Chrome 全栈 E2E 1/1 通过（复跑 7.7 秒），发布包 manifest、数据排除、Python/Go/Web 运行健康检查及 1,350 根 AOL9 发布启动器导入通过。每次构建输出最终 ZIP 的 SHA-256，CI 同时保存发布 artifact。公共 API 契约未因 13C 改动；新增的本地恢复记录不是正式事实来源。可重复样例由验收脚本在 `bin/e2e-runtime` 生成并在下一轮覆盖，不提交或替代真实历史行情。

最终全量门禁通过 Go test/vet/格式、234 个 Python 测试及 Ruff/mypy、24 个契约样例、31 个 Vue 文件共 114 项测试、类型检查和生产构建；`npm audit` 为 0 个已知漏洞。发布 ZIP 的最终 SHA-256 由构建脚本输出并随 CI artifact 记录。
