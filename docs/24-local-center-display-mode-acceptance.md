# 实体中枢笔／线段显示模式验收

范围：15E 显示回归。用户确认增加显式模式选择；不恢复旧笔/线段中枢算法。

## 原因与实际证据

截图关闭了实体中枢，只打开高级别中枢；两者并非同一类对象。此外旧绘制器和对象树固定优先 BI，没有 SEGMENT 选择入口。

权威来源：SHFE.AOL9.5m，revision `2904362e62173a418d63feaf8855a3aef4b61ce5b0027e7801785baf9d8302fe`，算法 17.0.0，缓存 `68e48bcbe2ef7c4f570fb5dd9db5fb9d7a24dc4cfab29d7c987d8f4c8f461e57`。

红框对象 `local-center-28be434980add1b59423` 的三条种子实际区间为 `[2977,3120]`、`[3076,3120]`、`[3076,3212]`，核心为 `[3076,3120]`。种子端点从 2025-07-03 11:05 到 07-10 14:20；形成确认 K45696（界面一基编号），不是端点形成时提前确认。

后续确有 `[3270,3515]`、`[3155,3168]`、`[3000,3034]` 等 SEGMENT 实体中枢。并非每个重叠窗口都单独新增一个中枢，仍以 Python 固定核心、延伸与分界规则为准。

## 修改

- `strategy-source-config.schema.json` 新增可选 `local_center_mode: BI | SEGMENT`；Go 验证、原子保存、读取，生成契约类型同步更新。缺省 BI，不改旧布局格式。
- 对象树“实体中枢依据”选择同时开启实体中枢，保留高级别开关和其他图层选择。
- 共享展示筛选函数供图表、回放及对象树使用；连接和确认事件与所选中枢一致。空模式不借用另一种模式的对象。
- 图表统计只计入筛选后的实体中枢。模式自动保存并在刷新时恢复；不创建计算任务，不改 Python 事实、known_at 或正式回测。
- 当前 AOL9 策略配置通过 Go 工作区存储器原子更新至 revision 112：SEGMENT、实体中枢开启，其余开关保留。旧运行中的 Go 调试实例须重启才能读取新增字段；未中断用户调试进程。

## 验证与复现

前端单元/组件覆盖模式切换、缺省兼容、空模式、连接和事件隔离、保存与重新挂载恢复。Go 覆盖模式持久化和非法值拒绝。OpenAPI/24 个契约示例校验。

最终结果：35 个前端测试文件、148 项测试通过；Vue/TypeScript 及生产构建通过；`go test ./...`、`go vet ./...` 通过；24 个契约示例及 OpenAPI 校验通过。真实中枢切换 E2E 1 项通过，原 12000 根分页 E2E 回归 1 项通过。

浏览器测试 `web/e2e/local-center-mode.spec.ts` 使用固定 Python 解释器只读提取本地 Parquet 事实，渲染真实 AOL9 K44001～K50000，真实 Vue/Lightweight Charts/ChanPrimitive；接口在夹具中提供已保存事实，非重新运行算法。断言四个目标对象进入几何、坐标有效、切换 BI 成功、高级别数量保持、不创建计算任务、无页面异常。

先按 docs/23 启动隔离 Vite 15173，再在 web 目录设置：

```powershell
$env:TVBT_LOCAL_CENTER_CACHE = 'E:/work/go/tradingview-historical-backtest/trading-data/cache/chan/68e48bcbe2ef7c4f570fb5dd9db5fb9d7a24dc4cfab29d7c987d8f4c8f461e57'
$env:TVBT_LOCAL_CENTER_BARS = 'E:/work/go/tradingview-historical-backtest/trading-data/normalized/SHFE.AOL9.5m/2904362e6217/bars.parquet'
npx playwright test e2e/local-center-mode.spec.ts
```

截图 `trading-data/acceptance/local-center-mode-segment.png` 已人工检查：目标中枢及后续框清晰可见。夹具不加载 MACD，因此副图留空；对象树不接入全量摘要接口，因此摘要列表为空。这两点不代表生产数据丢失。

未修改 Python，未重跑整套算法测试；本验收证明展示已有事实，不重新论证算法中枢定义。当前运行窗口的生效需重启旧 Go 调试服务后刷新。
# 历史说明（已由里程碑 15F 取代）

本文记录的是曾经使用“实体中枢依据：笔／线段”单选下拉菜单的验收结果。里程碑 15F 已删除该菜单：当前界面使用“笔中枢”和“线段中枢”两个独立开关，可单独或同时显示；递归高级别中枢不再生产或显示。本文不再代表当前契约或界面。

