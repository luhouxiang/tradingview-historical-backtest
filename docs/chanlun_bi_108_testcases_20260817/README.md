# 缠论108课：笔实现测试资料包

本资料包把《教你炒股票108课》中与“笔”直接相关的原图、原文规则和可执行数值夹具整理在一起，供 Codex 在实现或审查笔算法时使用。

## 快速使用

1. 先读 `AGENTS.md` 与 `docs/implementation_invariants.md`。
2. 以 `fixtures/bi_cases.json` 作为自动化测试输入与期望输出。
3. 以 `manifests/image_regions.json` 定位第62课原图中的图1—图9。
4. 用 `docs/visual_oracles.md` 对照原图做人工视觉回归。
5. 将 `tests/pytest_adapter_template.py.example` 复制到项目测试目录，替换其中的适配器函数。
6. 在本目录执行 `python -m unittest discover -s tests`，验证资料包自身的一致性。

## 采用的理论口径

本包采用 `chanlun_108_strict`：

- 只以108课内的第62、65、69、77、78、79、81、83、91课为规则依据；
- 不混入108课之外后来流行的“新笔”“放宽笔”等实现；
- 所有分型判断都针对顺序完成包含处理后的K线；
- 原始K线图上的肉眼最高、最低不必然等于处理后分型极值；
- 笔与线段严格分层，线段的“端点可非整段最高低点”不能套到笔；
- 价格夹具使用整数最小跳动单位，不使用浮点 epsilon。

## 正确处理管线

```text
原始K线
  -> 按顺序、按方向处理包含关系
  -> 无包含K线序列
  -> 识别顶/底分型
  -> 同类分型取舍
  -> 检查端点分型间隔及结合律
  -> 组成上下交替的笔
  -> 管理候选、确认、延伸和端点修订状态
```

## 自动化用例范围

`fixtures/bi_cases.json` 包含以下核心类别：

| 类别 | 覆盖内容 |
|---|---|
| 分型识别 | 顶分型、底分型、上升/下降三K无分型 |
| 包含处理 | 向上包含、向下包含、极值来源K线 |
| 成笔约束 | 共用K线拒绝、无独立K线拒绝、最小有效笔 |
| 同类分型取舍 | 后顶更高、后底更低、同价顶保留先出现者 |
| 精度 | 0.08价格差不得被舍入或 epsilon 吞掉 |
| 当下状态 | 第91课四状态及允许、禁止转移 |
| 边界 | 顶分型不必然延伸成笔、笔不能组成中枢、段规则不得污染笔 |

数值夹具是依据原图拓扑重新编码的规范化整数样本，不是从图片反推的原始市场OHLC。

## 原图分层

### 核心原图

- `assets/original/lesson62.jpg`：图1—图7是笔实现的基础视觉规范；图8—图9用于区分笔与线段。
- `assets/original/lesson69_1.jpg`：月线真实图上的有效顶底分型与被打叉分型。
- `assets/original/lesson69_2.jpg`：实时划分中“不构成一笔”的标记及后续线段编号。

### 补充边界原图

- `lesson67_xianduan.jpg`
- `lesson71_xianduan2.jpg`
- `lesson79.jpg`
- `lesson81.jpg`

这些图主要用于测试线段边界，不能作为修改笔端点规则的依据。

## 目录

```text
AGENTS.md
README.md
assets/original/                 108课镜像原图
docs/implementation_invariants.md
docs/source_notes.md
docs/test_case_index.md
docs/visual_oracles.md
fixtures/bi_cases.json           自动化夹具
manifests/image_regions.json     图中区域定位
manifests/sources.json           来源与用途
schemas/bi_cases.schema.json
tests/reference_oracle.py        小型独立规则参考
tests/test_fixture_contract.py   资料包自检
tests/pytest_adapter_template.py.example
SHA256SUMS
```

## 重要限制

- 第69课真实行情图没有附带可复原的完整OHLC，因此只作为视觉金样。
- 第62课图形是几何示意图，数值测试只保证相同拓扑与判定结果。
- 第81课的0.08差异用于强调逐最小跳动单位精确比较，不代表可从该插图重建完整行情。
- 本包用于算法一致性测试，不构成交易建议或收益保证。
