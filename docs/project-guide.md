# Mentor 项目开发指南

> 面向开发者的项目说明：代码结构、请求链路、配置分层、沙箱生命周期、测试与发布。
> 基础设施与端口拓扑见 [architecture.md](architecture.md)。

## 1. 项目定位

Mentor 是一个基于 **LangGraph / DeepAgents** 的 Agent 服务：

- **Nacos**：AgentSpec、Prompt、MCP、Skill、`sandbox_config` 的运行时控制面；
- **LiteLLM**：统一模型网关，Agent 不直接持有厂商密钥；
- **OpenSandbox**：隔离执行环境，按用户持久化 `/root`，共享只读 `/shared`；
- **MinIO**：保存运行时文件与交付产物；
- **Langfuse**：Trace、Dataset、Experiment 与告警。

## 2. 快速开始

```bash
# 1. 准备配置
cp .env.example .env

# 2. 启动基础设施
make docker-recreate

# 3. 首次使用/修改 Dockerfile 后构建沙箱镜像
docker build -t opensandbox-python-node:1.0 -f docker/sandbox/Dockerfile .

# 4. 启动前体检（Nacos、AgentSpec、模型网关、MinIO、sandbox_config）
uv run mentor config doctor
# 或：make dev-check（先 doctor 再启动）

# 5. 启动 Agent
make dev
```

常用检查：

```bash
uv run mentor config schema
uv run mentor config validate --file path/to/agent_spec.json
uv run mentor config validate --agent main_agent
make test-units
```

## 3. 代码结构

```text
src/mentor/
├─ agents/
│  ├─ agent_factory.py        # Nacos 规格 → 模型/工具/子 Agent/图；spec_hash 缓存
│  ├─ agent_cache.py          # (agent, spec_hash) LRU；每 Agent 默认 3 个版本
│  ├─ subagent_registry.py    # @register_subagent 注册表
│  ├─ main.py                 # 主 Agent：MainAgentState + run_projection
│  └─ web.py                  # web_agent 子 Agent
├─ backends/
│  └─ sandbox_backend.py      # 文件/命令/删除 → OpenSandbox；run 级连接缓存
├─ config/
│  ├─ layers.py               # 四层配置合并 + AgentSpecOverride
│  ├─ errors.py               # ConfigNotFound / ConfigUnavailable
│  ├─ doctor.py               # 启动前配置体检
│  ├─ nacos/                  # AgentSpec / Prompt / MCP / Skill / 通用配置
│  └─ *_settings.py           # pydantic-settings（.env）
├─ middlewares/
│  ├─ turn_summary.py         # 前端可见消息摘要
│  ├─ sandbox_lifecycle.py    # after_agent 关闭当前 run 的沙箱连接
│  ├─ tool_executor.py        # 工具调用 + 副作用前后事件
│  ├─ run_events.py           # run/turn/model 生命周期事件
│  ├─ token_meter.py          # 调用前后 token 计量
│  ├─ context.py              # 显式压缩 trigger/keep
│  └─ run_projection.py       # 写消息/压缩事件 + 修剪 checkpoint
├─ policy/
│  └─ tool_policy.py          # YAML 规则 → allow/prompt/deny
├─ events/
│  ├─ models.py               # RunEvent / RunSummary 前端契约
│  ├─ context.py              # run/thread/user 上下文
│  ├─ store.py                # Postgres 事件表（测试用 InMemory）
│  ├─ projection.py           # 消息→事件、checkpoint 修剪
│  ├─ writer.py               # 写事件 + outbox 兜底
│  └─ outbox.py               # 失败事件落盘与重放
├─ security/auth.py           # LangGraph 鉴权（当前为 MOCK_USER）
├─ tools/
│  ├─ specs.py                # ToolSpec / @register_tool / 风险与暴露级别
│  ├─ executor.py             # 限时、错误分类、截断落盘、结果信封
│  ├─ artifacts.py            # artifact:// 存储与读取
│  └─ *.py                    # deploy / web / time / ask_user_question
├─ benchmarks/                # Langfuse Dataset / Experiment / 评测器
├─ app/                       # Webhook / summary / trace / usage / SSE 路由
└─ utils/                     # 模型、缓存、MinIO、沙箱、trace 等通用工具
```

