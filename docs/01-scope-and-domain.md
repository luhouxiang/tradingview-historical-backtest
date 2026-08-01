# 01 产品范围与领域规则

## 1. 产品目标

用户能够选择本地历史期货数据集，在接近 TradingView 的页面中浏览 K 线、加载指标与策略覆盖物、使用绘图工具、逐根回放，并用完全相同的策略状态机完成回测。

系统优先保证：

1. 时间与交易日语义正确。
2. 没有未来函数。
3. 结果可复现、可追溯。
4. 图表交互流畅。
5. 指标、策略、回测执行与参数优化相互解耦。

## 2. 首期用户流程

### 2.1 历史数据浏览

1. 用户把一个或多个历史文件放入 history 目录。
2. Go 扫描并显示待导入文件及识别结果。
3. 用户修正无法自动识别的合约、周期、日期语义或时区。
4. Go 标准化并生成数据集修订。
5. 用户选择合约和周期，图表首次加载尾部 3000 根。
6. 向左平移接近缓存边界时，前端预取更早 1500 根。

### 2.2 指标与缠论

1. 用户从指标/策略窗口选择算法。
2. Vue 向 Go 提交参数。
3. Go 计算 cache_key，命中缓存则直接返回。
4. 未命中时，Go 创建任务并通知 Python。
5. Python 顺序读取标准化 K 线，写入结果和 manifest。
6. Vue 只查询当前时间范围内的结果并绘制。

### 2.3 回放

1. 用户选择起点、速度和策略。
2. Python 生成或复用因果事件流。
3. Vue 用 replay_cursor 控制当前可见的 K 线和 known_at 事件。
4. 暂停、单步、加速只移动游标，不重新定义算法输入历史。

### 2.4 回测

1. 用户选择数据范围、策略参数、资金、手续费、滑点和成交模型。
2. Go 创建 run_id，Python 从预热起点开始顺序运行。
3. 策略状态、阶段信号、交易信号、订单、成交和权益分别保存。
4. 成功后写 _SUCCESS，结果目录不再覆盖。
5. Vue 展示汇总、权益、回撤、交易列表和图上标记。

## 3. 核心术语

| 术语 | 定义 |
|---|---|
| source_file | 用户提供的原始历史文件，只读。 |
| dataset_id | 合约与周期的稳定逻辑标识，例如 SHFE.AO2609.5m。 |
| data_revision | 原始内容和影响标准化的配置/代码共同计算出的不可变修订。 |
| bar_index | 在一个 data_revision 内从 0 开始、按真实时间严格递增的连续序号。 |
| trading_day | 交易所业务交易日，不等同于自然日期。 |
| timestamp_utc | K 线实际自然时间的 UTC 毫秒。 |
| calculation_id | 一次指标/缠论计算任务。 |
| cache_key | 数据修订、算法、参数和模式的内容寻址键。 |
| run_id | 一次正式回放/回测运行的唯一 ULID。 |
| known_at_bar_index | 某状态、图形或信号在回放中最早允许被用户知道的 K 线序号。 |
| anchor | 图形的时间与价格坐标，不是像素坐标。 |
| generation_id | 前端切换合约/周期后生成的代号，用于丢弃旧请求返回。 |

## 4. 历史数据源

首个正式适配器是通达信导出的文本 K 线。

已核验样例：

- 文件：30#AO2609.txt。
- 标题：AO2609 氧化铝2609 5分钟线 不复权。
- 编码：GB18030/GBK。
- 换行：CRLF。
- 正文列：日期、时间、开、高、低、收、成交量、持仓量、结算价。
- 第一行是说明，第二行是表头，最后一行是数据来源。
- 同一日期记录顺序可为 21:45、23:05、01:00、09:05。
- 结算价为 0 时，在该样例中代表缺失，不代表真实结算价。

导入器必须使用适配器模式。首期只承诺 tdx_txt_v1；其他 CSV/TXT 格式以后新增适配器，不得在主导入流程堆积文件名特判。

## 5. 时间语义

### 5.1 必须保存的字段

每根 K 线至少保存：

