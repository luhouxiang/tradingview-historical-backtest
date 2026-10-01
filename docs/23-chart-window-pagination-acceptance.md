# 大范围缩放后左侧线段、高级别中枢与 MACD 缺失：修复验收

日期：2026-09-22。范围：里程碑 15E 的图表范围显示回归，不新增算法里程碑。

## 根因

`internal/calculation/service.go` 的 `Results()` 拒绝超过 5000 个 bar_index 的单次查询。原 `ChartGroup.vue` 在缩小视图或多次向左加载后，将整个可视索引区间直接交给结果接口。当区间超过 5000，指标和缠论查询同时失败，画面仍保留较窄范围的旧对象及 MACD。

原防抖回调还使用无错误处理的 `void Promise.all(...)`，未给图表明确失败提示；并发范围响应也没有代次隔离，慢旧响应可能覆盖新视口。

截图中右侧尚有段和 MACD，而左侧同时缺失，与上述路径吻合。本次以能够拒绝超限请求的测试接口复现并验证修复；没有连接用户原来的运行浏览器，也未声称已经在原窗口恢复。

## 实现

- `web/src/chart/calculationWindow.ts`：最多 5000 个连续索引一页，逐页读取已完成结果；跨页核对 job、cache、dataset、revision 和算法身份。
- 普通指标按 bar_index 合并排序，保留 null 预热值和多输出列；跨页长线段、高级别中枢等按各类别的 object_id 去重，保留较新 revision。
- 合并结果只包含绘图数据，不借用某一页的 coverage/checksum 冒充整个区间的 API 响应。
- `ChartGroup.vue`：普通指标与缠论共享分页机制；从用户产生新范围请求时就使旧响应失效，后续旧分页停止，避免倒覆盖。
- 换数据、信号定位和卸载也隔离旧请求；范围加载失败在图表内显示中文原因，下一次拖动/缩放成功后清除。

无 Go/Python 算法修改，无 API/JSON Schema 修改，不调整图层可见性，不补造中枢，不重算全历史，不覆盖正式回测。

## 验证

| 验证 | 结果 |
|---|---|
| 1 / 5000 / 5001 / 12001 索引的分页边界 | 通过 |
| 跨页长线段、高级别中枢去重 | 通过 |
| 数据 revision 不一致、列长度错误 | 明确拒绝，通过 |
| 过期分页停止、失败后重试 | 通过 |
| 从尾部 3000 向左加载至 6000，再显示完整视口 | 线段、中枢、MACD 完整，通过 |
| 慢旧视口返回后不得覆盖新视口 | 通过 |
| 错误提示、成功重试清除提示 | 通过 |
| Vue/TypeScript 检查与生产构建 | 通过 |
| 全部前端单元/组件测试 | 34 个文件、144 项通过 |
| Chromium 浏览器回归 | 1 项通过 |

浏览器测试使用 12000 根明确标为 TEST 的合成 OHLC，真实 Vue 组件、Lightweight Charts 和 ChanPrimitive。接口严格拒绝超过 5000 的请求；初始只提供尾部 3000，再通过实际时间轴范围变更触发 1500 根历史分页，最终一次展示全部 12000 根。断言：20 段、3 个高级别中枢（含跨页中枢）和完整 12000 个 MACD 值；左侧线段/中枢具有有效屏幕坐标；无页面异常、无创建计算任务请求。

已检查浏览器截图：`trading-data/acceptance/chart-window-pagination.png`。这是合成回归夹具，不是用户 AOL9 原始行情截图。

## 可重复运行

```powershell
cd web
npm run build
npm test -- --run
```

无 Go/Python 服务的隔离浏览器验证，终端一启动仅用于夹具的 Vite 服务：

```powershell
cd web
node --input-type=module -e "import { createServer } from 'vite'; import vue from '@vitejs/plugin-vue'; const server=await createServer({configFile:false,plugins:[vue()],server:{host:'127.0.0.1',port:15173,strictPort:true}}); await server.listen();"
```

终端二运行：

```powershell
cd web
$env:TVBT_CHART_WINDOW_E2E = '1'
npx playwright test e2e/chart-window.spec.ts
```

结束后关闭自己启动的夹具服务。新增可重复样例位于 `web/e2e/chart-window.html` 与 `chart-window.spec.ts`，没有修改用户的历史行情文件。

## 限制

本次验证的是范围加载、合并和渲染，并非重新验算高级别中枢是否应当生成；若相应图层关闭或 Python 权威结果本来没有该对象，不会凭空显示。Go/Python 未修改，未重新执行其全套算法测试。大范围需要更多缓存读取请求，但每页严格受既有接口上限约束，已过期请求不再继续后续分页。