## 4. 请求链路

```mermaid
sequenceDiagram
    participant U as User
    participant G as LangGraph
    participant F as AgentFactory
    participant N as Nacos
    participant S as SandboxBackend
    participant T as Tools/MCP
    participant M as MinIO
    participant L as Langfuse

    U->>G: 发起对话
    G->>F: factory(override, label)
    F->>N: get_agent_spec + ensure_skills
    N-->>F: AgentSpec / Prompt / MCP / Skill
    F->>F: spec_hash → 缓存命中/编译
    F-->>G: 已编译图
    G->>T: 模型选择工具
    T->>S: aexecute / upload / download / delete
    S->>S: 按 (user, thread) 复用沙箱
    T->>M: deploy 沙箱内直传产物
    G->>L: CallbackHandler 上报 trace
    G->>S: after_agent → aclose_run
```

要点：

- `AgentFactory` 按 `(agent_name, spec_hash)` 缓存图；Prompt/模型/MCP/工具/子 Agent 变化会生成新 hash。
- 子 Agent 规格参与主 Agent 的 hash，子 Agent 更新会触发主图重建。
- `spec_hash` 写入图 metadata，并随 trace 进入 Langfuse。
- `x-litellm-session-id` 由 `ReasoningChatModel` 每次请求注入，图缓存不会固定首个 thread 的 header。

### 4.1 工具执行链

```text
Agent 工具调用
  → ToolExecutorMiddleware
    → ToolSpec（risk / exposure / timeout_s / max_output_chars）
      → ToolExecutor
        → 内置工具 / 业务工具 / MCP 工具
          → ToolMessage.content（预览）
          → ToolMessage.artifact（{ok,data,error_kind,truncated,artifacts,duration_ms}）
```

- 业务工具实现后用一行 `@register_tool(risk=..., timeout_s=...)` 登记；Nacos AgentSpec 的 `tools` 白名单决定是否装配。
- 所有工具（含 DeepAgents 内置、MCP、业务）都经过同一个中间件：限时 → 执行 → 截断/落盘 → 错误分类。
- 错误分类：`input`（模型可修复）、`policy`（策略拒绝）、`timeout`、`system`（终止 run）。
- 超过 `max_output_chars`（默认 8000）的输出落到 `TOOL_ARTIFACT_DIR`，`content` 返回预览，`artifacts` 返回 `artifact://`；`read_artifact` 自动可用。
- MCP 默认 `deferred`：不直接进 prompt，模型先 `tool_search`，再 `call_tool(name, arguments)`；如要旧行为，把 server 配成 `exposure: direct`。
- MCP server 可声明 `auth`、`timeout_seconds`、`allowed_tools`、`exposure`、`risk`；`auth` 建议从密钥管理注入，不要提交到仓库。

### 4.2 会话投影：checkpoint 最小化

```text
Agent 开始
  → RunEventsMiddleware：run_started / turn_started / model_request / model_response
工具调用
  → ToolExecutorMiddleware：tool_call（副作用前）+ tool_result/approval（副作用后）
Agent 结束
  → run_projection：message / artifact / compression + checkpoint 修剪
  → run_finished
GET /runs/{run_id}/summary?after_seq=0   # session_summary
GET /runs/{run_id}/trace                # 状态 + 最后完成工具 + pending
GET /runs/{run_id}/usage                # token / 压缩
GET /runs/{run_id}/events               # SSE（Last-Event-ID 重连）
```

