# 04 类 TradingView UI、窗格与图层

## 1. 页面总体布局

~~~mermaid
flowchart TB
    T["顶部工具栏：合约｜周期｜图表类型｜指标｜策略｜回放｜布局｜保存"]
    subgraph W["中央工作区"]
        direction LR
        L["左工具栏<br/>光标、趋势线、矩形、文字、磁吸、锁定、隐藏、删除"]
        C["多窗格 ChartGroup<br/>主价格图 + 多个指标副图"]
        R["右侧可折叠 Dock<br/>自选、对象树、数据窗口、策略参数"]
    end
    B["底部可折叠面板：回放、回测、交易、统计、任务、日志"]
    T --> W
    W --> B
~~~

默认尺寸：

| 区域 | 默认 | 约束 |
|---|---:|---|
| 顶部工具栏 | 44 px | 固定高度 |
| 左绘图工具栏 | 48 px | 固定宽度 |
| 右侧面板 | 320 px | 280–600 px，可收起 |
| 底部面板 | 260 px | 160 px 至可用高度的 60%，可收起 |
| 主图 | 可用图表高度的 75% | 最小 240 px |
| 副图1 | 12.5% | 最小 80 px |
| 副图2 | 12.5% | 最小 80 px |

右侧和底部面板必须参与布局，展开时压缩图表；不得以浮层覆盖价格轴、时间轴或最后一根 K 线。

## 2. 多窗格模型

所有窗格属于一个 ChartGroup：

- 一个不可删除的 price 主图。
- 零到多个 indicator 副图。
- 默认创建主图、MACD 副图、成交量副图。
- 默认权重 6:1:1，即 75%:12.5%:12.5%。
- 新增更多副图时，主图默认维持 75%，所有副图均分剩余 25%，若触发最小高度则按约束重新分配。

共享：

- 时间轴。
- 可视逻辑范围。
- 横向缩放。
- 垂直十字线的时间位置。
- 回放游标。

独立：

- 每个窗格的纵轴。
- 自动缩放范围。
- 指标单位和格式。
- 窗格背景覆盖物与水平十字线。

只有最下面的可见窗格显示时间标签。各窗格的垂直网格线必须像素对齐。

## 3. 分隔线行为

### 3.1 水平分隔线

- 命中热区至少 8 px，视觉线可为 1 px。
- 拖动仅改变分隔线上下两个相邻窗格：上方增加多少，下方减少多少。
- 不重新分配其他窗格，除非达到最小高度。
- 达到最小高度后停止继续拖动。
- 双击恢复该布局模板的默认比例。
- 拖动期间只更新尺寸和重绘，不取 K 线、不调用 Python。

### 3.2 右侧垂直分隔线

- 拖动改变 ChartGroup 和 RightDockPanel 宽度。
- 左工具栏宽度不参与。
- 图表变宽后若新可见历史仍在内存缓存，仅重绘。
- 若超过左缓存边界，按预取规则向 Go 请求。

### 3.3 底部分隔线

- 拖动改变图表工作区和底部面板高度。
- 不修改内部主副图权重；只按保存权重重新分配新的图表高度。

### 3.4 通用能力

- 窗格最大化与还原。
- 副图折叠、删除、上移、下移。
- 主图不可删除。
- 右侧与底部面板记住最后展开尺寸。
- 窗口过小时优先保证最小高度，并允许整体滚动或自动折叠最低优先级副图，不允许出现负尺寸。

## 4. 推荐 Vue 组件

~~~text
AppShell.vue
├── TopToolbar.vue
├── WorkspaceBody.vue
│   ├── LeftDrawingToolbar.vue
│   ├── ChartWorkspace.vue
│   │   ├── ChartGroup.vue
│   │   ├── PaneSplitterOverlay.vue
│   │   ├── ReplayCursorOverlay.vue
│   │   └── ChartContextMenu.vue
│   ├── WorkspaceSplitter.vue
│   └── RightDockPanel.vue
│       ├── WatchlistPanel.vue
│       ├── ObjectTreePanel.vue
│       ├── DataWindowPanel.vue
│       └── StrategyParamsPanel.vue
├── BottomSplitter.vue
└── BottomDockPanel.vue
    ├── ReplayPanel.vue
    ├── BacktestPanel.vue
    ├── TradesPanel.vue
    ├── TasksPanel.vue
    └── LogsPanel.vue
~~~

状态建议分为：

- chartSessionStore：当前数据集、revision、generation_id、缓存覆盖范围。
- layoutStore：窗格、权重、右侧/底部面板。
- objectTreeStore：显示、锁定、顺序和选择。
- drawingStore：用户绘图、撤销/重做。
- calculationStore：指标、策略实例和参数。
- replayStore：游标、速度、状态。
- taskStore：导入、计算、回测任务。