- bar_index。
- timestamp_utc。
- trading_day。
- source_hhmm。
- timezone。
- timestamp_semantics，首个样例为 bar_end。

### 5.2 交易日标签转换

每个数据源必须明确 date_semantics：

- trading_day：源日期是业务交易日。
- calendar_date：源日期是自然日期。

当 date_semantics 为 trading_day：

- 时间大于等于 night_start 的夜盘记录使用交易日历中的 night_session_date。
- 午夜后至 night_end 的记录使用 trading_day 的自然日期。
- 日盘记录使用 trading_day 的自然日期。
- 周末和节假日前后的 night_session_date 必须从交易日历读取。

禁止把所有 21 点后的记录简单减去 24 小时，因为周末和节假日会产生错误。

无法找到交易日历映射时：

- 不生成可用数据集。
- 在 import-errors.ndjson 记录错误。
- 在 UI 显示待修正状态。

## 6. K 线质量规则

- high 必须不小于 open、close、low。
- low 必须不大于 open、close、high。
- volume 和 open_interest 不得为负。
- 同一 data_revision 内 timestamp_utc 必须严格递增。
- 重复时间不得静默覆盖；按配置选择报错或保留一条并记录冲突。
- 缺失 K 线不自动补齐。
- 零成交量 K 线保留并设置标记。
- 尾部不完整交易日保留并设置标记。
- 结算价是否允许 0 由适配器字段规则决定。
- 原始文件行号必须能追溯到标准化错误。

建议 flags 位：

| 位 | 名称 |
|---:|---|
| 0 | ZERO_VOLUME |
| 1 | SESSION_GAP_BEFORE |
| 2 | INCOMPLETE_TRADING_DAY |
| 3 | SOURCE_FIELD_MISSING |
| 4 | MANUALLY_CORRECTED |
| 5 | DUPLICATE_RESOLVED |

## 7. 价格与数量

- 持久化价格、信号价、订单价和成交价使用 int64 定点整数。
- 元数据保存 price_decimals 和 price_scale。
- 实际价格 = price_i64 / price_scale。
- Python 指标内部允许 float64，但写出价格型字段时必须按统一舍入规则转换回 int64。
- volume、open_interest 使用 int64。
- 前端从 price_i64 和 price_scale 计算绘制值；不得用格式化字符串参与数值计算。

## 8. 因果性和未来函数

### 8.1 通用规则

- 指标、策略和回测只能读取当前 bar_index 及以前的数据。
- 预热 K 线可用于状态初始化，但不得进入正式统计范围。
- 每个输出同时区分 anchor_bar_index 与 known_at_bar_index。
- 回放到 N 时，只显示 known_at_bar_index 小于等于 N 的事件。

### 8.2 缠论

缠论结果属于：

> dataset_id + data_revision + algorithm_id + algorithm_version + canonical_parameters

不属于：

- 当前屏幕显示范围。
- 当前缩放等级。
- 前端数组的起始下标。

Python 必须维护连续状态。加载更早 K 线不能把左右两段独立计算后直接拼接。

允许三种来源：

1. 完整范围已预计算，直接按范围查询。
2. 从前置检查点恢复并顺序重放。
3. 无缓存无检查点时，从确定起点完整重算。

固定预热若干根只能作为普通指标优化，不能宣称保证缠论与完整历史严格一致。

## 9. 策略信号层次

| 层次 | 示例 | 是否必存 |
|---|---|---|
| 策略状态 | waiting_retest | 是 |
| 阶段信号 | price_touched_ma20 | 是 |
| 交易信号 | open_short | 是 |
| 订单意图 | sell_open 1 lot | 是 |
| 模拟订单 | accepted/rejected | 是 |
| 模拟成交 | fill at next_open | 是 |

每层有稳定 ID，并保存父子关系。日志可记录摘要，但权威记录必须位于 run 目录。

## 10. 非目标

首期不承诺：

- 复权处理。
- 跨合约连续主力拼接。
- Tick 播放。
- 多品种组合回测。
- 实盘级撮合和交易所全部细则。
- 与 TradingView 私有实现完全一致。

这些能力只能在首期验收后作为新版本设计。