- `turn_summary` 不再写 state；UI 摘要由 `run_events` 投影计算。
- checkpoint 只保留最近 `RUN_EVENTS_KEEP_LAST_TURNS`（默认 10）轮；旧轮次进入 `history_summary`，工具输出全文留在事件表。
- 压缩日志记录 `before_tokens / after_tokens / kept_messages / summary_hash`；token 用量取自消息 usage metadata。
- 事件类型：`run_started / turn_started / model_request / model_response / tool_call / tool_result / approval / artifact / message / compression / run_finished`。
- 工具事件在副作用前后各写一条；崩溃时可用 `trace` 的 `pending_tools` 定位最后一个未完成工具。
- 事件表使用 Postgres（`mentor-postgres`），带 `schema_version`；`after_seq`/SSE `Last-Event-ID` 支持重放。
- Postgres 写失败时落到 `RUN_EVENTS_OUTBOX_DIR`，用 `mentor events replay` 重放。
- 前端 SSE/卡片验证待前端阶段完成，见 [run-events-frontend-todo.md](run-events-frontend-todo.md)。

### 4.3 工具策略与审批

```text
ToolPolicyMiddleware（最外层）
  → ToolPolicy.decide(tool, args, risk)
    → allow：进入 ToolExecutor
    → deny：返回 status=error 的 ToolMessage，不执行工具
    → prompt：approval_events(requested) → interrupt → allow/deny
      → allow：进入 ToolExecutor
      → deny：返回 policy 错误
```

- 规则文件：`TOOL_POLICY_FILE`，默认 `config/tool_policy.yaml`；`tool` 支持 glob，`pattern` 是参数 JSON 正则。
- 决策：`allow / prompt / deny`；未命中按 `ToolSpec.risk` 默认值，`destructive` 默认 prompt。
- 单 run 审批上限：`max_prompts_per_run`，超过直接 deny。
- 审批审计：`approval_events`（actor、tool、args_hash、decision、reason、ts），`GET /runs/{run_id}/approvals` 可查。
- 审批存储不可用时 **fail-closed**，不执行 prompt 工具。
- `TOOL_POLICY_ENABLED=false` 可整体回退到 allow；规则开关关闭后行为可回退。
- `interrupt` 需要 checkpointer + resume；前端审批卡待前端阶段验证。

### 4.4 上下文与 Token 治理

```text
TokenMeterMiddleware（模型调用外层）
  → 调用前 estimate_tokens
  → 调用后读取 usage_metadata
  → 写 usage 事件（estimated/input/output/total）
ToolExecutor 超长输出
  → 头 4KB + 省略标记 + 尾 1KB
  → 本地 artifact:// 可读引用
  → MinIO 全文镜像（minio_ref）
SummarizationMiddleware（显式配置）
  → trigger = 模型窗口 × CONTEXT_TRIGGER_RATIO
  → keep = CONTEXT_KEEP_MESSAGES
  → compact_conversation 工具（/compact 触发）
  → compression 事件
```

- `CONTEXT_WINDOW_TOKENS` 是模型 profile 缺失时的回退；有 `max_input_tokens` 时优先用 profile。
- `CONTEXT_PRECOMPACT_RATIO=0.60`：回答结束后、检查点修剪前，如果 token 超过窗口 60%，后台异步生成摘要，写入 `pending_context_summaries`；下一轮只读取并注入，不在请求路径调摘要模型。
- `CONTEXT_AUTO_COMPACT=false` 时自动触发阈值抬高，只保留 `compact_conversation` 手动压缩；手动压缩仍需达到正常 trigger 的 50%（默认约 51,200 tokens）。
- 工具输出超过阈值时保留头 4KB + 尾 1KB；全文写本地 artifact，并镜像 MinIO，envelope 带 `minio_ref`。
- `/compact` 由前端映射到 `compact_conversation` 工具；压缩结果写 `compression` 事件。
- Token 成本通过 `GET /runs/{run_id}/usage` 查询；usage 事件优先于 model_response 汇总。

### 4.5 模型韧性与单 run 预算

```text
LiteLLM（网关侧）
  → provider 重试 / fallback / cooldown（熔断）
Agent 侧
  → MODEL_REQUEST_TIMEOUT_SECONDS + MODEL_MAX_RETRIES=0
  → BudgetMiddleware：token/deadline 预检查
  → ToolExecutor：工具执行前 deadline 检查
  → TokenMeter：usage 事件 + served_model
```