## 5. 图表内部图层

从底到顶：

| 逻辑层 | 层级带 | 内容 | 是否允许用户跨带移动 |
|---|---:|---|---|
| Background | 0 | 背景、交易时段底色、水印 | 否 |
| Region Fill | 100 | 浅蓝笔中枢与浅黄标准线段中枢的半透明填充、策略区间底色 | 仅同带排序 |
| Grid | 200 | 网格、交易日分隔线 | 否 |
| Main Series | 300 | K 线、美国线、主行情 | 同带排序 |
| Price Indicator | 400 | MA、EMA、VWAP 等主图指标 | 同带排序 |
| Strategy Overlay | 500 | 中枢边框、分型、缠论笔/段、背驰、买卖点、策略状态 | 同带排序 |
| User Drawing | 600 | 矩形、趋势线、水平线、文字 | 同带排序；可选择置于策略下方的兼容子层 |
| Trading Overlay | 700 | 回测买卖点、订单线、持仓线；为未来实盘预留 | 同带排序 |
| Crosshair | 800 | 十字线、当前价格线、悬停标记 | 否 |
| Interaction | 900 | 选择框、控制点、拖动预览、工具提示 | 否 |

中枢必须拆分绘制：

- 笔中枢：Region Fill 中绘制浅蓝色半透明填充与柔和阴影，Strategy Overlay 中绘制浅蓝色实线边框。
- 标准线段中枢：Region Fill 中绘制浅黄色半透明填充与柔和阴影，Strategy Overlay 中绘制浅黄色实线边框。
- 缠论笔：高于均线和中枢边框。

用户矩形默认位于策略覆盖物之上，填充透明度建议 10%–20%。选中控制点只在 Interaction 层绘制。

## 6. Lightweight Charts 映射

首版使用一个支持多 pane 的 Lightweight Charts 图表实例，不创建三套互相追赶的独立图表。

| 业务对象 | 推荐实现 |
|---|---|
| K 线 | CandlestickSeries，主 pane |
| MA | LineSeries，主 pane |
| 成交量 | HistogramSeries，副 pane |
| MACD | LineSeries + HistogramSeries，副 pane |
| 缠论 | 一个批量 ChanPrimitive |
| 中枢填充 | ChanPrimitive 的 bottom pane view |
| 中枢边框、笔 | ChanPrimitive 的 normal pane view，内部再排序 |
| 用户绘图 | DrawingPrimitive 的 normal pane view |
| 选择控制点 | 选中时才创建 top pane view |
| 回测买卖点 | TradingEventPrimitive |

Lightweight Charts 的 bottom、normal、top 只是物理绘制层。业务 LayerManager 必须在 normal 内继续维护稳定顺序，不能把所有内容塞入 top。永久策略线不得遮住十字线和交互控件。

## 7. 对象模型

### 7.1 Source 与 Object

- SeriesSource：K 线和普通指标序列。
- StrategySource：一个算法实例，内部批量拥有大量只读语义对象。
- DrawingObject：一个用户可编辑绘图。
- TradingSource：一次回测的买卖点、订单和持仓覆盖物。

缠论的一笔或一个中枢不是普通 DrawingObject。对象树中建议：

~~~text
主图
├── AO2609 K线
├── MA
│   ├── MA5
│   └── MA20
├── 缠论策略
│   ├── 分型
│   ├── 笔
│   ├── 笔中枢
│   ├── 标准线段中枢
│   ├── 背驰
│   └── 买卖点
├── 用户绘图
│   ├── 矩形 1
│   └── 趋势线 1
└── 回测运行 BT-...
~~~

右侧“对象树”在每个 `StrategySource` 父节点下逐项显示背驰、标准买卖点和类买卖点，按 `bar_index` 倒序展示，并在内容超出面板高度时使用独立纵向滚动条。点击信号只改变选中状态，不调整共享时间轴；锚点处于当前屏幕时，以发生点和 `confirmed_at_bar_index` 对应确认点作为两个高亮端点。点击小锁才执行定位，若锚点不在当前 K 线缓存或视口内，则用目标附近的有限连续历史窗口替换当前非连续缓存并将对象移入屏幕。信号仍是只读语义对象，不加入普通绘图节点和绘图持久化文件。

策略分组可展开显示类别，但不列出成千上万个单体对象。数据窗口负责查看悬停对象详情。

### 7.2 通用属性

所有 source 或 drawing 至少有：

