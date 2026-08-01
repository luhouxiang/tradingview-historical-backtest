# 07 Go、Python、Vue 3 统一日志规范

## 1. 目标

能够用 trace_id、request_id、job_id/run_id、signal_id 把以下链路串起来：

> 数据发现 → 导入 → 标准化 → 区间读取 → Go/Python 任务 → 指标/策略状态 → 阶段信号 → 交易信号 → 回测结果 → Vue 绘制

## 2. 文件与轮转

~~~text
logs/
├── go/app.ndjson
├── python/strategy.ndjson
└── vue/client.ndjson
~~~

每个日志流：

- UTF-8 NDJSON，一行一个完整 JSON 对象。
- 活动文件最大 50 MiB，即 50 × 1024 × 1024 字节。
- backup_count = 9，活动文件加备份总数最多 10。
- 旧备份 gzip 压缩。
- 一个文件只允许一个进程负责轮转。
- 追加写；单条日志不得跨行。
- 文件写失败不得让主业务失败。

Go 写 go/app.ndjson；Python 写 python/strategy.ndjson；Vue 将日志上传给 Go，由 Go 写 vue/client.ndjson。

## 3. 统一字段

必填：

~~~json
{
  "timestamp": "2026-08-01T10:32:18.265+08:00",
  "level": "INFO",
  "service": "python-engine",
  "event": "strategy.stage_signal.generated",
  "message": "MA20反抽失败阶段信号生成",
  "source_file": "strategies/ma20_retest.py",
  "source_line": 238,
  "source_function": "evaluate"
}
~~~

按上下文增加：

- request_id。
- trace_id。
- job_id。
- run_id。
- replay_id。
- dataset_id。
- data_revision。
- algorithm_id。
- algorithm_version。
- strategy_id。
- strategy_version。
- cache_key。
- bar_index。
- bar_time。
- sequence。
- stage_signal_id。
- signal_id。
- parent_signal_id。
- duration_ms。

字段符合 contracts/schemas/log-event.schema.json。

## 4. 日志级别

| 级别 | 使用 |
|---|---|
| TRACE | 逐根 K 线输入和极细状态，默认关闭 |
| DEBUG | 批次范围、缓存判断、任务阶段、指标诊断 |
| INFO | 启停、任务开始/完成、状态变化、阶段信号、交易信号 |
| WARN | 缺口、乱序、无效但可恢复数据、慢任务、重试 |
| ERROR | 读取失败、算法异常、写入失败、契约不兼容 |

INFO 禁止记录完整 K 线或完整结果批次。

## 5. 必须记录的事件

### 5.1 Go

- app.started、app.stopped。
- source.scan.started/completed。
- source.file.discovered/rejected。
- dataset.import.started/completed/failed。
- dataset.quality.warning。
- data.range.requested/read/completed。
- api.request.started/completed。
- python.job.submitted/completed/failed/cancelled。
- workspace.loaded/saved/conflict。
- client_logs.batch.received/rejected。

### 5.2 Python

- engine.started、engine.stopped。
- algorithm.loaded/rejected。
- calculation.started/cache_hit/completed/failed。
- checkpoint.loaded/saved/rejected。
- strategy.state.changed。
- strategy.stage_signal.generated/updated/cancelled/confirmed。
- strategy.trade_signal.generated/filtered/transmitted。
- backtest.order.created/rejected。
- backtest.fill.generated。
- backtest.completed/failed/cancelled。

### 5.3 Vue 3

- app.started。
- chart.dataset.selected。
- chart.range.requested/received/discarded。
- chart.prefetch.triggered。
- chart.layout.resized。
- pane.added/removed/reordered/maximized。
- drawing.created/updated/deleted。
- object.visibility.changed/order.changed。
- calculation.requested/completed/failed。
- replay.started/paused/seeked/stopped。
- ui.error。

高频 mousemove、crosshair.move 和每帧 resize 不写 INFO；必要时采样 DEBUG。

## 6. 数据流摘要

~~~json
{
  "event": "data.batch.transferred",
  "from_service": "go",
  "to_service": "python",
  "first_bar_index": 10000,
  "last_bar_index": 11999,
  "bar_count": 2000,
  "payload_bytes": 184320,
  "checksum": "sha256:...",
  "duration_ms": 8.7
}
~~~