- **重试/fallback/熔断放在 LiteLLM**，Agent 侧默认 `max_retries=0`，避免 3×3 放大。
- `MODEL_REQUEST_TIMEOUT_SECONDS` 显式设置模型请求超时，不再依赖 SDK 默认。
- `RunBudget`：
  - `RUN_BUDGET_MAX_TOKENS`
  - `RUN_BUDGET_MAX_SECONDS`
  - `RUN_BUDGET_MAX_OUTPUT_TOKENS`（预检查预留）
  - 0 表示不限制；可通过 `configurable.run_budget` 单次 run 覆盖
- 预算在模型调用前检查，超限抛 `BudgetExceeded` 终止；主 Agent 与子 Agent 共享同一 ContextVar。
- `usage.served_model` 记录实际返回模型，便于发现 LiteLLM fallback。
- **成本预算**由 LiteLLM key/team budget 承担；Agent 侧 token 预算是防单 run 失控，不是账单级成本控制。

## 5. 配置分层与灰度

```text
Pydantic defaults  <  .env  <  Nacos AgentSpec  <  request override
```

| 层 | 来源 | 说明 |
|---|---|---|
| defaults | Pydantic 字段默认值 | 最小可运行配置 |
| `.env` | `BaseSettingsWithEnv` | 地址、密钥、TTL、label |
| Nacos | AgentSpec / Prompt / `sandbox_config` | 运行时主配置 |
| request override | `configurable.agent_spec_override` | 单次 run 临时覆盖，不写回 Nacos |

请求级覆盖示例：

```json
{
  "configurable": {
    "agent_label": "staging",
    "agent_spec_override": {
      "system_prompt": "本次使用精简提示",
      "model": { "temperature": 0.2 }
    }
  }
}
```

边界：

- `NACOS_LABEL` 支持 `production` / `staging`；AgentSpec 和 Prompt 跟随 label。
- MCP 是 Nacos 管理端全局配置，不随 label 切换。
- Skill 下载仍固定 `production`；staging 技能需要后续把 label 纳入技能存储路径。

配置体检：

```bash
uv run mentor config doctor
```

检查 Nacos AgentSpec、必填字段、模型网关 `/models`、MinIO 桶、`sandbox_config`。任一项失败返回非 0。

## 6. 沙箱生命周期

```text
SandboxBackend._get_or_create_sandbox
  key = (user_id, thread_id)
  ├─ 命中进程内缓存 → 更新 last_used_at，直接返回
  └─ 未命中 → per-key lock → get_sandbox(user_id) → OpenSandbox.connect/create
       └─ 写入缓存

Agent 结束 → sandbox_lifecycle after_agent → aclose_run
命令通道异常 → aexecute 丢弃当前连接
deploy 自建 backend → finally aclose_run
```

- **PVC**：`<tenant>-<sha256(user_id)[:12]>`；`/root` 持久，`/shared` 只读共享。
- **严格 Store**：生产禁止 `InMemoryStore` 回落；本地脚本需显式 `OPEN_SANDBOX_ALLOW_MEMORY_STORE=true`。
- **资源/网络**：`sandbox_config` 可配 `cpu`、`memory`、`network_default_action`、`network_allow`；未配置时用 OpenSandbox 默认值。
- **空闲回收**：进程内缓存按 `OPEN_SANDBOX_IDLE_TIMEOUT_SECONDS`（默认 1800 秒）回收；0 表示交给 OpenSandbox TTL。
- **删除安全**：只允许 `/root` 下路径；父目录 realpath 解析后再做前缀检查；走文件 API，不拼接 shell。
- **迁移注意**：PVC 名变化后旧 `/root` 数据不会自动挂载，需要保留旧 PVC 或复制数据。

## 7. 关键环境变量

