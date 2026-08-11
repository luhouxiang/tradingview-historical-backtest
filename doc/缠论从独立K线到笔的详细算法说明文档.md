# 缠论从独立K线到笔的详细算法说明文档

更新日期：2026-08-10

适用实现：`python/src/tvbt/chan/engine.py` 中 `ChanEngine.algorithm_version = "5.0.0"`。

本文只说明从原始 K 线到独立 K 线、包含关系、分型、笔、笔中枢的程序算法和工程表达。段、段中枢、背驰、买卖点只在必要处作为边界提及，详细规则见 `docs/13-chan-bi-center-segment-algorithm.md` 和 `docs/14-chan-108-segment-center-divergence-trade-points.md`。

## 0. 总体处理链路

程序中的缠论基础结构按逐 K 线因果方式生成：

```text
RawBar
-> IncludedBar
-> Fractal
-> LineObject(kind=bi)
-> ReferenceCenter(component_kind=bi)
-> zhongshu.parquet / events.parquet / API / Vue ChanPrimitive
```

关键源码位置：


| 主题                                   | 位置                                                                                                                                                                                              |
| ---------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
|                                        |                                                                                                                                                                                                   |
| 算法定义、输出类别、因果模式           | `python/src/tvbt/chan/algorithm.py:34`, `python/src/tvbt/chan/algorithm.py:53`, `python/src/tvbt/chan/algorithm.py:73`                                                                            |
| 原始 K 线、独立 K 线、分型、笔的数据类 | `python/src/tvbt/chan/engine.py:23`, `python/src/tvbt/chan/engine.py:32`, `python/src/tvbt/chan/engine.py:48`, `python/src/tvbt/chan/engine.py:70`                                                |
| 逐 K 线入口                            | `python/src/tvbt/chan/engine.py:119`                                                                                                                                                              |
| 包含关系与独立 K 线生成                | `python/src/tvbt/chan/engine.py:145`                                                                                                                                                              |
| 分型封存                               | `python/src/tvbt/chan/engine.py:214`                                                                                                                                                              |
| 笔生成、候选端点、临时笔               | `python/src/tvbt/chan/engine.py:247`, `python/src/tvbt/chan/engine.py:287`, `python/src/tvbt/chan/engine.py:316`, `python/src/tvbt/chan/engine.py:354`                                            |
| 笔中枢扫描                             | `python/src/tvbt/chan/reference.py:364`                                                                                                                                                           |
| 结构对象同步为事件流                   | `python/src/tvbt/chan/engine.py:378`, `python/src/tvbt/chan/events.py:36`                                                                                                                         |
| Parquet Schema 与缓存提交              | `python/src/tvbt/chan/storage.py:19`, `python/src/tvbt/chan/storage.py:32`, `python/src/tvbt/chan/storage.py:48`, `python/src/tvbt/chan/storage.py:129`, `python/src/tvbt/chan/storage.py:169`    |
| Go 范围读取与 JSON 返回                | `internal/calculation/results.go:143`, `internal/calculation/results.go:155`, `internal/calculation/results.go:170`, `internal/calculation/results.go:247`, `internal/calculation/results.go:259` |
| OpenAPI 契约                           | `contracts/openapi.yaml:1194`, `contracts/openapi.yaml:1227`, `contracts/openapi.yaml:1242`, `contracts/openapi.yaml:1260`                                                                        |
| Vue 投影与绘制                         | `web/src/chart/chanPrimitive.ts:59`, `web/src/chart/chanPrimitive.ts:70`, `web/src/chart/chanPrimitive.ts:75`, `web/src/chart/chanPrimitive.ts:261`                                               |

原文来源说明：本文引用的“缠论 108 课原文口径”来自当前仓库已采用的镜像索引 `chanlun108.cn`，访问时间为 2026-08-10。本文只做短摘录和转述，不复制整篇原文。主要页面：

- 第 17 课：`https://chanlun108.cn/chanzhongshuochan108ke/17.html`
- 第 18 课：`https://chanlun108.cn/chanzhongshuochan108ke/18.html`
- 第 20 课：`https://chanlun108.cn/chanzhongshuochan108ke/20.html`
- 第 62 课：`https://chanlun108.cn/chanzhongshuochan108ke/62.html`
- 第 65 课：`https://chanlun108.cn/chanzhongshuochan108ke/65.html`
- 第 72 课：`https://chanlun108.cn/chanzhongshuochan108ke/72.html`
- 第 77 课：`https://chanlun108.cn/chanzhongshuochan108ke/77.html`

