# Agent 装配与缓存

## 职责与入口

- `agent_factory.py:AgentFactory` 是装配基类：读取 Nacos 规格 → 计算 `spec_hash` → 命中缓存直接返回 → 未命中时装配模型、工具、子 Agent 与沙箱 profile，再调用 `create_deep_agent`。
- `main.py` 提供 `MainAgentFactory`、`factory` 与 `factory_with_trace`；`langgraph.json` 只注册 `main_agent` 一个图，指向 `mentor.agents.main:factory_with_trace`。
- `web.py` 定义 `web_agent`，用 `@register_subagent("web_agent")` 登记；`agents/__init__.py` 导入该模块以触发注册，新增子 Agent 要在这里追加导入。
- `agent_cache.py:AgentCache` 保存已编译图，`async_subagent.py` 负责后台子代理的启动、查询与取消。

## 装配流程与契约

- 规格 hash 覆盖主 Agent 与全部子 Agent 的 `model_dump`，子 Agent 按名称排序后做规范化 JSON + SHA-256；技能内容不在 hash 内。
- `factory` 在查缓存前先 `ensure_skills`，所以技能列表或内容变化不重建图；Prompt、模型、MCP、工具白名单、子 Agent 变化才会切到新版本。
- 同一 Agent 的首次并发编译由 `_build_locks` 的 `asyncio.Lock` 串行化，锁内二次查缓存。缓存只保存图对象，不关闭被淘汰的版本，in-flight run 仍持有自己的引用。
- `AgentCache` 每个 Agent 保留 3 个版本，按 LRU 淘汰同一 Agent 的旧版本；读写都在事件循环线程内，不加锁。
- 子 Agent 由 `_load_subagent_specs` 通过注册表解析，未注册的名称被静默跳过；该处调用 `get_agent_spec` 不带 `override`，主 Agent 的 `agent_spec_override` 不会传导给子 Agent。
- `create_agent` 的中间件顺序固定：子类 `get_middlewares()` → Budget → TokenMeter → Summarization → compact 工具 → （仅 main_agent）预压缩两个 → SandboxProfileGuard → ToolPolicy → ToolExecutor → sandbox_lifecycle。策略必须排在执行器外层。
- `model` 为 `None` 时跳过全部模型相关中间件，这是测试与自定义工厂的入口，不要顺手改成必填。
- 只有声明了 `subagents` 的 Agent 才追加 `spawn_subagent` / `check_subagent` / `cancel_subagent`。

## 异步子代理

- `SubAgentSpec` 默认只读、深度 1、180 秒超时；深度检查在 `spawn` 里，超限直接抛错，禁止递归委派。
- 任务表 `_jobs` 是进程内 dict，没有淘汰策略；进程退出后未完成任务丢失，事件与 run 记录仍可查。
- `_run` 调用 `factory(label=None)`，异步子代理按 `production` 读规格，不受父 run 的 `agent_label` 影响。
- 父级只拿 `snapshot()` 摘要，结果截断到 2,000 字符；完成后不会自动唤醒父图，需要模型主动 `check_subagent`。

## 修改与验证

- 新增子 Agent：实现工厂 → `@register_subagent` → Nacos 建同名 agentspec。只有需要对外暴露成独立图时才改 `langgraph.json`。
- 改状态字段同步 `main_agent_state.py`；改装配顺序或中间件同步本文件与 `middlewares/AGENTS.md`。
- 单测覆盖 `tests/units/test_agent_cache.py`（LRU、hash 变化、并发只编译一次、trace metadata 合并）与 `test_async_subagent.py`（后台执行、超时、深度、只读）。真实装配依赖 Nacos、模型网关与 OpenSandbox，单测通过不等于真实可运行。