| 变量 | 作用 |
|---|---|
| `NACOS_SERVER_ADDRESS` / `NACOS_NAMESPACE_ID` | Nacos 地址与命名空间 |
| `NACOS_TTL` | 配置本地缓存秒数，建议 5 |
| `NACOS_LABEL` | `production` / `staging` |
| `MINIO_*` | MinIO 地址、凭据、桶名、预签名有效期 |
| `OPENAI_BASE_URL` / `OPENAI_API_KEY` | LiteLLM 网关地址与鉴权 |
| `OPEN_SANDBOX_DOMAIN` / `OPEN_SANDBOX_API_KEY` | OpenSandbox Server |
| `OPEN_SANDBOX_TENANT` | PVC 名称前缀 |
| `OPEN_SANDBOX_ALLOW_MEMORY_STORE` | 仅本地脚本设为 true |
| `OPEN_SANDBOX_IDLE_TIMEOUT_SECONDS` | 空闲连接回收时间 |
| `LANGFUSE_*` | Trace / Dataset / Webhook |
| `TOOL_ARTIFACT_DIR` | 工具超长输出落盘目录，默认 `/tmp/mentor-tool-artifacts`；生产建议挂持久卷并限制权限 |
| `RUN_EVENTS_DSN` | Run 事件表 Postgres 连接串，默认 `postgresql://mentor:mentor@127.0.0.1:5433/mentor` |
| `MENTOR_POSTGRES_PASSWORD` | compose 中 `mentor-postgres` 的密码，默认 `mentor` |
| `RUN_EVENTS_KEEP_LAST_TURNS` | checkpoint 保留最近轮数，默认 10 |
| `RUN_EVENTS_MAX_TOOL_CHARS` | 保留轮次中的工具输出上限，默认 500 |
| `RUN_EVENTS_SUMMARY_MAX_CHARS` | 历史摘要上限，默认 4000 |
| `RUN_EVENTS_OUTBOX_DIR` | Postgres 写入失败时的 JSONL outbox，默认 `/tmp/mentor-run-events-outbox` |
| `TOOL_POLICY_FILE` | 工具策略 YAML，默认 `config/tool_policy.yaml` |
| `TOOL_MINIO_ARTIFACTS` | 超长工具全文是否镜像 MinIO，默认 true |
| `MODEL_REQUEST_TIMEOUT_SECONDS` | 模型请求超时，默认 120s |
| `MODEL_MAX_RETRIES` | Agent 侧客户端重试；默认 0，交给 LiteLLM |
| `RUN_BUDGET_MAX_TOKENS` / `RUN_BUDGET_MAX_SECONDS` | 单 run token/时间预算；0 不限制 |
| `RUN_BUDGET_MAX_OUTPUT_TOKENS` | 预算预检查预留输出，默认 4096 |
| `CONTEXT_WINDOW_TOKENS` | 模型窗口回退值，默认 128000 |
| `CONTEXT_TRIGGER_RATIO` | 自动压缩触发比例，默认 0.8 |
| `CONTEXT_PRECOMPACT_RATIO` | 回答后异步预压缩水位，默认 0.60 |
| `CONTEXT_KEEP_MESSAGES` | 压缩后保留消息数，默认 20 |
| `CONTEXT_TRIM_TOKENS_TO_SUMMARIZE` | 摘要输入 token 上限，默认 4000 |
| `CONTEXT_AUTO_COMPACT` | 是否自动压缩；false 仅手动 /compact |
| `TOOL_POLICY_ENABLED` | 策略总开关，设为 false 回退 allow |

## 8. 测试与发布

```bash
# 静态检查
.venv/bin/pyright --pythonpath .venv/bin/python src tests resource

# 单元测试
.venv/bin/pytest -q tests/units

# 部署集成（需要真实 MinIO / OpenSandbox）
MINIO_INTEGRATION=1 DEPLOY_INTEGRATION=1 \
DEPLOY_SANDBOX_IMAGE=opensandbox-python-node:1.0 \
.venv/bin/pytest -q tests/intergrations/test_deploy.py

# run_events Postgres 集成（需要 mentor-postgres）
RUN_EVENTS_POSTGRES_DSN=postgresql://mentor:mentor@127.0.0.1:5433/mentor \
.venv/bin/pytest -q tests/intergrations/test_run_events_postgres.py
```

发布：

1. 重新构建包含 `mentor` CLI 的镜像（`[project.scripts]`）。
2. 运行 `mentor config doctor` / `make dev-check`。
3. 灰度时先在 Nacos 发布 `staging` AgentSpec + Prompt，再切 `NACOS_LABEL=staging`。
4. 回滚优先改 Nacos label/版本；进程内可用 `AgentFactory.rollback(spec_hash)` 回到仍缓存的历史 hash。

