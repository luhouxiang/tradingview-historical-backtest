# 里程碑 8 验收报告

验收日期：2026-08-01  
结论：通过

## 1. 环境

- Windows 10.0.26200
- Intel Core i5-13400，16 逻辑处理器
- Go 1.25.7
- Python 3.14.6
- Node.js 22.23.2 / npm 10.9.8

## 2. 自动化门禁

执行 `scripts/accept-milestone8.ps1`：

- Go：`go test ./...`、`go vet ./...`、gofmt 全部通过；
- Python：34 tests passed，Ruff 检查/格式、mypy strict 全部通过；
- Vue：15 test files、36 tests passed，类型检查和生产构建通过；
- 契约：OpenAPI 可解析，13 个 JSON 示例通过 Schema 验证；
- 生产前端：58 modules，JS 320.40 kB / gzip 108.26 kB。

## 3. 跨进程 E2E

完整 17,017 根 AO2609 5m 样例通过以下链路：TXT 扫描与导入、Parquet、catalog、K 线
范围读取、普通指标、缠论、工作区、回放、策略回测、正式 run 和结果读取。

关键结果：

- 导入 124 ms，原始文件哈希不变，零成交量 K 线 1,224 根保留；
- 热缓存 3,000 根 HTTP p95 26.19 ms；
- 缠论 857 个可见对象、21,950 个因果事件、16 个检查点；
- 不存在 segments 线段产物；
- 回放缓存复用成功，回放与回测信号逐条一致；
- 回测 447 笔交易，等价 run signature 和事实哈希一致；
- 任务重启转 interrupted、过期临时目录恢复、2 个 replay 缓存移入 trash 均通过。

## 4. 性能与浏览器检查

- Go 热缓存 3,000 根：1.75–1.90 ms/op（五轮）；
- Go 结构化日志到 discard：4.25–4.98 μs/op（五轮）；
- 10,000 个缠论对象批量 primitive 构建测试通过；
- 25,000 个因果事件索引与 seek 测试通过；
- 真实浏览器恢复 MA、缠论 StrategySource、用户绘图并展开回放面板通过。

浏览器检查曾发现 Vue 响应式绘图代理无法被 `structuredClone` 克隆。实现改为按 DrawingObject
字段复制，并加入响应式输入回归测试；修复后的全新浏览器页没有 DataCloneError。

## 5. 恢复与安全

- 任务状态采用单文件原子更新；UTF-8 和带 BOM 的合法 JSON 均可恢复；
- 活动任务重启后在服务监听前持久化为 interrupted；
- 缓存工具默认 dry-run，只处理白名单种类、合法 SHA-256 目录和 `_SUCCESS`；
- 清理与临时恢复均移入 data_root/trash，不递归删除；
- 符号链接、路径逃逸、绝对路径和未知目录由测试覆盖并拒绝。

## 6. 已知限制

- 中断任务不跨进程续算，需重新提交；
- 浏览器性能测试覆盖批量几何和事件索引，交互 FPS 仍受实际显卡、浏览器和窗口尺寸影响；
- 本报告验收时缠论尚未实现段；该历史限制已由 C-026 和算法 3.0.0 取代。

## 7. 样例数据

未修改 `samples/30#AO2609.txt`。验收每次复制到隔离临时 data_root，动态生成覆盖样例日期的
交易日历，并在完成后安全清理临时目录。
