# 里程碑 9 验收报告

验收日期：2026-08-01  
结论：通过

## 1. 实现范围

- 新增 SearchSpace、Objective、Constraint、Study、Evaluation 公共契约与 4 个 `/api/v1/studies` 接口；
- 独立 Python 优化器仅调用标准回测入口，不包含第二套策略或成交逻辑；
- 支持稳定顺序 grid 与显式种子的无放回 random，预算 1–100；
- 训练区间严格早于验证区间，验证结果不参与训练选参；
- 每个候选生成训练/验证两个不可变正式 run，并保存 ID、signature 和完整指标；
- Study 原子保存 manifest、Evaluation、稳定性报告、正式日志和 `_SUCCESS`；
- Vue 底部面板提供参数空间、目标、约束、预算及训练/验证对比。

## 2. 自动化门禁

执行 `scripts/accept-milestone9.ps1`：

- Go：`go test ./...`、`go vet ./...`、gofmt 全部通过；
- Python：36 tests passed，Ruff 检查/格式、mypy strict 全部通过；
- Vue：16 test files、37 tests passed，类型检查和生产构建通过；
- 契约：OpenAPI 可解析，15 个 JSON 示例通过 Schema 验证。
- 生产前端：60 modules，JS 327.86 kB / gzip 110.09 kB。

Python 测试覆盖有限搜索空间、grid 顺序、固定种子随机顺序、标准训练/验证回测调用、正式
run/Study 提交和排名。Go 测试覆盖搜索值、Study 身份、预算、范围及临时 Study 恢复。Vue
组件测试覆盖有序区间提交及训练/验证结果比较。

## 3. 跨进程 E2E

完整 17,017 根 AO2609 5m 样例的里程碑 9 smoke 结果：

- 导入 122 ms，热缓存 3,000 根 HTTP p95 27.07 ms；
- 缠论 857 个对象、21,950 个因果事件、16 个检查点，不存在 segments 线段产物；
- 回测 447 笔交易，回放/回测信号逐条一致；
- seeded random Study 完成 3 个候选和 6 个正式训练/验证 run；
- 3 个 Evaluation 排名完整，至少一个候选满足最少交易硬约束；
- 训练集选中候选在验证集排名第 1；
- Study Schema、全部 run `_SUCCESS`、任务恢复和缓存安全清理均通过。

性能复验：Go 热缓存 3,000 根为 1.764–1.797 ms/op（五轮），结构化日志写入 discard 为
4.392–4.612 μs/op（五轮）；10,000 个缠论对象批量几何与 25,000 个因果事件索引测试通过。

## 4. 数据语义与安全

- 优化器接收 dataset_id/data_revision、策略 source_hash、参数空间、目标、约束、执行模型、
  资本、随机种子和明确区间；不会读取 Vue 当前窗口；
- 训练排序先应用硬约束，再按显式目标顺序比较；null/非有限目标按最差值处理；
- 验证排名独立计算，不参与候选选择；稳定性报告记录主目标差距和训练/验证排名相关性；
- Study 与子 run 均位于 data_root，临时目录同卷原子重命名，成功目录不覆盖；
- 服务重启将活动优化任务标记为 interrupted，过期 Study 临时目录移入 trash。

## 5. 已知限制

- 本里程碑不实现 AI 自动调参、贝叶斯优化、walk-forward 或手续费/滑点敏感性批量分析；
- random 首先构造有限参数笛卡尔积，因此组合数限制为 100,000；
- 示例 UI 一次配置一个数值参数；公共 API 支持多个参数维度和多目标顺序；
- 本报告验收时缠论尚未实现段；该历史限制已由 C-026 和算法 3.0.0 取代。

## 6. 样例数据

未修改 `samples/30#AO2609.txt`。验收复制到隔离临时 data_root，动态生成交易日历，完成后
安全清理；未新增或修改持久样例行情。