- id。
- name。
- pane_id。
- visible。
- locked。
- z_band。
- order_in_band。
- style。
- created_at 或 algorithm_version。

用户绘图另有 anchors 和 revision。策略对象另有 data_revision、parameters_hash 和 confirmed 状态。

指标 source 的 `style.outputs` 以算法输出名为键，保存颜色、透明度、线宽、线型和可见性。
“当前使用”卡片中的 `…` 打开样式弹窗；弹窗内选择只修改本地草稿，点击确认后才写回
source 并刷新渲染，取消则丢弃草稿。样式调整不修改算法参数，不创建 Python 计算任务。
普通指标只开放线型输出；缠论按分型、笔、段、笔中枢、标准线段中枢、背驰和买卖点七个语义输出配置，并与类别可见性同步。段默认使用黄色线。
MACD 柱保留由数值正负决定的红/蓝绿色细柱，不开放颜色和线型覆盖。

## 8. 坐标与锚点

所有持久化图形保存：

~~~json
{
  "id": "drawing-01...",
  "type": "rectangle",
  "anchors": [
    {"time": 1785502800000, "price_i64": 235100, "price_scale": 100},
    {"time": 1785510000000, "price_i64": 238600, "price_scale": 100}
  ]
}
~~~

禁止持久化：

- x/y 像素。
- 当前窗口中的数组下标。
- Canvas 宽高相关比例。

不存在精确 K 线时间的用户锚点可以保留实际时间；磁吸开启时才吸附到最近 K 线 OHLC。

## 9. 命中检测与输入优先级

从高到低：

1. 已选对象控制点。
2. 活动绘图工具预览。
3. 顶层可交互 DrawingObject。
4. 其他可交互 DrawingObject。
5. 回测标记与策略对象悬停。
6. 图表平移和缩放。

命中检测按视觉顺序反向遍历。locked 对象可显示悬停信息，但不可拖动。隐藏对象不参与命中检测。

## 10. 顶部与左侧工具

首期顶部：

- 合约选择。
- 周期选择。
- 图表类型。
- 指标。
- 策略。
- 回放。
- 撤销/重做。
- 布局。
- 保存状态。

首期左侧：

- 选择/十字光标。
- 趋势线。
- 水平线。
- 矩形。
- 文字。
- 测量。
- 磁吸。
- 保持绘图模式。
- 锁定全部。
- 隐藏全部绘图。
- 删除选中或删除全部绘图。

斐波那契、复杂形态和社区脚本不在首期。

## 11. 右侧面板

标签：

- 自选合约。
- 对象树。
- 数据窗口。
- 策略参数。

对象树支持：

- 显示/隐藏。
- 锁定/解锁。
- 同兼容层内拖动排序。
- 重命名用户绘图。
- 删除用户绘图或指标实例。
- 定位到窗格。

不得允许把 Crosshair 或 Interaction 层拖动到业务层中。

## 12. 底部面板

标签：

- 回放控制。
- 回测摘要。
- 交易与成交。
- 权益与回撤。
- 后台任务。
- 可筛选日志。

日志 UI 读取 Go 提供的受限查询 API，不直接打开日志文件。首期可只显示当前会话内存日志，文件查询可在后续里程碑实现。

## 13. 回放视觉规则

- 回放游标右侧的未来 K 线不绘制。
- known_at_bar_index 大于游标的图形、状态和信号不绘制。
- 未确认缠论对象用虚线或降低透明度。
- 已撤销对象不显示，但数据窗口可在审计模式查看事件历史。
- 移动游标不调用 Python；只有缺少事件缓存或策略参数变化时才创建计算任务。

## 14. 布局保存

保存权重，不保存实际窗格像素：

~~~json
{
  "panes": [
    {"id": "main", "role": "price", "weight": 6, "min_height": 240},
    {"id": "macd", "role": "indicator", "weight": 1, "min_height": 80},
    {"id": "volume", "role": "indicator", "weight": 1, "min_height": 80}
  ],
  "right_panel": {"width": 320, "collapsed": false},
  "bottom_panel": {"height": 260, "collapsed": true}
}
~~~

完整格式以 contracts/schemas/layout.schema.json 为准。

## 15. UI 验收重点

- 拖动任意分隔线不触发 Python 计算。
- 右侧面板展开后最后一根 K 线仍在图表可视区。
- 三窗格时间轴和十字线像素一致。
- 缩放和向左预取后，用户绘图与缠论锚点不漂移。
- 对象树顺序与实际绘制顺序一致。
- 10,000 个缠论语义对象不产生 10,000 个 Vue 组件。
- 页面刷新后布局、绘图、指标实例和面板状态正确恢复。
