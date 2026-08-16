# 03 数据来源、标准化与文件存储

## 1. data_root

运行数据根目录由配置项 storage.data_root 指定。开发默认可用项目旁的 trading-data，但生产或大数据测试应放在独立磁盘目录。

原始行情只允许来自 `storage.data_root/history`。源码目录不再保留 `samples` 或其他行情副本；测试需要
完整历史时同样只读该目录，跨进程 smoke 仅在系统临时目录创建隔离工作副本。

源码仓库不得假定固定盘符。支持 Windows 和 Linux 路径，但所有持久化引用统一保存为 data_root 下的正斜杠相对路径。

## 2. 目录结构

~~~text
trading-data/
├── history/                              # 用户原始文件，只读
├── config/
│   ├── instruments.json
│   ├── sessions.json
│   └── trading_calendar.csv
├── catalog/
│   ├── catalog.json
│   └── import-errors.ndjson
├── normalized/
│   └── SHFE.AO2609.5m/
│       └── <data_revision_short>/
│           ├── meta.json
│           ├── bars.parquet
│           ├── quality.json
│           └── _SUCCESS
├── cache/
│   ├── indicators/<cache_key>/
│   ├── chan/<cache_key>/
│   └── replay/<cache_key>/
├── runs/
│   └── <run_id>/
├── workspaces/
│   └── default/
│       ├── layouts/
│       └── drawings/
├── logs/
│   ├── go/
│   ├── python/
│   └── vue/
└── tmp/
    └── <job_id>/
~~~

目录职责：

| 目录 | 可删除 | 可覆盖 | 权威性 |
|---|---|---|---|
| history | 否 | 否 | 原始事实 |
| config | 程序按权威规则补充，允许人工校正 | 有版本规则 | 时间与合约语义 |
| catalog | 可重建 | 原子替换 | 查询索引 |
| normalized | 可由原始数据重建 | 相同 revision 不覆盖 | 标准行情 |
| cache | 是 | 用新 key，不原地改成功结果 | 派生缓存 |
| runs | 否 | 否 | 正式回测审计记录 |
| workspaces | 可备份恢复 | 带 revision 更新 | 用户界面状态 |
| logs | 自动轮转 | 是 | 运行诊断 |
| tmp | 是 | 是 | 未完成任务 |

## 3. 数据集标识

扫描未知合约时，导入器先匹配带来源版本的内置品种规则；仍未命中时，从公开期货交易参数表
结构化读取交易所、品种、合约乘数和最小变动价位，并以同目录临时文件和原子重命名补充
`instruments.json` 与 `sessions.json`。首批内置规则覆盖郑商所白糖 `SR`：价格精度 0、缩放 1、最小
变动价位 1、合约乘数 10，夜盘为 21:00–23:00。规则来源为郑商所 2024 年 6 月 26 日发布的
《郑州商品交易所白砂糖期货业务细则》。自动生成条目保存 `rule_source_url`、`rule_version` 和
`rule_checked_at`，已有匹配条目不会被覆盖。

品种配置按 `exchange + product` 唯一保存。具体月份合约和同品种加权指数共享价格精度、最小
变动价位、合约乘数与交易时段，例如 `AO2609` 和 `AOL9` 都匹配 `SHFE.AO`，不会为指数新增
第二条配置，也不会在已有 AO 品种配置时访问网络。

联网生成的品种条目保存参数表 URL、数据日期和核验日期。交易时段从只读 TDX 中实际出现的时间边界
生成并单独记录规则版本，不根据品种名称猜测。交易日历优先使用已有日历和 TDX 文件中实际出现的
交易日；首个夜盘仍缺前序交易日时查询公开交易日序列。程序不得把交易日简单减一天；联网、解析或
唯一性校验失败时，该源文件保持 `needs_mapping` 并报告具体原因。

建议：

~~~text
dataset_id = <exchange>.<instrument>.<timeframe>
例：SHFE.AO2609.5m
~~~

dataset_id 不含 data_revision。同一个逻辑数据集可以有多个不可变 revision，catalog 指向 active_revision。

data_revision：

~~~text
SHA256(
  source_file_bytes
  + importer_id
  + importer_version
  + canonical_import_options
  + instrument_config_hash
  + session_config_hash
  + trading_calendar_hash
)
~~~

不得只用文件名、大小或修改时间作为修订依据。

## 4. 标准 K 线 Parquet Schema

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| bar_index | int64 | 是 | revision 内连续序号 |
| timestamp_utc | int64 | 是 | UTC 毫秒，严格递增 |
| trading_day | date32 | 是 | 业务交易日 |
| source_hhmm | int32 | 是 | 原始 HHmm |
| open_i64 | int64 | 是 | 定点价格 |
| high_i64 | int64 | 是 | 定点价格 |
| low_i64 | int64 | 是 | 定点价格 |
| close_i64 | int64 | 是 | 定点价格 |
| volume | int64 | 是 | 成交量 |
| open_interest | int64 | 否 | 持仓量 |
| settlement_i64 | int64 nullable | 否 | 结算价 |
| source_line | int64 | 是 | 原文件行号 |
| flags | uint32 | 是 | 质量标志 |

Parquet 元数据同时写：

- schema_version。
- dataset_id。
- data_revision。
- timezone。
- timestamp_semantics。
- price_decimals。
- price_scale。
- importer_id 与 importer_version。

row group 建议按 16,384 至 65,536 行，首版选择 32,768 行。按 timestamp_utc 和 bar_index 写统计信息，以便区间裁剪。

## 5. meta.json

meta.json 必须符合 contracts/schemas/dataset-meta.schema.json，至少包括：

