# 06 API 与跨进程契约

OpenAPI 权威入口：contracts/openapi.yaml。JSON Schema 权威入口：contracts/schemas。

## 1. 公共 API 规则

- 前缀：/api/v1。
- 内容类型：application/json。
- 字段：snake_case。
- 时间点：UTC 毫秒；审计时间可用 RFC 3339。
- 分页：K 线用 bar_index 游标，表格用 opaque cursor。
- 每个响应含 request_id；链路含 trace_id。
- data_revision 为数据读取的乐观一致性令牌。

## 2. 数据集 API

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | /api/v1/health | Go 和 Python 健康状态 |
| POST | /api/v1/datasets/scan | 扫描 history |
| GET | /api/v1/source-files | 查看识别/待修正文件 |
| POST | /api/v1/datasets/import | 创建导入任务 |
| GET | /api/v1/datasets | 数据集目录 |
| GET | /api/v1/datasets/{dataset_id} | 元数据 |
| GET | /api/v1/datasets/{dataset_id}/bars | 初始或向左加载 K 线 |

所有长任务统一通过 `GET /api/v1/jobs/{job_id}` 查询状态，通过
`POST /api/v1/jobs/{job_id}/cancel` 请求协作式取消；数据集扫描和导入返回的 `job_id`
也使用这一入口，不要求前端根据任务类型拼接不同状态路径。

K 线查询：

- revision：必填，避免混用修订。
- tail：首次尾部数量，与 before_bar_index 二选一。
- before_bar_index：向左加载时使用，返回严格更早的数据。
- limit：默认 1500，最大 5000。

紧凑响应：

~~~json
{
  "dataset_id": "SHFE.AO2609.5m",
  "data_revision": "sha256:...",
  "generation_id": "gen-42",
  "price_scale": 1,
  "coverage": {"first_bar_index": 9000, "last_bar_index": 10499},
  "has_more_before": true,
  "bars": {
    "bar_index": [9000],
    "timestamp_utc": [1785502800000],
    "open_i64": [2351],
    "high_i64": [2360],
    "low_i64": [2348],
    "close_i64": [2358],
    "volume": [123],
    "open_interest": [456]
  }
}
~~~

各列长度必须相同。Vue 在接收边界校验后才写入图表缓存。

## 3. 计算 API

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | /api/v1/algorithms | 指标、缠论和策略定义 |
| POST | /api/v1/calculations | 创建或命中计算 |
| GET | /api/v1/calculations/{job_id} | 查询状态 |
| POST | /api/v1/calculations/{job_id}/cancel | 取消 |
| GET | /api/v1/calculations/{job_id}/results | 按范围读取结果 |

创建请求必须包含 dataset_id、data_revision、algorithm_ref、参数和 calculation_mode。

状态枚举：

- queued。
- running。
- cancelling。
- completed。
- failed。
- cancelled。
- interrupted。

结果响应必须返回 result_revision 和 data_revision。

## 4. 回放与回测 API

| 方法 | 路径 | 作用 |
|---|---|---|
| POST | /api/v1/replays | 创建/复用回放事件 |
| GET | /api/v1/replays/{replay_id} | 状态与元数据 |
| GET | /api/v1/replays/{replay_id}/events | 按 known_at 范围查询 |
| POST | /api/v1/backtests | 创建 run |
| GET | /api/v1/backtests/{run_id} | 状态与 manifest |
| POST | /api/v1/backtests/{run_id}/cancel | 取消 |
| GET | /api/v1/backtests/{run_id}/summary | 汇总 |
| GET | /api/v1/backtests/{run_id}/trades | 交易分页 |
| GET | /api/v1/backtests/{run_id}/equity | 权益区间 |
| GET | /api/v1/backtests/{run_id}/chart-events | 图上事件 |

POST /backtests 每次创建新 run_id，即使参数相同；响应可同时给出相同 run_signature，供用户判断重复。

`BacktestRequest` 和 `StudyRequest` 可携带同构的 `risk_overlay`，其中包含已发布
`kind=risk_filter` AlgorithmRef、完整显式参数和点时 `RiskContext`。context 保存市场状态
revision、板块 ID、合法/已处理未来分支以及严格按 `effective_from_bar_index` 排序的 observation；
每条 observation 的 `available_at_bar_index` 不得晚于生效 K 线。该字段可选是为了兼容旧调用，
不是允许启用时省略阈值；当前 Vue 的正式回测与 Study 默认启用。规范化后的全部事实参与
run/study 签名并写入对应 manifest。

## 5. 工作区 API

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | /api/v1/workspaces/{profile_id}/layouts/{layout_id} | 读取布局 |
| PUT | /api/v1/workspaces/{profile_id}/layouts/{layout_id} | 带 revision 保存 |
| GET | /api/v1/workspaces/{profile_id}/strategy-source-config | 读取独立的 StrategySource 动态显示配置 |
| PUT | /api/v1/workspaces/{profile_id}/strategy-source-config | 带 revision 保存独立动态配置 |
| GET | /api/v1/workspaces/{profile_id}/drawings/{layout_id}/{dataset_id} | 读取绘图 |
| PUT | /api/v1/workspaces/{profile_id}/drawings/{layout_id}/{dataset_id} | 带 revision 保存 |

