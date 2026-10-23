# mentor 包

## 职责与边界

这里实现 Agent 服务本体：装配、工具、策略、事件、沙箱适配与评测。`__init__.py` 只有一行说明，不是启动入口，也不承担统一初始化；真正的服务入口由 `langgraph.json` 指向具体模块。

## 主要调用关系

- `langgraph.json` → `agents/main.py:factory_with_trace` → `utils/trace_wrapper.py` → `AgentFactory.factory()`。
- 工厂读 `config/nacos` 的 AgentSpec，组装模型、内置工具、MCP、子 Agent 与技能，交给 Deep Agents 建图；编译结果按 `spec_hash` 缓存在 `agents/agent_cache.py`。
- 工具链：`middlewares/tool_policy.py`（判定）→ `middlewares/tool_executor.py` → `tools/executor.py`（限时、截断、错误分类）→ 具体工具。
- 事件链：`middlewares/run_events.py` 与 `run_projection.py` 写 `events/writer.py` → `events/store.py`；`app/run_summary.py`、`app/run_events.py`、`app/approvals.py` 读同一张表。
- 沙箱链：`agents/agent_factory.py:get_backend` → `backends/sandbox_backend.py` → `utils/sandbox.py`；run 结束由 `middlewares/sandbox_lifecycle.py` 关闭连接。
- 评测链：`app/experiment_webhook.py` 或 CLI → `benchmarks/benchmark.py` → `benchmarks/experiment.py` → `benchmarks/evaluators`。
- 离线回归：`golden/runner.py` 用脚本化模型与 mock 工具重放事件序列，不经过 Nacos 与真实模型。

## 模块分工

- `agents` 负责组装与缓存，`middlewares` 负责执行阶段的读写与护栏，`tools` 负责模型可调用动作。
- `config` 管环境配置与四层合并，`config/nacos` 管远程资源；`policy` 只做规则判定，实际拦截在 `middlewares/tool_policy.py`。
- `events` 是前端契约与审计的唯一来源，`app` 只做 HTTP 适配，不重新计算业务语义。
- `budget/run_budget.py` 与 `utils/subagent_context.py` 这类上下文对象保持无 IO，方便单测。
- `backends` 只做协议适配，公共连接逻辑在 `utils/sandbox.py`；`utils/minio.py` 必须保持可独立复制，它会被打进沙箱镜像。
- `benchmarks` 负责实验编排，评分公式在 `benchmarks/evaluators`；`golden` 负责无网络回归。
- `utils` 只保留被多个模块复用的能力，不作为新业务逻辑的默认归宿。

## 修改与验证

- 调整身份、会话、模型或沙箱配置契约时，沿上述链路检查受影响模块，不要只改一处。
- 三个层面的名字互不相同：Nacos 里的 Agent 名、`langgraph.json` 的图名、Python 模块的导出名；改名时逐一核对。
- 跨模块改动同步维护对应目录的 `AGENTS.md`，避免两处描述相互矛盾。
- 验证优先级为单测 → 集成测试 → 真实 Agent 运行；不同层能证明的东西不同，不要用低层通过代替高层结论。