## 9. 已知限制

- `MOCK_USER` 明文 token，仅适合本地；生产需 OIDC/JWT。
- MCP 为全局配置，Skill 固定 production，staging 只覆盖 AgentSpec/Prompt。
- Agent 编译缓存与沙箱缓存都是进程内缓存，多 worker 不共享。
- 目录部署是「先清空、再上传」，不是原子替换。
- OpenSandbox 依赖宿主 Docker socket，多租户生产应换成独立沙箱集群/microVM。
- `SandboxBackend` 同步接口仍用 `asyncio.run`；事件循环内请使用异步方法。
- `TOOL_ARTIFACT_DIR` 是进程本地目录，多 worker 不共享；生产需挂持久卷/共享存储，并限制目录权限。
- MCP 默认 `deferred`，模型需要先调用 `tool_search` 再 `call_tool`；如果 MCP 工具必须直接暴露，在 server 配置里设 `exposure: direct`。
- `ToolMessage.artifact` 保存结构化信封，`content` 是预览；依赖原始 `content` 的旧工具/前端需要适配。
- MCP `auth` 会进入 Nacos 配置，属于敏感信息；生产应从密钥管理注入，不要写进 Git。
- `run_events` 使用 Postgres；多 worker 共享同一 `RUN_EVENTS_DSN`，事件表要按保留策略清理。
- `GET /runs/{id}/summary` 的 run_id 优先取 LangGraph config；缺失时回退 thread_id，多个 run 共用 thread 时会合并事件。
- 旧前端若读取 state 的 `turn_summary`，需要迁移到 `/runs/{id}/summary` 的 `RunSummary`。
- 策略 `pattern` 匹配的是参数 JSON 文本，不是语义解析；复杂命令需配合更精确的规则。
- LiteLLM 的 fallback/circuit 需要显式配置；当前 `litellm-config.yaml` 只有 session_affinity。
- 单 run 成本账单级控制仍应使用 LiteLLM key/team budget；Agent 只做 token/deadline 防失控。
- TokenMeter 用 usage_metadata 回填成本，不实现自定义 tokenizer；摘要模型调用本身暂未计入 usage。
- 预压缩摘要写入 Postgres 后，下一轮会作为 `lc_source=summarization` 的 HumanMessage 注入；注入后仍由 checkpoint 修剪保留最近 10 轮。
- pending 使用原子 claim：`try_claim` 只允许一个 run 启动摘要，`claim_ready` 只允许一个 run 注入；stale generating 默认 10 分钟后可回收。
- 预压缩只在主 Agent 挂载；子 Agent 共享 thread 时不重复覆盖。
- `trace_wrapper` 对同一 thread 加进程内 asyncio 锁；多 worker 部署仍需 Redis/Postgres 分布式锁。
- 后台摘要失败/进程退出时没有 pending，自动 0.8 压缩仍是兜底。
- `/compact` 需要前端把命令映射到 `compact_conversation`；命令入口未在本仓库实现。
- 审批中断依赖 LangGraph checkpointer/resume；无 checkpointer 的本地调用不会产生可恢复审批。

## 10. 上线前：多账号 / 多租户

当前**不能直接上线多账号**：登录仍是 `MOCK_USER`，Assistants 缺 owner 过滤，MinIO 把桶级凭据放进沙箱，LiteLLM 没有账号级配额，且没有 tenant 模型。

完整 P0/P1/P2 改造清单、风险矩阵、双用户验收用例与 Go/No-Go 条件见：

- [多账号 / 多租户上线改造清单](multi-account-hardening.md)
- [上线前待改动项 Backlog](prelaunch-backlog.md)（4.2.5 鉴权/多租户、4.2.7 观测/评测）
- [章节 4 优化项复核（2026-10-22）](section4-audit-2026-10-22.md)

上线前至少完成 P0-1 ~ P0-6，并通过双用户越权 E2E；4.2.7 指标与 golden 回归通过。
