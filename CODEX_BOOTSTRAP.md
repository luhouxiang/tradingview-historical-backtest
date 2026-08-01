# Codex 开工指令

## 使用方式

将本规范包解压到新仓库根目录，然后向 Codex 提交：

> 请完整阅读根目录 AGENTS.md、README.md、DECISIONS.md 和 docs/09-implementation-roadmap.md。只实现里程碑 0，不实现后续业务。先给出文件变更计划，再生成代码，最后执行该里程碑的全部验收命令并报告结果。不得更换 Go 1.25.7、Python 3.14、Vue 3、文件存储、HTTP/JSON 与轮询这些已确认边界。

里程碑 0 通过后，再逐个提交：

> 继续实现 docs/09-implementation-roadmap.md 的里程碑 N。先阅读该里程碑引用的全部规范与契约。不得顺带实现 N+1。

## Codex 必须先回答的检查清单

开始写代码前，Codex 应明确：

1. 当前里程碑的输入、输出和不在范围内的内容。
2. 将修改或创建的目录。
3. 涉及的跨进程契约。
4. 数据正确性风险。
5. 将运行的自动测试。

## 建议生成的最终仓库

~~~text
.
├── AGENTS.md
├── README.md
├── go.mod
├── cmd/chartd/
├── internal/
│   ├── api/
│   ├── catalog/
│   ├── importer/
│   ├── jobs/
│   ├── logx/
│   ├── pythonclient/
│   ├── storage/
│   └── workspace/
├── web/
├── python/
│   ├── pyproject.toml
│   ├── src/tvbt/
│   └── tests/
├── contracts/
├── config/
├── scripts/
├── tests/
└── docs/
~~~

## 里程碑 0 的目标

只生成可运行的空骨架：

- Go 健康检查、配置加载、data_root 路径保护、统一错误模型和滚动日志。
- Python 健康检查、配置加载、任务接口占位和滚动日志。
- Vue 3 外壳、路由、顶/左/右/底布局框架、API 客户端和日志队列。
- OpenAPI、JSON Schema 与生成代码/类型的脚本。
- 启动脚本与 VS Code 调试配置。
- 三端测试和 CI 基础。

里程碑 0 不读取真实行情，不画 K 线，不计算指标，不运行回测。

## 开发启动项

至少提供：

- 全部服务快速启动：不先执行完整编译。
- 全部服务调试：Go、Python、Vue 3 可分别断点。
- Go 全编译通过后启动。
- Vue 全编译通过后启动。
- Python 全测试通过后启动。

VS Code 调试输出进入 Debug Console；仅构建任务使用 Terminal。停止调试时必须可靠终止由该启动项创建的子进程，不得按端口误杀无关程序。

