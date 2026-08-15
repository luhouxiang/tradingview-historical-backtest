# 02 系统架构与数据流

## 1. 总体架构

~~~mermaid
flowchart LR
    V["Vue 3 浏览器<br/>图表、绘图、对象树、回放控制"] -->|"公共 HTTP/JSON"| G["Go 1.25.7<br/>API、目录、区间查询、任务编排"]
    G -->|"内部 HTTP/JSON<br/>仅传引用和参数"| P["Python 3.14<br/>指标、缠论、策略、回放、回测"]
    G --> F[("data_root<br/>原始数据、Parquet、工作区、日志")]
    P --> F
~~~

硬边界：

- Vue 不直接访问 Python 或磁盘。
- Python 内部服务只监听 127.0.0.1。
- Go 是公共 API、路径授权与任务状态的唯一入口。
- Go 与 Python 不用 JSON 搬运整份 K 线；共同读取标准化 Parquet。

## 2. 推荐源码结构

~~~text
project/
├── cmd/chartd/main.go
├── internal/
│   ├── api/                   # 公共 HTTP API
│   ├── catalog/               # 数据集索引
│   ├── importer/              # 源适配器与标准化
│   ├── jobs/                  # 任务状态机、并发、取消
│   ├── logx/                  # 统一结构化日志
│   ├── pythonclient/          # Python 内部 API 客户端
│   ├── storage/               # Parquet/JSON/原子写入
│   └── workspace/             # 布局与用户绘图
├── web/
│   └── src/
│       ├── api/
│       ├── chart/
│       ├── components/
│       ├── logging/
│       ├── stores/
│       ├── types/
│       └── views/
├── python/
│   └── src/tvbt/
│       ├── api/
│       ├── engine/
│       ├── indicators/
│       ├── chan/
│       ├── strategies/
│       ├── backtest/
│       ├── optimize/
│       ├── storage/
│       └── logging_config/
├── contracts/
├── config/
├── scripts/
└── tests/
~~~

## 3. 组件职责

| 组件 | 输入 | 输出 | 不负责 |
|---|---|---|---|
| Go Importer | 原始 TXT、源配置、交易日历 | bars.parquet、meta.json、质量报告 | 指标与策略 |
| Go Catalog | normalized 元数据 | 可查询数据集目录 | 解析策略 |
| Go Chart Query | 数据集、范围 | 紧凑 K 线 JSON | 直接渲染 |
| Go Job Manager | 任务请求 | job_id、状态、取消 | 算法实现 |
| Python Engine | DatasetRef、算法与参数 | cache 或 run 文件 | 浏览器接口 |
| Vue ChartGroup | K 线和语义对象 | Canvas 渲染与交互 | 权威算法计算 |
| Workspace Store | 布局、绘图 JSON | 原子保存的工作区 | 正式回测记录 |

## 4. 数据导入流

~~~mermaid
sequenceDiagram
    participant U as 用户
    participant V as Vue
    participant G as Go
    participant D as data_root

    U->>D: 放入原始历史文件
    V->>G: POST /datasets/scan
    G->>D: 扫描 history
    G-->>V: 文件识别结果与待修正项
    V->>G: POST /datasets/import
    G->>D: 只读源文件与配置
    G->>G: 解析、时间转换、质量检查、计算 data_revision
    G->>D: 原子写 meta.json、bars.parquet、quality.json、_SUCCESS
    G-->>V: dataset_id 与 data_revision
~~~

导入过程中不得覆盖已有 revision。相同 revision 已存在且 _SUCCESS 完整时，直接复用。

## 5. 图表浏览流

1. Vue 请求数据集目录。
2. 切换合约或周期时生成新的 generation_id。
3. 首次请求尾部 3000 根 K 线。
4. Go 从 Parquet 读取并返回递增顺序的数据。
5. Vue 建立内存缓存和时间到 bar_index 映射。
6. 平移、缩放和窗格尺寸变化只在缓存内重新投影。
7. 距左边界不足一个可视屏幕时，请求更早 1500 根。
8. 返回的 generation_id 不等于当前值时，Vue 丢弃响应。

