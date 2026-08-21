# 图像测试素材与视觉判定

## 使用原则

原图适合做三件事：

1. 人工核对算法输出是否与原课标注一致。
2. 建立截图回归测试（线、点、标签、区间位置）。
3. 为边界反例保留可视化证据。

原图不适合直接作为唯一数值真值，因为很多图压缩、缩放或缺少完整OHLC。数值单元测试应使用 `fixtures/` 中的整数ticks；图像只做第二重oracle。

## 24张原图分类

| 课次/文件 | 主要用途 | 测试优先级 |
|---|---|---|
| 14 `lesson14.png` | 均线吻、MACD、早期B1/B2示例 | 辅助 |
| 47 `lesson47.jpg` | 日内中枢、第三卖点、分笔背驰 | 场景 |
| 54 `lesson54.jpg` | 中枢与走势完整标注 | 核心 |
| 56 `lesson56.jpg` | 跳空、二卖/三卖、中枢升级 | 核心 |
| 57 `lesson57.jpg` | 当下线段/中枢分解 | 核心 |
| 58 `lesson58.jpg` | 图解分析示范 | 场景 |
| 59 `lesson59_fenxi1/2/3.jpg` | 多张连续分析图 | 场景 |
| 60 `lesson60.jpg` | 中枢与买卖点图解 | 核心 |
| 61 `lesson61_fenxi6.jpg` | 区间套、背驰段定位 | 核心 |
| 62 `lesson62.jpg` | 分型、最小成笔、包含、线段基础 | 最高 |
| 63 `lesson63.jpg` | 线段/中枢基本概念 | 核心 |
| 64 `lesson64.jpg` | 线段与微观中枢示例 | 边界 |
| 67 `lesson67_xianduan.jpg` | 特征序列、线段两类破坏 | 最高 |
| 69 `lesson69_1/2.jpg` | 月线分型取舍、同类分型替换 | 最高 |
| 70 `lesson70_1/2.jpg` | 教科书式走势全流程 | 核心 |
| 71 `lesson71_xianduan2.jpg` | 线段破坏再辨认 | 最高 |
| 79 `lesson79.jpg` | 分型辅助、线段完成与未完成 | 核心 |
| 81 `lesson81.jpg` | 微小价差、划分更正 | 边界 |
| 88 `lesson88.jpg` | 图形生长、中阴阶段 | 核心 |
| 98 `lesson98.jpg` | 完全分类与线段破坏案例 | 场景 |

## 用户回归图

`assets/user_cases/user_case_pen_or_segment_endpoint.png` 来自本次对话，标记了“折线端点不是邻近K线最高点”的疑问。它不是原课证据，测试状态为 `needs_object_type_resolution`：

1. 先确认蓝线对象究竟是笔、线段还是上层走势连接线。
2. 若是笔，检查包含处理、同类顶分型替换和确认时点；已确认向上笔终点不应任意低于其有效顶分型极值。
3. 若是线段，第78课允许结构端点不是全段绝对高点；中枢计算应另用标准化区间。
4. 禁止仅凭截图像素判断OHLC数值，需导出同一窗口原始K线与对象日志。

建议回归断言：

```text
drawn_node.object_type is explicit
drawn_node.endpoint_ref exists
drawn_node.endpoint_price == referenced_object.structural_endpoint_price
if object_type == bi: endpoint_price == selected_fractal.extreme_price
if object_type == segment: range_high/low stored independently
```

## 视觉回归格式

后续可为每张图增加 `regions`：

```json
{
  "region_id": "L62-F5",
  "bbox": [x, y, width, height],
  "expected_labels": ["valid_minimal_bi"],
  "notes": "顶底分型间存在独立K线"
}
```

由于原图可能被网页重新压缩，视觉测试应固定本包字节哈希；若源站图片更新，作为新revision审阅，不直接覆盖旧oracle。