- 数据集和修订标识。
- 合约、交易所、周期。
- 原始文件相对路径、内容哈希、编码。
- 时间与日期语义。
- 价格精度。
- K 线数量、起止时间和起止交易日。
- 导入器与配置版本。
- 质量统计。
- 文件列表及校验和。

不在对外 API 或日志暴露本机绝对路径。

## 6. catalog.json

catalog 是可重建索引，不是数据库替代物。结构建议：

~~~json
{
  "schema_version": 1,
  "catalog_revision": 27,
  "updated_at": "2026-08-01T10:00:00Z",
  "datasets": [
    {
      "dataset_id": "SHFE.AO2609.5m",
      "active_revision": "sha256:...",
      "meta_path": "normalized/SHFE.AO2609.5m/abcd1234/meta.json",
      "status": "ready"
    }
  ]
}
~~~

更新时：

1. 在内存生成完整新 catalog。
2. 写同目录临时文件。
3. fsync 文件。
4. 原子重命名。
5. 必要时 fsync 目录。

## 7. 指标缓存

cache_key：

~~~text
SHA256(
  data_revision
  + algorithm_kind
  + algorithm_id
  + algorithm_version
  + source_hash
  + canonical_parameters
  + calculation_mode
  + engine_version
)
~~~

canonical_parameters 必须：

- JSON 键排序。
- 数字类型规范化。
- 明确默认值。
- 不包含 UI 临时状态。

普通指标目录：

~~~text
cache/indicators/<cache_key>/
├── manifest.json
├── values.parquet
└── _SUCCESS
~~~

values.parquet 以 bar_index 为连接键，不依赖当前前端数组下标。

## 8. 缠论缓存

~~~text
cache/chan/<cache_key>/
├── manifest.json
├── fractals.parquet
├── bi.parquet
├── zhongshu.parquet
├── events.parquet
├── checkpoints/
│   └── <bar_index>.bin
└── _SUCCESS
~~~

语义对象字段至少包含：

- object_id。
- start_bar_index、start_time、start_price_i64。
- end_bar_index、end_time、end_price_i64。
- direction 或上下界。
- confirmed。
- confirmed_at_bar_index。
- revision。

events.parquet：

| 字段 | 含义 |
|---|---|
| event_seq | 稳定递增序号 |
| known_at_bar_index | 回放可见时间 |
| object_type | fractal、bi、zhongshu |
| object_id | 对象稳定 ID |
| operation | upsert 或 delete |
| object_revision | 对象修订 |
| payload_json | 语义字段，不含像素 |

检查点是算法内部状态，不作为公共 API。检查点格式必须含 algorithm_version；版本不匹配时拒绝恢复。

### 8.1 回放事件缓存

~~~text
cache/replay/<cache_key>/
├── manifest.json
├── events.parquet
└── _SUCCESS
~~~

回放缓存键覆盖数据修订、算法引用、规范参数、起止范围、预热范围和引擎版本。
`events.parquet` 沿用上表的因果事件 Schema，不复制结构对象终态；Vue 从事件索引按
`known_at_bar_index <= replay_cursor` 应用 upsert/delete。成功目录不可原地修改，
等价请求复用已有 `_SUCCESS` 目录。

## 9. 正式回测目录

~~~text
runs/<run_id>/
├── run.json
├── status.json
├── summary.json
├── indicator_values.parquet
├── strategy_states.parquet
├── stage_signals.parquet
├── trade_signals.parquet
├── chart_events.parquet
├── orders.parquet
├── fills.parquet
├── trades.parquet
├── positions.parquet
├── equity.parquet
├── log.ndjson
└── _SUCCESS
~~~

run.json 必须符合 run-manifest.schema.json，并记录：

- dataset_id 与 data_revision。
- 正式起止范围和预热起点。
- 策略 ID、版本、源码哈希和参数。
- 指标依赖及版本。
- 初始资金。
- 合约乘数、保证金、手续费和滑点。
- 信号和成交时机。
- 随机种子。
- Python 与引擎版本。
- 创建时间和 trace_id。

status.json 可在运行中原子替换。成功后 run 目录不可修改；添加备注应写独立元数据文件或新版本，不改事实表。

## 10. 工作区文件

~~~text
workspaces/default/
├── workspace.json
├── layouts/<layout_id>.json
└── drawings/<layout_id>/<dataset_id>.json
~~~

- 布局保存窗格权重、面板尺寸、可见项和对象顺序。
- 绘图按 profile_id、layout_id、dataset_id 保存。
- 策略参数属于布局实例；算法结果属于 cache_key。
- 每个文件有 revision、updated_at 和 schema_version。

## 11. 日志文件

~~~text
logs/go/app.log
logs/python/strategy.log
logs/vue/client.log
~~~

活动文件达到 50 MiB 后滚动；保留 9 个备份，总数不超过 10。旧文件压缩为 gzip。每个进程只滚动自己的文件，Vue 文件由 Go 统一写。

## 12. 原子性与崩溃恢复

- 所有最终文件先写入 tmp/<job_id>。
- 写完后校验行数、Schema 和校验和。
- 将完整目录原子移动到目标位置。
- 最后写 _SUCCESS。
- 存在目录但没有 _SUCCESS 时视为无效，不可查询。
- Go 启动时清理超过配置保留时间的 tmp。
- 删除 cache 应先移入 trash 或使用明确的缓存清理命令；不得递归删除未经校验的路径。

## 13. 数据查询约定

- 所有返回按 bar_index 递增。
- 首次尾部查询默认 3000 根。
- 向左预取默认 1500 根。
- 请求可指定 before_bar_index，返回严格小于该值的最近 N 根。
- 每个响应返回 coverage、has_more_before、data_revision 和 checksum。
- UI 必须拒绝不同 revision 的 K 线与指标结果混合。
