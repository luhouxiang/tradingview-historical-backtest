# 缠论108课形态与算法集

版本：`1.0.0`  
整理日期：`2026-08-19`  
目标：把“教你炒股票”108课中可程序化的形态、状态转换、买卖点与操作程序，整理成可追溯、可测试、可逐步实现的规格包。

## 先看结论

本包不是一个声称“必赚”的交易系统。“盈利算法”在这里严格指：**依据原课程规则形成、可回测的候选信号与仓位决策逻辑**。任何收益、胜率或“理论保证”都必须在目标市场、交易制度、数据质量、手续费、滑点、停牌/涨跌停和样本外条件下重新验证。

程序实现必须遵循这一条流水线：

```text
原始K线
  -> 按方向消除包含关系
  -> 顶/底分型
  -> 笔（候选/确认/延伸）
  -> 线段（特征序列、两类破坏）
  -> 标准最小中枢（组件必须是线段）
  -> 各级别中枢与走势类型
  -> 背驰/盘整背驰与三类买卖点
  -> 多级别完全分类
  -> 策略、仓位与执行
```

最重要的实现边界：

1. **笔的端点**是经包含处理后、被笔划分算法选中的顶/底分型极值；同类分型出现更极端者时，尚未确认的端点会被替换。
2. **线段的端点不必是该线段的绝对最高/最低点**。第78课明确给出这种“古怪线段”；当线段作为中枢组件时，才可将其标准化为完整高低区间。
3. **笔不能构成108课严格口径的标准中枢**。第83、91课明确说明标准最小中枢以线段为组件。若项目要保留“三笔重叠”，只能命名为非标准实验对象，禁止与标准中枢信号混用。
4. **第三类买点边界是闭区间口径**：第一次次级别回试的低点“不跌破ZG”，即 `low >= ZG`；第三类卖点镜像为 `high <= ZD`。
5. MACD、均线、布林、缺口、分型强弱、Zn监视器都是辅助或经验层；不能越过结构前提独立制造标准买卖点。
6. 所有实时对象至少分为 `candidate`、`confirmed`、`invalidated`；回测成交不得早于 `confirmed_at`，否则就是未来函数。

## 目录

- `docs/00_scope_and_evidence.md`：范围、证据等级、整理方法。
- `docs/01_morphology_taxonomy.md`：六层形态总表与互斥关系。
- `docs/02_pipeline_and_state_machine.md`：事件流、状态机、确认时点。
- `docs/03_strategy_cards.md`：逐个策略的人读版。
- `docs/04_risk_and_backtest_protocol.md`：风控、回测与防过拟合。
- `docs/05_ambiguities_and_decisions.md`：108课内部口径演进与本包决策。
- `docs/06_source_traceability.md`：课次—规则—算法映射。
- `docs/07_image_test_oracles.md`：24张原课图与用户回归图的用途。
- `docs/08_implementation_roadmap.md`：推荐开发顺序与验收门槛。
- `specs/morphology_catalog.json`：机器可读形态目录。
- `specs/algorithm_catalog.json`：机器可读策略目录。
- `specs/invariants.json`：跨模块不变量。
- `specs/state_machine.json`：在线状态转换规格。
- `specs/config.example.json`：无隐式默认值的示例配置。
- `schemas/*.schema.json`：核心JSON契约。
- `fixtures/*.json`：合成结构测试与策略情景测试。
- `tests/reference_oracle.py`：无第三方依赖的参考判定器。
- `tests/test_catalog_contract.py`：目录、引用、边界与未来函数检查。
- `assets/original/`：108课网页出现的24张原图。
- `assets/user_cases/`：对话中的待回归图；不是原课证据。
- `manifests/`：图片与来源清单。

## 证据等级

| 等级 | 含义 | 可否直接生成标准信号 |
|---|---|---|
| `FORMAL` | 课程明确给出的定义、定理、唯一划分步骤 | 可以，但必须满足全部前置条件 |
| `DERIVED` | 由多个正式规则组合出的确定性决策树 | 可以，需记录推导链 |
| `AUXILIARY` | MACD、均线、BOLL、分型强弱、Zn等辅助判断 | 不可以，只能确认/排序/风控 |
| `HEURISTIC` | 经验、概率、资金与操作建议 | 不可以，必须独立回测 |
| `EXPERIMENTAL` | 为工程需要增加、原课未定义或明确称不稳定 | 不可以与标准对象混用 |

## 快速验证

在包目录运行：

```bash
python -m unittest discover -s tests -p 'test_*.py'
python tests/reference_oracle.py
```

## 推荐实现范围

第一版只做 `FORMAL` 几何和确认时点，不下单；第二版加入事件回放和三类买卖点；第三版才加入背驰辅助与策略；最后加入成交、风控和组合层。详细门槛见 `docs/08_implementation_roadmap.md`。

## 来源与许可提醒

本包是研究、实现和测试用的二次整理，不复制108课全文。课程文本与图片版权归原权利人；原图保留来源URL与哈希，使用者应自行确认再分发权限。主要在线索引：`https://chanlun108.cn/`；单课统一为 `https://chanlun108.cn/chanzhongshuochan108ke/{lesson}.html`。

本资料仅用于研究和软件工程，不构成投资建议或收益承诺。
