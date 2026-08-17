# Codex 使用说明

本目录是缠论108课“笔”实现的测试依据。处理相关代码前必须完整阅读：

1. `README.md`
2. `docs/implementation_invariants.md`
3. `fixtures/bi_cases.json`
4. `docs/visual_oracles.md`

## 不可违反的规则

1. 分型只能在K线包含关系处理完成后识别。
2. 包含处理必须顺序进行；包含关系不具有传递律。
3. 向上包含取较高高点和较高低点；向下包含取较低高点和较低低点。
4. 一笔必须连接有效的一顶一底，方向上下交替。
5. 端点分型不得共用K线；端点分型之间至少有一根不属于任一端点分型的处理后K线。
6. 无有效反向分型时，后顶更高替换前顶，后底更低替换前底。
7. 笔端点必须保留极值来源原始K线ID，不能只保存合成K线时间。
8. 候选分型、候选笔、确认笔必须分开；回测只能使用当时已经确认的信息。
9. 所有价格先转成整数最小跳动单位；禁止用浮点 epsilon 决定相等或突破。
10. 线段结构端点、线段区间极值和标准化线段端点必须另存，禁止覆盖笔端点。
11. 笔不能直接作为中枢构件；最低中枢构件是线段。
12. 原图是判定证据，不是机器可读OHLC。不得从像素臆造原始行情数据。

## 实现建议

建议分别实现并测试：

```text
merge_inclusion(raw_k, direction)
build_processed_klines(raw_klines)
detect_fractals(processed_klines)
reduce_same_type_fractals(fractals)
assemble_bi(fractals, processed_klines)
update_bi_state(new_processed_kline)
```

每个输出对象至少保存：

```text
id
direction / fractal_type
status: candidate | confirmed | invalidated
start/end processed index
start/end raw extreme source index
price_ticks
theoretical_at
confirmed_at
invalidated_at (可空)
source_rule_ids
```

修改算法后应先跑自动夹具，再做原图视觉回归。视觉金样与数值夹具冲突时，不要猜测：先检查是否混用了原始K线与包含处理后K线，或混用了笔和线段。