其中第 72 课只作为“分型、笔、线段、中枢”层级关系的背景来源；具体算法差异仍以第 62、65、77 课和第 17、18、20 课为主。

## 1. 独立 K 线

### 1.1 程序中的详细算法

程序把原始 K 线 `RawBar` 逐根送入 `ChanEngine.update()`。每根原始 K 线先经过三个基础校验：

1. `bar_index` 必须连续递增。
2. `time` 必须严格递增。
3. `low_i64 <= high_i64`。

校验通过后，程序调用 `_update_inclusion()` 尝试把原始 K 线转换为独立 K 线 `IncludedBar`。

独立 K 线不是简单复制原始 K 线。它是包含处理后的归一化 K 线，可以覆盖一根或多根原始 K 线。每根独立 K 线保留：

- `normalized_index`：独立 K 线序号。
- `start_raw_index` / `end_raw_index`：覆盖的原始 K 线起止范围。
- `high_i64` / `low_i64`：合并后的高低点。
- `high_time` / `low_time`：高低点对应的时间。
- `high_raw_index` / `low_raw_index`：高低点来自哪根原始 K 线。
- `confirm_time`：当前独立 K 线被最新原始 K 线更新后的确认时间。
- `direction`：当前合并方向，取 `up`、`down` 或初始阶段的 `unknown` 语义；当前代码实际以 `down` 初始化第一根候选。
- `source_raw_indices`：参与合并的全部原始 K 线索引。

新增独立 K 线的规则是严格的方向推进：

```text
当前 high > 前一独立 K 线 high 且 当前 low > 前一独立 K 线 low
=> 新增独立 K 线，direction = up

当前 high < 前一独立 K 线 high 且 当前 low < 前一独立 K 线 low
=> 新增独立 K 线，direction = down
```

只要不满足这两个严格推进条件，程序就不新增独立 K 线，而是替换最后一根独立 K 线。这意味着以下情况都会进入合并分支：

- 当前 K 线被上一根包含。
- 当前 K 线包含上一根。
- 高点或低点相等导致无法严格判定上移或下移。
- 边界重合造成的非严格推进。

`_update_inclusion()` 返回值表达是否真的追加了新的独立 K 线：

- 返回 `True`：追加了新的 `IncludedBar`，可以尝试封存倒数第二根独立 K 线的分型。
- 返回 `False`：只是合并替换最后一根 `IncludedBar`，不会封存新的分型。

### 1.2 程序表达与位置

独立 K 线只在 Python 算法内部和检查点中直接存在，不作为单独的 Parquet 结果暴露给 Go/Vue。

程序位置：

- `RawBar` 定义在 `python/src/tvbt/chan/engine.py:23`。
- `IncludedBar` 定义在 `python/src/tvbt/chan/engine.py:32`。
- 逐 K 线入口在 `python/src/tvbt/chan/engine.py:119`。
- 独立 K 线生成和合并在 `python/src/tvbt/chan/engine.py:145`。
- 缓存清单只记录独立 K 线数量为 `merged_bar_count`，位置在 `python/src/tvbt/chan/algorithm.py:85` 和 `python/src/tvbt/chan/storage.py:217`。
- 检查点会保存 `included`，位置在 `python/src/tvbt/chan/engine.py:703`。

对外表达：

- API 不直接返回独立 K 线数组。
- 分型、笔、笔中枢的 `bar_index/time/price_i64` 已经引用了独立 K 线内部锚定到的原始 K 线高低点。
- 因果回放依赖 `known_at_bar_index`，而不是依赖浏览器当前可见范围。

### 1.3 108 课原文口径

原文没有使用本项目字段名 `IncludedBar`，也没有把“独立 K 线”作为数据结构命名。它在第 65 课用“包含关系处理后的图形”表达同一层含义：先把有包含关系的相邻 K 线合并成新的 K 线，然后在没有包含关系的 K 线序列上继续讨论分型、笔和线段。

第 62 课讨论分型时，默认三根 K 线之间已经不再有包含关系；第 65 课进一步说明，包含关系处理要按已经形成的方向来合并高低点。

### 1.4 与当前程序的差异

差异点：