在共享文件模式下，payload_bytes 表示控制消息大小；另记 input_file_bytes。不得把 2000 根内容复制进日志。

## 7. 策略状态与信号

状态变化：

~~~json
{
  "event": "strategy.state.changed",
  "state_from": "waiting_retest",
  "state_to": "waiting_retest_failure",
  "reason_code": "PRICE_TOUCHED_MA20",
  "known_at_bar_index": 10542
}
~~~

交易信号：

~~~json
{
  "event": "strategy.trade_signal.generated",
  "signal_id": "SIG-10548-SHORT",
  "parent_stage_signal_id": "STAGE-10542-RETEST",
  "side": "short",
  "price_i64": 2350,
  "reason_code": "RETEST_FAILED_BREAK_OPEN",
  "known_at_bar_index": 10548
}
~~~

正式 Parquet 表是权威事实；日志是可读摘要。

## 8. 三端实现

### 8.1 Go

- 使用标准结构化日志接口，JSON Handler 启用调用源。
- 轮转 writer 设置 max_size_mb = 50、backup_count = 9、compress = true。
- logx 包装层必须调整调用栈深度，source 指向业务调用点。
- request middleware 生成/继承 request_id 和 trace_id。
- 所有 goroutine 从 context 取得链路字段。

测试必须断言 source_file 不是 logx 包装器。

### 8.2 Python 3.14

- 标准 logging + QueueHandler/QueueListener，避免计算线程被磁盘阻塞。
- RotatingFileHandler 或自定义兼容 handler，maxBytes = 50 MiB，backupCount = 9。
- 自定义 rotator 压缩已轮转文件。
- 使用 pathname、lineno、funcName。
- 包装器使用正确 stacklevel，让行号指向策略或引擎调用点。
- 未处理异常通过统一 hook 写 ERROR。

### 8.3 Vue 3

- 统一 logger.ts，业务代码不得直接 console.log。
- 开发时 console 输出可作为同一 logger 的附加 sink。
- 使用 Vite 源码转换插件或编译宏为 logger 调用注入 source_file、source_line、source_function。
- 生产 sourcemap 的访问受限，不把本机绝对路径上传。
- 内存队列达到 100 条或 1 秒即发送。
- 队列设上限；超限丢弃 DEBUG/TRACE 并生成一条 dropped_count 摘要。
- 页面隐藏/卸载时用 sendBeacon 尝试发送。

测试必须在构建后的客户端事件中检查真实 .vue/.ts 文件和非零行号。

## 9. 关联 ID

| ID | 生命周期 |
|---|---|
| trace_id | 一次用户操作跨 Vue、Go、Python |
| request_id | 一次 HTTP 请求 |
| job_id | 一次导入/计算任务 |
| run_id | 一次正式回测 |
| stage_signal_id | 一个阶段信号生命周期 |
| signal_id | 一个交易信号 |
| order_id | 一个模拟订单 |
| fill_id | 一个模拟成交 |

Go 收到 Vue 请求时保留合法 trace_id 或生成新值。Python 必须回传同一 trace_id。

## 10. 安全与容量

禁止记录：

- 原始文件完整内容。
- 完整 K 线批次。
- Authorization、Cookie、令牌。
- 不必要的绝对用户路径。
- 任意 Python 对象 repr 中的海量数组。

对参数只记录规范化、小体积值；大配置记录 hash 和安全摘要。

## 11. 日志查询

首期排错可使用文件与命令行。底部日志面板若提供查询：

- 只能按时间、级别、服务、event 和链路 ID 过滤。
- 设置返回条数上限。
- 不允许用户传任意文件路径。
- 不跨越已授权的日志目录。

## 12. 验收

- 三端任意业务日志均能定位到真实文件和行号。
- 人工生成超过 50 MiB 的测试流后发生滚动。
- 任一日志流总文件数不超过 10。
- 备份可解压且每行是合法 JSON。
- 同一次回测可用 run_id 串起 Go 与 Python，发起动作可追到 Vue trace_id。
- 断开日志目录写权限时，核心任务报告日志降级但继续运行。
- 阶段信号可追到交易信号、订单和成交。