同一 dataset_id + data_revision + direction 的历史请求最多一个在途任务。新的更早请求可以合并目标范围，但不能并行重复读取。

## 6. 指标计算流

~~~mermaid
sequenceDiagram
    participant V as Vue
    participant G as Go
    participant P as Python
    participant D as data_root

    V->>G: POST /calculations
    G->>G: 规范化参数并计算 cache_key
    alt 缓存有效
        G-->>V: completed + result_ref
    else 缓存缺失
        G-->>V: accepted + job_id
        G->>P: DatasetRef + AlgorithmRef + 参数
        P->>D: 读取 bars.parquet
        P->>D: 原子写缓存结果与 manifest
        P-->>G: 完成摘要
        V->>G: 轮询 job 状态
        G-->>V: completed + result_ref
    end
~~~

DatasetRef 至少包含：

~~~json
{
  "dataset_id": "SHFE.AO2609.5m",
  "data_revision": "sha256:...",
  "bars_path": "normalized/SHFE.AO2609.5m/<revision>/bars.parquet",
  "meta_path": "normalized/SHFE.AO2609.5m/<revision>/meta.json"
}
~~~

bars_path 和 meta_path 必须是 data_root 下的规范化相对路径。Python 再次验证路径，拒绝绝对路径、父目录穿越和符号链接逃逸。

## 7. 回放与回测流

回放和回测使用同一核心：

~~~mermaid
flowchart TD
    B["按 bar_index 顺序的 K 线"] --> I["指标与缠论状态"]
    I --> S["策略状态机"]
    S --> E["阶段/交易/图形事件"]
    E --> R["回放游标"]
    E --> O["订单与成交模型"]
    O --> Q["持仓、权益和统计"]
~~~

区别只有消费者：

- 回放消费者按 known_at_bar_index 向 Vue 显示事件。
- 回测消费者把交易信号交给订单和成交模型。

不得维护一套“图表策略”和另一套“回测策略”。

## 8. 用户绘图与布局流

- Vue 在内存中完成拖动预览。
- 完成一次用户操作后生成新的 workspace_revision。
- Vue 通过 Go 保存完整快照或带 revision 的更新。
- Go 使用乐观并发：请求 revision 不是当前值时返回 409。
- 文件采用临时文件加同目录原子重命名。
- 用户绘图存时间与价格锚点，不存像素。

首期没有登录，使用 profile_id = default；数据模型仍保留 profile_id，以便未来扩展。

## 9. 浏览器日志流

~~~text
Vue logger
  -> 内存队列
  -> 每 1 秒或累计 100 条
  -> POST /api/v1/client-logs
  -> Go 校验和补充 received_at
  -> logs/vue/client.log
~~~

页面关闭时使用 sendBeacon 尝试发送剩余日志。失败不得阻塞页面卸载或图表交互。

## 10. 错误模型

所有公共 API 错误：

~~~json
{
  "error": {
    "code": "DATASET_REVISION_MISMATCH",
    "message": "Requested revision is no longer active",
    "request_id": "01...",
    "details": {
      "requested_revision": "sha256:...",
      "active_revision": "sha256:..."
    }
  }
}
~~~

错误码稳定，message 可读，details 不包含敏感绝对路径。

## 11. 并发与取消

- 导入锁粒度：source_file 的规范路径。
- 计算锁粒度：cache_key。
- 回测每次创建独立 run_id，不按参数去重；可复用只读指标缓存。
- 取消为协作式：Go 标记 cancelling，Python 在批次或 K 线安全点检查。
- 取消后的目录写 _CANCELLED，不写 _SUCCESS。
- Go 重启时扫描未完成任务，标记 interrupted；首期不自动续跑。

## 12. 后期实时扩展边界

未来可在 Go 公共 API 旁增加实时增量通道，但不得改变：

- DatasetRef。
- bar_index 与时间语义。
- Python 算法接口。
- 图层对象的时间/价格锚点。
- 回放/回测因果事件模型。

首期代码不得引入空转的实时连接或消息队列。