1. 原文是图形定义，当前程序必须落到可回放的数据对象，所以额外保存 `source_raw_indices`、高低点来源、确认时间和检查点状态。
2. 原文没有定义 `known_at_bar_index`。当前程序为避免未来函数，把每个结构对象的可知时点显式记录下来。
3. 原文没有规定起始两根 K 线方向未明时的代码处理。当前程序在 `first_pair` 分支中有特殊合并规则，用于兼容参考实现的起始包含语义。
4. 当前程序用严格 `>` / `<` 判断新增独立 K 线。等高、等低不会新增独立 K 线，而会进入合并分支；这是工程边界选择。

## 2. 包含关系

### 2.1 程序中的详细算法

包含关系处理发生在 `_update_inclusion()` 内部。它不是批量预处理，而是逐根原始 K 线在线更新。

算法步骤：

1. 把当前原始 K 线构造成候选 `IncludedBar`。
2. 若独立 K 线列表为空，直接追加第一根。
3. 若当前 K 线相对上一根独立 K 线严格上移，追加新独立 K 线并标记 `direction = up`。
4. 若当前 K 线相对上一根独立 K 线严格下移，追加新独立 K 线并标记 `direction = down`。
5. 否则认为需要合并，替换最后一根独立 K 线。

合并时使用已经建立的方向：

```text
direction = up:
  high = max(previous.high, current.high)
  low  = max(previous.low, current.low)

direction = down:
  high = min(previous.high, current.high)
  low  = min(previous.low, current.low)
```

这正是常见缠论包含处理口径：上升方向取两个高点中的高者、两个低点中的高者；下降方向取两个高点中的低者、两个低点中的低者。

程序还维护高低点来自哪根原始 K 线：

- 上升合并时，只有当前高点更高，`high_raw_index` 才来自当前 K 线；只有当前低点更高，`low_raw_index` 才来自当前 K 线。
- 下降合并时，只有当前高点更低，`high_raw_index` 才来自当前 K 线；只有当前低点更低，`low_raw_index` 才来自当前 K 线。

首个二元关系有特殊分支：

```text
len(included) == 1 且 previous 只覆盖一根原始 K 线
```

此时方向还没有被可靠建立。程序用当前 K 线是否向右扩张来决定高低点组合：

- 如果当前高点突破前高或当前低点跌破前低，`high` 更偏向当前高点，`low` 保留前低。
- 否则保留前高，`low` 更偏向当前低点。

这个分支是兼容参考实现的起始规则，不是一般缠论理论中的独立公理。

### 2.2 程序表达与位置

程序位置：

- 包含关系主逻辑：`python/src/tvbt/chan/engine.py:145`。
- 严格上移追加：`python/src/tvbt/chan/engine.py:164`。
- 严格下移追加：`python/src/tvbt/chan/engine.py:168`。
- 起始特殊分支：`python/src/tvbt/chan/engine.py:173`。
- 上升方向合并：`python/src/tvbt/chan/engine.py:187`。
- 下降方向合并：`python/src/tvbt/chan/engine.py:193`。
- 替换最后一根独立 K 线：`python/src/tvbt/chan/engine.py:198`。

程序表达上，包含关系没有单独输出对象。它的结果体现在：

- `self.included` 中的独立 K 线序列。
- 分型的 `normalized_index` 和锚点来源。
- `manifest.json` 中的 `merged_bars` 数量。
- 检查点 `export_state()` 中的 `included`。

### 2.3 108 课原文口径

第 65 课明确给出包含关系处理的方向性：向上时新 K 线取两根 K 线高点较高者和低点较高者；向下时取高点较低者和低点较低者。它还强调这种处理后的图形才是后续分型、笔、线段分析的对象。

第 62 课先给出分型、笔、线段的图形定义；第 65 课是在此基础上补充包含关系的标准化处理。

### 2.4 与当前程序的差异

差异点：

1. 当前程序把等高、等低这类边界统一视为合并分支，不会新增独立 K 线；原文更多是图形语言，没有给出所有等值边界的程序化分支。
2. 当前程序首个二元关系的 `first_pair` 处理是参考实现兼容规则，原文没有单独说明这个启动分支。
3. 当前程序只向后处理，不回看重写已经提交的原始历史文件；原文没有涉及持久化、检查点、回放因果这些工程约束。
4. 当前程序在合并时保存高低点的原始 K 线索引和时间。原文只关心合并后的图形高低点，不关心数据血缘。

