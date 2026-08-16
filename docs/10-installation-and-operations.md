# 10 安装、运行与运维

## 1. 支持环境

- Windows 10/11 x64。
- Go 1.25.7。
- Python 3.14.x；当前固定解释器为
  `D:\ProgramData\anaconda3\envs\pydev3.14\python.exe`（Python 3.14.4）。
- Node.js 22.23.2 与 npm 10.9.8。

版本不匹配会由启动脚本和进程入口明确失败，不会降级运行。

## 2. 首次安装

在仓库根目录执行：

~~~powershell
D:\ProgramData\anaconda3\envs\pydev3.14\python.exe -m pip install -r python/requirements-dev.lock
Push-Location web
npm ci
Pop-Location
./scripts/check-versions.ps1
./scripts/test-all.ps1
~~~

复制 `config/app.yaml` 作为本机配置，至少确认：

- `storage.data_root` 是专用数据目录，不是仓库根、用户主目录或磁盘根目录。
- `server.listen` 和 `python_engine.base_url` 仅使用回环地址。
- `data_root/config` 可由扫描器自动创建；首次发现本地未配置且没有内置规则的品种时，需要访问公开
  期货交易参数与交易日序列。网络或远程页面不可用时，源文件保持待映射并显示具体失败原因。
- 原始通达信 TXT 放在 `data_root/history`，程序不会原地修改它。

## 3. 启动

最简单的方式是双击仓库根目录的 `启动程序.cmd`，或执行：

~~~powershell
./scripts/start-tvbt.ps1
~~~

该入口会检查固定版本与 Python 依赖，确认 `trading-data/history` 中存在初始标的行情并准备交易日历，
构建 Go，启动 Python/Go/Vue，扫描并导入该唯一数据源中的行情，然后打开 `http://127.0.0.1:5173/`。前端自动选择第一个
就绪数据集并显示 K 线；打开底部“回测”页签，点击“开始正式回测”即可执行。运行窗口中按
Ctrl+C 会按精确进程树停止三个服务。启动诊断输出位于 `bin/runtime`。

开发环境可运行：

~~~powershell
./scripts/start-all.ps1
~~~

在 VS Code 中运行 `Debug all services (fast)` 或 `Debug all services (checked)` 时，Vite 会在
Go 的统一健康检查确认 Go/Python 均为 `ok` 后检查 catalog。若尚无就绪数据集，compound 的
启动前任务确认唯一历史数据源中的初始标的文件，随后 Vite 开发插件通过 Go 公共 API 扫描并导入该行情；
已有数据集时不扫描、不导入、不覆盖。首个数据集就绪后输出全栈就绪标记，VS Code 随后调用
系统默认浏览器打开 `http://127.0.0.1:5173/`；浏览器未运行时会启动，已运行时会新开标签页。

也可分别使用 `start-python-checked.ps1`、`start-go-checked.ps1` 和
`start-web-checked.ps1`。Python 内部 API 仅监听 127.0.0.1；浏览器只访问 Go 的
`/api/v1`，不得直接访问 Python 或数据文件。

## 4. 数据目录与备份

建议备份：

- `history`：原始只读数据；
- `config`：品种、时段和交易日历；
- `catalog` 与 `normalized`：数据身份和标准化结果；
- `runs`：不可覆盖的正式回测事实；
- `studies`：不可覆盖的优化请求、Evaluation 排名与稳定性报告；
- `workspaces`：布局和用户绘图；
- `tasks/jobs`：任务终态和重启诊断。

`cache` 可重建，不应替代 `runs` 备份。复制备份时先停止 Go 与 Python，或使用支持一致性
快照的文件系统；恢复时保持目录层级和 `_SUCCESS` 文件。不要只恢复 Parquet 而丢失其
manifest、meta 或 revision 身份。

## 5. 中断与重启

Go 在每次任务状态变化时原子更新 `tasks/jobs/{job_id}.json`。进程重启后：

- queued、running、cancelling 转为 `interrupted`，错误码为 `PROCESS_RESTARTED`；
- completed、failed、cancelled 保持原终态；
- 超过 `storage.tmp_retention_hours` 的 `tmp/import-*` 及指标、缠论、回放、run 的已知
  `.tmp-` 目录，以及 studies 下的优化临时目录被移入 `trash/interrupted`；
- 未带 `_SUCCESS` 的 normalized、cache、run 或 study 仍不可见、不可当作完成结果。

客户端可通过 `GET /api/v1/jobs/{job_id}` 查询恢复结果。计算、回放、回测和优化任务会持久化
cache_key 或 run_signature，使专用状态接口在重启后仍可返回身份信息。中断任务不会自动
续跑；用户应在确认输入仍有效后重新提交，新任务拥有新 job_id 或 run_id。

## 6. 缓存清理

先预览：

~~~powershell
go run ./cmd/cachectl -config config/app.yaml -kind all -older-than 720h
~~~

确认输出中的 source 和 destination 后执行：

~~~powershell
go run ./cmd/cachectl -config config/app.yaml -kind all -older-than 720h -dry-run=false
~~~

`kind` 只能是 `indicators`、`chan`、`replay` 或 `all`。工具不会处理 runs、studies、normalized、
history、workspaces 或未知名称，也不跟随符号链接。移动结果位于 `trash/cache/<UTC时间>/`；
需要恢复时应先停止服务，再把目标目录原子移回原 source，且不得覆盖已有目录。

## 7. 日志与诊断

三端固定文本日志位于 `data_root/logs`，活动文件最大 50 MiB，最多 9 个压缩备份。正式回测的
`runs/{run_id}/log.ndjson` 另行保留状态变化、阶段信号、交易信号、订单、成交及其关联 ID，
不受滚动日志替代。

复现实机性能和生成 Go CPU profile：

~~~powershell
./scripts/benchmark-milestone8.ps1
./scripts/benchmark-milestone8.ps1 -CpuProfile
go tool pprof bin/marketdata.cpu.pprof
~~~

2026-08-01 验收机为 Windows 10.0.26200、Intel i5-13400、16 逻辑处理器：热缓存 3000 根
Go 读取基准约 1.91–2.01 ms/op，结构化日志写入 discard 约 4.60–4.84 μs/op；完整样例
HTTP 热读取 p95 由 smoke 脚本强制不超过 200 ms。10,000 个缠论语义对象和 25,000 个
因果事件由 Vue 性能测试覆盖。

## 8. 完整验收

~~~powershell
./scripts/accept-milestone9.ps1
~~~

该入口依次运行全部 Go/Python/Vue/契约门禁、17,017 根样例跨进程 E2E、重启恢复、可恢复
缓存清理、正式优化 Study 和性能基准。smoke 使用隔离的临时 data_root，结束时只删除已解析并确认位于系统
临时目录下的测试目录。

## 9. 已知限制

- 首期仅支持文件存储、轮询任务；没有数据库、Redis、消息队列或 WebSocket。
- 中断任务不能跨进程续算，只能被明确标记后重新提交。
- 缠论计算分型、笔、段和笔中枢；详细参考算法见 `docs/13-chan-bi-center-segment-algorithm.md`。
- 回测为单向净持仓模型；示例策略不产生止损/止盈同根冲突。
- 优化首版只提供 grid 与 seeded random；尚未提供 walk-forward、成本敏感性或 AI 选参。
- 服务面向本机单用户，不应把 Go 或 Python 监听地址暴露到公网。