冲突返回 409 和当前 revision。首期 UI 提示用户重新加载；不得静默覆盖。

## 6. 日志 API

POST /api/v1/client-logs：

- 每批最多 100 条。
- 请求体上限建议 256 KiB。
- 单条 message 和 fields 有长度限制。
- Go 覆盖 service 为 vue-client，并补 received_at。
- 非法条目丢弃并记录摘要，不回显敏感内容。
- 日志上传失败不触发无限重试。

## 7. Go 到 Python 内部 API

仅监听本机回环地址：

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | /internal/v1/health | Python 健康检查 |
| GET | /internal/v1/algorithms | 算法清单与参数 Schema |
| POST | /internal/v1/job-submissions/calculation | 指标/缠论计算 |
| POST | /internal/v1/job-submissions/replay | 回放事件 |
| POST | /internal/v1/job-submissions/backtest | 回测 |
| GET | /internal/v1/jobs/{job_id} | 状态 |
| POST | /internal/v1/jobs/{job_id}/cancel | 取消 |

内部请求只传：

- request_id、trace_id、job_id/run_id。
- DatasetRef。
- AlgorithmRef 和参数。
- 输出目录相对路径。
- 范围、预热、引擎配置。

`aux_ma_sector_rotation` 的公共 `BacktestRequest` 额外携带不含路径的 `ranking_context`：点时
universe/membership revision、统一复权模式/revision、episode 起点/可用时间及成员有效区间。
Go 必须逐成员核对活动 catalog revision、日线元数据和路径边界，再在内部 backtest 载荷中增加
按 dataset_id 排序的 `ranking_datasets`（显式 DatasetRef）；Python 不得自行扫描目录。相同公共
上下文写入正式 `run.json` 并参与 `run_signature`，内部路径不进入公共 manifest。

公共 `risk_overlay` 经 Go 校验 AlgorithmRef、参数 Schema、revision、观察时点和无杠杆审批后，
原样作为不含路径的内部事实交给 Python。Python 在逐 K 线执行入口产生
`risk_decisions.parquet`，并把 `approved_order_intent/reduced_order_intent/blocked_decision/kill_switch`
作为 `object_type=risk_decision` 因果事件写入 `chart_events.parquet`；Go 不实现第二套风险算法。
Study 的每个训练/验证 run 必须携带同一覆盖事实，study manifest 也保存该配置。

Python 不返回完整结果表，只返回：

- 状态。
- 进度。
- 行数和覆盖范围。
- 相对结果路径。
- 校验和。
- 错误码。

## 8. 结果对象

缠论笔：

~~~json
{
  "id": "bi-1842",
  "start_bar_index": 100,
  "start_time": 1785502800000,
  "start_price_i64": 2351,
  "end_bar_index": 118,
  "end_time": 1785506400000,
  "end_price_i64": 2386,
  "direction": "up",
  "confirmed": true,
  "confirmed_at_bar_index": 122,
  "object_revision": 27
}
~~~

中枢：

~~~json
{
  "id": "zs-327",
  "start_time": 1785502800000,
  "end_time": 1785510000000,
  "zg_i64": 2380,
  "zd_i64": 2362,
  "confirmed": false,
  "known_at_bar_index": 130,
  "object_revision": 27
}
~~~

## 9. 请求幂等性

- scan：幂等。
- import：可带 idempotency_key；相同源修订复用任务或结果。
- calculation：按 cache_key 幂等。
- replay：按 cache_key 幂等。
- backtest：默认不幂等，每次新 run；相同 idempotency_key 的网络重试不得创建两个 run。
- workspace PUT：按 expected_revision 乐观并发。

## 10. 策略比较接口

- `POST /api/v1/strategy-comparisons`：创建单数据集、一个或多个策略的 comparison，立即返回 `comparison_id`。
- `GET /api/v1/strategy-comparisons`：读取已经原子提交的历史 comparison 摘要。
- `GET /api/v1/strategy-comparisons/{comparison_id}`：读取状态、进度、当前策略和失败数。
- `POST /api/v1/strategy-comparisons/{comparison_id}/cancel`：请求取消尚未完成的批次。
- `GET /api/v1/strategy-comparisons/{comparison_id}/results`：读取逐策略成功或失败结果及成功子 run 引用。

请求必须固定 dataset/revision、策略版本与完整参数、回测/预热区间、执行和资金成本口径、统一风险覆盖、随机种子和最少交易数。comparison 本身不携带大批 K 线；Python 通过 `data_root` 内已验证引用读取数据，结果写入 `comparisons/{comparison_id}`，子策略事实继续写入标准 `runs/{run_id}`。

## 11. 兼容性

- API 破坏性变更提升 /api/vN。
- 文件 Schema 使用 schema_version 并提供迁移器。
- 算法语义变化必须提升 algorithm_version，即使函数签名未变化。
- 前端必须忽略未知可选字段，但不得忽略未知枚举状态。
- Go 与 Python 启动握手校验 contract_version，不兼容时健康检查返回 degraded，拒绝创建任务。