## 3. 分型

### 3.1 程序中的详细算法

分型只在新增独立 K 线后尝试封存。包含合并不会触发新的分型判断。

当追加了新的独立 K 线后，程序检查倒数第二根独立 K 线：

```text
index = len(included) - 2
window = included[index - 1 : index + 2]
center = window[1]
left = window[0]
right = window[2]
```

顶分型条件：

```text
center.high_i64 > left.high_i64
center.high_i64 > right.high_i64
center.low_i64  > left.low_i64
center.low_i64  > right.low_i64
```

底分型条件：

```text
center.high_i64 < left.high_i64
center.high_i64 < right.high_i64
center.low_i64  < left.low_i64
center.low_i64  < right.low_i64
```

注意当前程序要求高点和低点都严格更高或严格更低。只高点更高但低点没有更高，不是顶分型；只低点更低但高点没有更低，也不是底分型。任意等值边界也不会构成分型。

分型锚点：

- 顶分型锚在中间独立 K 线的最高价，`bar_index/time` 使用该最高价对应的原始 K 线。
- 底分型锚在中间独立 K 线的最低价，`bar_index/time` 使用该最低价对应的原始 K 线。

分型确认时间：

- `confirmed_at_bar_index = right.end_raw_index`。
- `known_at_bar_index = confirmed_at_bar_index`。

也就是说，分型要等右侧独立 K 线出现并完成当前合并状态后，才成为可知事实。

### 3.2 程序表达与位置

程序位置：

- `Fractal` 数据类：`python/src/tvbt/chan/engine.py:48`。
- 分型封存函数：`python/src/tvbt/chan/engine.py:214`。
- 顶分型判断：`python/src/tvbt/chan/engine.py:224`。
- 底分型判断：`python/src/tvbt/chan/engine.py:227`。
- 锚点、确认时点和稳定 ID：`python/src/tvbt/chan/engine.py:231`。
- 事件发布：`python/src/tvbt/chan/engine.py:131`。
- Parquet Schema：`python/src/tvbt/chan/storage.py:19`。
- Go 读取结构：`internal/calculation/results.go:143`。
- OpenAPI 契约：`contracts/openapi.yaml:1227`。
- Vue 投影和绘制：`web/src/chart/chanPrimitive.ts:17`, `web/src/chart/chanPrimitive.ts:103`, `web/src/chart/chanPrimitive.ts:271`。

分型输出字段：


| 字段                     | 含义                                              |
| -------------------------- | --------------------------------------------------- |
| `object_id`              | 稳定 ID，由类型、锚点时间、确认位置和价格哈希生成 |
| `bar_index`              | 分型理论锚点所在原始 K 线                         |
| `time`                   | 分型理论锚点时间                                  |
| `price_i64`              | 顶分型最高价或底分型最低价                        |
| `fractal_type`           | `top` 或 `bottom`                                 |
| `confirmed`              | 当前分型输出恒为`true`                            |
| `confirmed_at_bar_index` | 右侧独立 K 线已知时点                             |
| `known_at_bar_index`     | 事件流中实际可见时点                              |
| `object_revision`        | 语义对象修订号                                    |

### 3.3 108 课原文口径

第 62 课把三根无包含关系 K 线的顶、底结构定义为分型：顶分型要求中间 K 线在三根中最高且最低点也最高；底分型反向。原文还把相邻顶、底分型作为笔和线段的基础。

第 79 课、第 82 课等后续课次把分型作为辅助操作工具讨论，但程序中从独立 K 线到笔的基础定义主要对应第 62 课和第 77 课。

### 3.4 与当前程序的差异

差异点：

1. 当前程序使用严格 `>` / `<`。原文的“最高”“最低”是图形语言，未逐项列出等高等低时的代码分支；当前实现选择等值不成分型。
2. 当前程序的分型有 `object_id`、`object_revision`、`known_at_bar_index`，这些是工程审计和回放需求，原文没有这些概念。
3. 当前程序只在新增独立 K 线时封存分型。若新原始 K 线只是合并进最后一根独立 K 线，则不会提前创建新分型；这符合无未来函数约束，但比静态图形判断更严格。
4. 当前程序分型锚点保存原始 K 线的时间和索引，而不是只保存独立 K 线序号或屏幕坐标。

## 4. 笔

### 4.1 程序中的详细算法

笔由已确认分型队列 `_pending_fractals` 生成。核心常量是：

```text
REFERENCE_MIN_INDEPENDENT_BARS = 5
```

笔生成分为候选选择、候选端点替换、后续分型确认和临时笔绘制四层。

#### 4.1.1 待处理分型队列

每个新分型进入 `_pending_fractals`。队列第一个分型是当前候选笔起点 `base`。生成一笔后，被选中的终点分型不会丢弃，而是保留为下一笔起点。因此程序天然保证相邻笔首尾相接。

#### 4.1.2 寻找第一个相反分型

程序从 `base` 后面寻找第一个相反类型分型：

```text
base = 顶分型 => 先找底分型
base = 底分型 => 先找顶分型
```

找到后，还要求起点和候选终点之间的独立 K 线跨度满足：

```text
候选终点.normalized_index - 起点.normalized_index + 1 >= 5
```

不足 5 根独立 K 线时，程序继续向后找下一个相反类型分型。

#### 4.1.3 同类候选端点极值替换

找到满足跨度的候选终点后，程序继续向后扫描。如果出现与候选终点同类且更极端的分型，就替换候选终点。

规则：

- 起点为顶分型时，候选终点是底分型；后续更低的底分型替换当前候选底。
- 起点为底分型时，候选终点是顶分型；后续更高的顶分型替换当前候选顶。

这里的极值比较使用候选分型所在独立 K 线的高低点，而不是只比较 `Fractal.price_i64`。

#### 4.1.4 后续分型确认

候选终点本身不会立即成为正式笔终点。程序继续等待与起点同类的后续分型，并要求该后续分型与候选终点之间也满足最少 5 根独立 K 线跨度。

确认条件：

```text
起点为顶分型:
  后续顶分型所在独立 K 线 low_i64 > 候选底分型所在独立 K 线 high_i64

起点为底分型:
  后续底分型所在独立 K 线 high_i64 < 候选顶分型所在独立 K 线 low_i64
```

满足后，候选终点被释放为正式笔终点。正式笔的可知时点取以下最大值：

```text
max(
  参考选择规则返回的 known_at,
  当前新分型 known_at_bar_index,
  前一笔 known_at_bar_index
)
```

这样做是为了处理“一次新分型释放多笔”的情况，确保事件时间不倒退。

#### 4.1.5 笔方向

笔方向由起点分型决定：

```text
起点为 bottom，终点为 top => direction = up
起点为 top，终点为 bottom => direction = down
```

程序还强制检查：如果上一笔方向与当前方向相同，直接抛出断言错误。正常输出必须严格上下交替。

#### 4.1.6 临时笔

如果候选终点已经满足跨度和极值规则，但缺少后续同类分型的价格分离确认，程序会发布一条临时笔：

- `object_id` 使用 `bi-provisional` 前缀。
- `confirmed = false`。
- 只进入事件流和当前结果对象。
- 不加入 `self.bi`，因此不参与笔中枢、段、中枢、背驰或策略信号。
- 后续正式笔确认时，程序会删除旧临时笔。

### 4.2 程序表达与位置

程序位置：

- 最少独立 K 线常量：`python/src/tvbt/chan/engine.py:14`。
- `LineObject` 数据类：`python/src/tvbt/chan/engine.py:70`。
- 分型消费和正式笔创建：`python/src/tvbt/chan/engine.py:247`。
- 候选终点选择：`python/src/tvbt/chan/engine.py:287`。
- 最少独立 K 线检查：`python/src/tvbt/chan/engine.py:303`, `python/src/tvbt/chan/engine.py:338`。
- 同类极值替换和后续分型确认：`python/src/tvbt/chan/engine.py:316`。
- 临时笔输出：`python/src/tvbt/chan/engine.py:354`。
- 正式笔进入结构重算：`python/src/tvbt/chan/engine.py:141`。
- Parquet Schema：`python/src/tvbt/chan/storage.py:32`。
- Go 读取结构：`internal/calculation/results.go:155`。
- OpenAPI 契约：`contracts/openapi.yaml:1242`。
- Vue 线段投影和绘制：`web/src/chart/chanPrimitive.ts:70`, `web/src/chart/chanPrimitive.ts:242`, `web/src/chart/chanPrimitive.ts:265`。

笔输出字段：


| 字段                                                 | 含义                                                                      |
| ------------------------------------------------------ | --------------------------------------------------------------------------- |
| `object_id`                                          | 稳定 ID。正式笔由起点分型 ID 和终点分型 ID 生成；临时笔由起点分型 ID 生成 |
| `start_bar_index` / `start_time` / `start_price_i64` | 起点分型锚点                                                              |
| `end_bar_index` / `end_time` / `end_price_i64`       | 终点分型锚点                                                              |
| `direction`                                          | `up` 或 `down`                                                            |
| `confirmed`                                          | 正式笔为`true`；当前尾部临时笔为 `false`                                  |
| `confirmed_at_bar_index`                             | 正式笔确认时点；临时笔为空                                                |
| `known_at_bar_index`                                 | 事件流实际发布时间                                                        |
| `object_revision`                                    | 修订号                                                                    |

`bi.parquet` 可能包含当前尾部临时笔，因为它来自 `EventEmitter.current("bi")`；但 `self.bi` 只保存正式笔，后续 `reference_centers(self.bi)` 只使用正式笔。

### 4.3 108 课原文口径

第 62 课把笔建立在相邻顶、底分型之间，并要求顶和底之间有足够 K 线隔离。第 77 课继续讨论笔的唯一性和同类分型处理：在相邻同类分型中保留更符合极值的一方，使最终笔序列方向交替且唯一。

转成程序语言后，原文口径可概括为：

1. 笔端点来自顶分型和底分型。
2. 相邻端点类型必须相反。
3. 同类端点竞争时，保留更高的顶或更低的底。
4. 顶、底分型之间不能过近，不能把共享过多 K 线的局部波动当成笔。
5. 笔序列应形成上下交替的结构。

### 4.4 与当前程序的差异

差异点：

1. 当前程序把“不能过近”固定为 `REFERENCE_MIN_INDEPENDENT_BARS = 5`，且不是参数。原文是图形和唯一性说明，没有给出本项目这种命名常量。
2. 当前程序的正式笔确认还等待后续同类分型的价格分离。这是参考实现的因果确认规则，比静态回看图形更晚。
3. 当前程序会输出 `confirmed=false` 的临时笔供图表显示；原文只讨论最终图形结构，没有临时笔和删除事件。
4. 当前程序用 `known_at_bar_index` 保证回放时不提前显示完整历史终态。原文没有回放引擎和事件流语义。
5. 当前程序不使用 ATR 门槛，不根据屏幕显示根数改变笔的起点，也不让 Vue 重算笔。这和仓库架构约束一致。

## 5. 笔中枢

### 5.1 程序中的详细算法

笔中枢由 `reference_centers(self.bi)` 计算，只使用正式笔，不使用临时笔。当前函数是对参考实现 `compute_bi_pivots/process_down_up` 的移植。

基础函数：

```text
_low(line)  = min(line.start.price_i64, line.end.price_i64)
_high(line) = max(line.start.price_i64, line.end.price_i64)
```

扫描前置条件：

```text
len(lines) >= 5
base = 1
```

注意：程序不是从第 0 笔开始扫描，而是从第 1 笔开始。这是参考实现口径。

每轮扫描：

```text
seed_end = base + 2
```

程序取 `base` 与 `seed_end` 两条同奇偶位置的笔，用它们的价格区间判断候选核心：

```text
ZD = max(low(base), low(seed_end))
ZG = min(high(base), high(seed_end))
```

如果：

```text
ZD > ZG
```

说明没有交集，不生成中枢，本轮推进扫描基点。

如果：

```text
ZD <= ZG
```

说明有重叠，生成候选笔中枢。这里 `ZD == ZG` 的点接触在笔中枢中被允许。

中枢延长：

```text
cursor = base + 4
```

程序继续按同奇偶位置每隔两笔检查一次。只要后续同奇偶笔的价格区间与冻结核心 `[ZD, ZG]` 仍有交集，就延长中枢结束位置：

```text
max(ZD, low(cursor)) <= min(ZG, high(cursor))
```

延长只改变 `end_index`，不改变已经确定的 `ZD/ZG`。也就是说，中枢核心冻结。

离开判断：

- 如果后续同奇偶笔不再与核心相交，当前中枢状态为 `left`。
- 若该笔整个区间在 `ZG` 上方，`leave_direction = up`。
- 否则 `leave_direction = down`。

如果没有离开：

- 只有三笔参与时，`status = confirmed`。
- 超过三笔参与时，`status = extended`。

参与构件：

程序虽然用 `base` 和 `base + 2` 两条同奇偶笔确定核心，但输出的参与切片是：

```text
components = lines[base : end_index + 1]
```

所以 `component_count` 至少为 3，并且包含中间那条反向笔。`DD/GG` 也按这个完整切片计算：

```text
DD = min(所有参与笔的低点)
GG = max(所有参与笔的高点)
Z  = (ZD + ZG) // 2
```

`known_at_bar_index` 分两层：

1. `reference_centers()` 中，候选中枢的理论确认时间取参与笔 `known_at_bar_index` 的最大值。
2. `_sync_objects()` 发布事件时，再取 `max(理论确认时间, 当前结构重算触发时点)`，避免后发现对象被回填到过去。

### 5.2 程序表达与位置

程序位置：

- `ReferenceCenter` 数据类：`python/src/tvbt/chan/reference.py:70`。
- 低高点函数：`python/src/tvbt/chan/reference.py:86`, `python/src/tvbt/chan/reference.py:90`。
- 笔中枢主扫描：`python/src/tvbt/chan/reference.py:364`。
- `base = 1`：`python/src/tvbt/chan/reference.py:369`。
- `seed_end = base + 2`：`python/src/tvbt/chan/reference.py:371`。
- 核心重叠判断：`python/src/tvbt/chan/reference.py:372`。
- 冻结核心 `ZD/ZG`：`python/src/tvbt/chan/reference.py:377`。
- 延长扫描：`python/src/tvbt/chan/reference.py:379`。
- 离开方向：`python/src/tvbt/chan/reference.py:386`, `python/src/tvbt/chan/reference.py:392`。
- 理论确认时点：`python/src/tvbt/chan/reference.py:406`。
- Python 生成 `zhongshu` 对象：`python/src/tvbt/chan/engine.py:378`。
- `component_kind = bi`、`analysis_level = stroke`：`python/src/tvbt/chan/engine.py:403`。
- 事件同步：`python/src/tvbt/chan/engine.py:643`, `python/src/tvbt/chan/events.py:65`。
- Parquet Schema：`python/src/tvbt/chan/storage.py:48`。
- Go 读取结构：`internal/calculation/results.go:170`, `internal/calculation/results.go:272`。
- OpenAPI 契约：`contracts/openapi.yaml:1260`。
- Vue 矩形区域投影和绘制：`web/src/chart/chanPrimitive.ts:75`, `web/src/chart/chanPrimitive.ts:109`, `web/src/chart/chanPrimitive.ts:262`。

笔中枢输出名为 `zhongshu`，字段包括：


| 字段                             | 含义                                  |
| ---------------------------------- | --------------------------------------- |
| `object_id`                      | 由核心起始笔和种子结束笔生成的稳定 ID |
| `start_bar_index` / `start_time` | 参与切片第一笔起点                    |
| `end_bar_index` / `end_time`     | 当前结束笔终点                        |
| `zd_i64` / `zg_i64`              | 冻结核心下沿和上沿                    |
| `dd_i64` / `gg_i64`              | 参与切片完整外包络                    |
| `z_i64`                          | 核心中轴                              |
| `analysis_level`                 | 当前为`stroke`                        |
| `component_kind`                 | 当前为`bi`                            |
| `component_count`                | 参与笔数量，至少 3                    |
| `status`                         | `confirmed`、`extended` 或 `left`     |
| `leave_direction`                | `up`、`down` 或 `null`                |
| `confirmed_at_bar_index`         | 理论确认时间                          |
| `known_at_bar_index`             | 实际进入事件流的可知时点              |
| `object_revision`                | 修订号                                |

缓存与 API：

- Python 写入 `zhongshu.parquet` 和 `events.parquet`。
- Go 在 `internal/calculation/results.go:259` 后读取全部 Chan 语义对象，并按查询范围过滤。
- Vue 不重新计算中枢，只把 `zhongshu` 投影为主图矩形区域。

### 5.3 108 课原文口径

第 17 课建立“走势类型”和“走势中枢”的基础概念：走势分为趋势和盘整，趋势至少包含两个同级别中枢，盘整只包含一个中枢。

第 18 课给出走势中枢的数学定义：由三个连续次级别走势类型的重叠部分确定，中枢区间可写成三段高低点的最大低点到最小高点。

第 20 课进一步给出 `ZD/ZG/DD/GG` 口径，并把与中枢方向一致的次级别走势段记为 `Z` 段。它说明中枢延伸等价于后续 `Z` 段区间与 `[ZD, ZG]` 继续重叠；不再重叠则可能产生新中枢、趋势延续或更高级别结构。

### 5.4 与当前程序的差异

差异点：

1. 原文的标准对象是“走势中枢”，构件是连续次级别走势类型。当前 `zhongshu` 是“笔中枢”，构件口径为 `component_kind = bi`。它是工程上的底层兼容对象，不等同于完整 108 课里的标准走势中枢。
2. 当前程序中更接近 108 课标准中枢的是 `segment_zhongshu`，它以已确认段为构件，且要求 `ZD < ZG`。笔中枢 `zhongshu` 允许 `ZD == ZG` 点接触。
3. 当前笔中枢扫描从 `base = 1` 开始，并要求 `len(lines) >= 5`。原文没有“跳过第 0 笔”或“至少已有 5 笔后才扫描”的代码规则。
4. 当前程序用 `base` 和 `base + 2` 两条同奇偶笔确定核心，再把中间笔也纳入 `component_count` 和 `DD/GG`。这对应第 20 课对同向 `Z` 段重叠的简化口径，但不是第 18 课三段高低点公式的逐字实现。
5. 当前程序冻结 `ZD/ZG`，后续只延长时间和更新外包络。这与第 20 课的中枢延伸口径相容，但和某些会动态收缩核心区的实现流派不同。
6. 当前程序可能因参考实现的 `base` 推进规则输出相邻参与笔有重叠的笔中枢。原文在标准走势分解中更强调同级别走势中枢、趋势和扩张的级别关系，因此不能把这些笔中枢直接当作标准趋势判断依据。
7. 当前程序为所有中枢保存 `known_at_bar_index` 和修订事件，防止回放提前显示；原文没有工程回放语义。

## 6. 当前实现与原文口径的总差异表


| 概念      | 当前程序实现                                             | 108 课原文口径                                     | 差异判断                                                                 |
| ----------- | ---------------------------------------------------------- | ---------------------------------------------------- | -------------------------------------------------------------------------- |
| 独立 K 线 | `IncludedBar`，保存数据血缘和确认时间                    | 包含关系处理后的无包含图形                         | 程序多了数据结构、血缘和因果字段                                         |
| 包含关系  | 逐 K 线在线合并，严格推进才新增，等值进入合并            | 按方向合并相邻包含 K 线                            | 大方向一致；等值和起始分支是工程选择                                     |
| 分型      | 三独立 K 线严格高低同时更高或更低                        | 三根无包含 K 线构成顶/底分型                       | 基本一致；程序严格处理等值并记录可知时点                                 |
| 笔        | 顶底分型交替，至少 5 根独立 K 线，后续分型价格分离后确认 | 相邻顶底分型构成笔，同类分型取更极端者，笔序列唯一 | 结构方向一致；程序的后续确认和临时笔是参考实现与因果工程规则             |
| 笔中枢    | `component_kind=bi`，同奇偶笔重叠，允许 `ZD == ZG`       | 标准走势中枢由三个连续次级别走势类型重叠确定       | 名称相近但级别不同；笔中枢是工程兼容对象，标准中枢应看`segment_zhongshu` |

## 7. 维护注意事项

1. 修改包含关系、分型、笔或笔中枢算法时，必须升级 `ChanEngine.algorithm_version`，否则旧缓存会被误用。
2. 若把 `REFERENCE_MIN_INDEPENDENT_BARS = 5` 改成参数，参数必须进入缓存键、算法定义和契约测试。
3. 若改变等高等低处理规则，必须补充前缀不变性测试，因为等值边界会影响分型封存和笔释放时间。
4. 若把笔中枢改为严格 `ZD < ZG`，需要同步调整 `docs/13-chan-bi-center-segment-algorithm.md`、契约示例、AOL9 金样和前端对象数量预期。
5. Vue 不得复制这些算法。浏览器只根据 Go 返回的 `ChanObjects` 做投影、样式和可见性过滤。
6. Go 不得实现第二套缠论算法。Go 只读取 Parquet、校验范围、按 API 返回对象。
7. 所有结构对象必须保留 `known_at_bar_index`，正式回放和回测只能在该位置之后展示或消费该对象。
