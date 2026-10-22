# Mentor 基础设施架构

> 本文根据「基础设施全貌」图整理，并结合 mentor 仓库中实际的 `docker-compose.yml`、`opensandbox_config.toml`、`langgraph.json` 与 `.env.example` 补充了端口、配置和调用关系。

![基础设施全貌](infrastructure-overview.jpg)

## 0. 最近演进（2026-10）

| 模块 | 当前实现 |
|---|---|
| Agent 工厂 | 按 `(agent_name, spec_hash)` 缓存编译图，每 Agent 保留 3 个版本；spec_hash 覆盖主/子 Agent 完整配置 |
| 配置 | 四层合并：Pydantic defaults < `.env` < Nacos < request override；`NACOS_LABEL` 支持 production/staging |
| 配置校验 | `mentor config schema` / `validate` / `doctor`；`make dev-check` 先体检再启动 |
| 沙箱 | 一次 run 内按 `(user, thread)` 复用连接；Agent 结束关闭；PVC 名 `tenant-sha256(user)[:12]`；支持资源/网络策略与空闲回收 |
| 工具 | `ToolSpec` + `ToolRegistry` + `ToolExecutor`；所有工具统一限时/截断/错误分类；MCP 默认 deferred |
| 会话 | `run_events` 事件表 + `run_projection`；checkpoint 只留最近 10 轮 + 历史摘要 + 产物列表；`GET /runs/{id}/summary` 投影 |
| 策略 | `ToolPolicy` + `ToolPolicyMiddleware`；`approval_events` 审计；prompt 中断、deny 不执行、单 run 上限 |
| 上下文 | `TokenMeterMiddleware` + 显式 `SummarizationMiddleware`；工具头 4KB/尾 1KB，全文 MinIO；`compact_conversation` 手动压缩 |

## 1. 一句话定位

Mentor 是一个以 **LangGraph / DeepAgents** 为运行时的 Agent 服务：

- **Nacos** 管运行时配置（Agent 规格、Prompt、MCP、Skill、沙箱参数）；
- **OpenSandbox** 提供隔离执行环境；
- **LiteLLM** 统一所有模型请求；
- **MinIO** 保存运行时文件与交付产物；
- **Langfuse** 负责观测、评测与告警；
- **开发者** 基于 Langfuse 的数据持续改进 Agent。

从分层视角看：

| 平面 | 组件 | 职责 |
|---|---|---|
| 控制面 | Nacos | 配置、Prompt、MCP、Skill、沙箱参数 |
| 执行面 | Agent 服务 + OpenSandbox | 任务编排、工具调用、隔离执行 |
| 模型面 | LiteLLM | 多模型路由、密钥收敛、会话亲和 |
| 数据面 | MinIO | 对象存储、产物交付 |
| 观测面 | Langfuse | Trace、Dataset、Experiment、Alert |

## 2. 全景图

```mermaid
flowchart LR
    Nacos["Nacos<br/>运行时配置资源"]
    Agent["Agent 服务<br/>编排与执行任务"]
    LiteLLM["LiteLLM<br/>统一模型网关"]
    Sandbox["OpenSandbox<br/>隔离执行环境"]
    MinIO["MinIO<br/>运行时文件资源"]
    Langfuse["Langfuse<br/>统一观测与评估平台"]
    Dev["开发者<br/>基于数据改进 Agent"]

    Nacos -->|运行时配置| Agent
    Nacos -->|沙箱配置| Sandbox
    Agent -->|所有模型请求| LiteLLM
    Agent <-->|隔离执行| Sandbox
    Sandbox -->|读写文件资源| MinIO
    LiteLLM -->|请求观测数据| Langfuse
    Langfuse -->|观测与评估数据| Dev
    Dev -->|持续改进| Agent
```

图中的箭头是逻辑关系，不全是直接网络连接。例如 Nacos 不会直接调用 OpenSandbox：Agent 读取 `sandbox_config` 后，把镜像、超时等参数传给 OpenSandbox Server，由后者创建沙箱容器。

## 3. 组件职责

| 组件 | 实际部署 | 在 mentor 中的职责 | 关键配置 |
|---|---|---|---|
| Nacos | `nacos/nacos-server:v3.2.4-slim`，控制台 `:8080`，HTTP `:8848`，gRPC `:9848` | 保存 `agentspecs`、Prompt、MCP、Skill 与 `sandbox_config`；Agent 装配时按 TTL 缓存读取 | `NACOS_SERVER_ADDRESS`、`NACOS_NAMESPACE_ID`、`NACOS_TTL` |
| Agent 服务 | `langgraph dev`，默认 `http://localhost` | 装配主 Agent / 子 Agent，执行模型调用与工具调用，暴露 LangGraph API 与自定义 Webhook | `langgraph.json`、`main_agent`、`auth` |
| LiteLLM | `litellm/litellm-database:v1.101.0`，`:4000` | OpenAI 兼容网关；统一模型密钥、路由、限额与会话亲和 | `litellm-config.yaml`、`LITELLM_MASTER_KEY` |
| OpenSandbox | `opensandbox/server:release-1.1.0`，`127.0.0.1:8090` | 通过宿主 Docker 创建沙箱容器，提供命令执行与文件读写 | `opensandbox_config.toml`、`OPEN_SANDBOX_API_KEY` |
| MinIO | `minio`，API `:9002`，控制台 `:9003` | 保存 Agent 产物与用户上传文件；部署工具写入 `agent-artifacts` 桶 | `MINIO_*`、`resource/minio-policy.json` |
| Langfuse | `langfuse/langfuse:4`，Web `:3000` | 收集 Agent / 模型 trace；管理 Dataset、Experiment、Alert | `LANGFUSE_*`、Webhook 签名密钥 |
| 开发者 | 浏览器 / Langfuse UI / Nacos 控制台 | 查看 trace 与评测结果，调整 Prompt、Agent 规格与策略 | 无需单独部署 |

### 3.1 内部依赖

- Langfuse 自身依赖 PostgreSQL、Redis、ClickHouse 与一个独立 MinIO；这些只在 Compose 网络内使用。
- LiteLLM 依赖 PostgreSQL（存模型/消费记录）与 Redis（缓存/限流）。
- OpenSandbox Server 挂载宿主 `/var/run/docker.sock`，动态创建的沙箱容器由宿主 Docker 管理，与 OpenSandbox Server 同级，不是 Agent 进程的子进程。

## 4. 四条主链路

### 4.1 配置链路：Nacos → Agent / Sandbox

```
Nacos agentspecs
  ├─ content：model / tools / mcps / subagents / skills
  ├─ resource：instruction_<agent>/AGENTS__MD  → system_prompt
  └─ link_prompts → Nacos Prompt 模板 → 追加到 system_prompt

Nacos Prompt / MCP / Skill
  ├─ Prompt：get_prompt 按 label 读取
  ├─ MCP：管理端全局配置，不随 label 切换
  └─ Skill：下载仍固定 production（当前边界）

Nacos sandbox_config（YAML，必须存在）
  └─ SandboxBackend._get_or_create_sandbox → OpenSandbox.create(image, timeout, resource, network_policy, volumes)
```

要点：

- Agent 规格和 Prompt 按 `NACOS_LABEL` 读取（默认 `production`；staging 需在 Nacos 发布对应标签）。
- MCP 仍是管理端全局配置，不随 label 切换；Skill 下载仍固定 `production`，staging 技能尚未支持。
- 配置按四层合并：Pydantic defaults < `.env` < Nacos < 单次 run 的 `agent_spec_override`；override 不写回 Nacos。
- `sandbox_config` 不再有“缺失即默认值”的兜底；缺失或非法会明确报错，`mentor config doctor` 会失败。
- Skill 包由共享沙箱下载并按内容哈希落到 `/shared/skills/<agent_name>/<version>/`，`current` 符号链接原子指向可用版本；用户沙箱以只读方式挂载 `/shared`。

### 4.2 执行链路：Agent → OpenSandbox → MinIO

```
用户请求
  → Agent（模型决定调用 execute / write_file / deploy 等工具）
    → SandboxBackend
      → _get_or_create_sandbox（按 user + thread 复用连接）
        → OpenSandbox Server
          → 沙箱容器
            ├─ 普通文件：/root/workspace（用户级 PVC，持久）
            └─ deploy：沙箱内脚本直传 MinIO
                → agent-artifacts/<user_id>/<target_dirname>/
                → 宿主生成预签名 URL（默认 1 小时有效）
      → after_agent / sandbox_lifecycle 关闭当前 run 的连接
```

要点：

- 一次 run 内，`SandboxBackend` 按 `(user_id, thread_id)` 缓存连接；同一 thread 的 10 个并发工具调用只创建/连接一个 sandbox。
- Agent 正常结束由 `sandbox_lifecycle` 中间件关闭连接；命令通道异常时 `aexecute` 会丢弃坏连接，避免被后续工具复用。
- `/root` 是用户持久工作区，PVC 名为 `"<tenant>-<sha256(user_id)[:12]>"`；沙箱销毁后 PVC 保留。
- `/shared` 是只读共享卷，存放 Skill 与跨用户复用的依赖。
- 部署工具在沙箱内执行上传，产物不经过 Agent 进程；`deploy` 自己创建 backend，用完在 `finally` 中关闭。
- 目录部署是「先清空目标目录、再递归上传」，不是原子操作；失败时可能留下半成品。
- `sandbox_config` 可配置 CPU/内存、出口网络策略和超时；未配置时使用 OpenSandbox 默认值。

### 4.2.1 工具执行链：Agent → ToolExecutor → 工具

```text
Agent 工具调用
  → ToolExecutorMiddleware（覆盖内置 / 业务 / MCP）
    → ToolSpec：risk / exposure / timeout_s / max_output_chars
      → ToolExecutor
        → 执行工具
          → content：可读预览
          → artifact：{ok, data, error_kind, truncated, artifacts, duration_ms}
```

要点：

- 业务工具用 `@register_tool(risk=..., timeout_s=...)` 登记；Nacos `tools` 白名单决定是否装配。
- 错误统一分类：`input` / `policy` / `timeout` / `system`；前三种回给模型，`system` 终止 run。
- 输出超过 `max_output_chars`（默认 8000）时落盘到 `TOOL_ARTIFACT_DIR`，返回 `artifact://`；`read_artifact` 自动暴露。
- MCP 默认 `deferred`：不直接进 prompt；模型先 `tool_search`，再 `call_tool`。需要旧行为时配置 `exposure: direct`。
- MCP server 可声明 `auth`、`timeout_seconds`、`allowed_tools`、`exposure`、`risk`；`auth` 属于敏感配置。
- 策略层位于工具执行器外层：YAML 规则按工具名/参数 JSON 正则判定 `allow/prompt/deny`；prompt 通过 `interrupt` 暂停，审批结果写 `approval_events`，单 run 有次数上限。

### 4.2.2 会话投影：checkpoint 最小化

```text
Agent 开始
  → RunEventsMiddleware：run_started / turn_started / model_request / model_response
工具调用
  → ToolExecutorMiddleware：tool_call（副作用前）+ tool_result/approval（副作用后）
Agent 结束
  → run_projection：message / artifact / compression + checkpoint 修剪
  → run_finished
GET /runs/{run_id}/summary?after_seq=0   # session_summary
GET /runs/{run_id}/trace                # status / last_completed_tool / pending
GET /runs/{run_id}/usage                # token / compression
GET /runs/{run_id}/events               # SSE（Last-Event-ID 重连）
```

要点：

- `turn_summary` 不再写 state；UI 摘要由接口从事件表计算。
- 工具输出全文留在 run_events；checkpoint 里只保留 500 字符预览，100 轮会话可控。
- 压缩事件记录 `before_tokens / after_tokens / kept_messages / summary_hash`。
- 工具事件在副作用前后各写一条；崩溃后 `trace.pending_tools` 可定位未完成工具。
- 事件表使用 Postgres（`mentor-postgres`），带 `schema_version`；接口按 Bearer 身份过滤，`after_seq` / `Last-Event-ID` 支持重放。
- Postgres 写失败时落 `RUN_EVENTS_OUTBOX_DIR`，用 `mentor events replay` 重放。
- 前端 SSE/卡片验证待前端阶段完成。

### 4.2.3 上下文与 Token 治理

```text
TokenMeterMiddleware
  → estimate_tokens(messages)（调用前）
  → AIMessage.usage_metadata（调用后）
  → usage 事件（estimated/input/output/total）
ToolExecutor 超长输出
  → 头 4KB + 省略标记 + 尾 1KB
  → artifact:// 本地引用 + minio_ref 全文镜像
SummarizationMiddleware（显式 trigger/keep）
  → compact_conversation 工具（/compact）
  → compression 事件
```

要点：

- trigger 按模型 profile 最大输入 token × `CONTEXT_TRIGGER_RATIO`；profile 缺失用 `CONTEXT_WINDOW_TOKENS`。
- `CONTEXT_AUTO_COMPACT=false` 时把自动 trigger 抬到窗口外，只保留手动 compact。
- `usage` 事件优先于 `model_response` 汇总 token；`/runs/{id}/usage` 可查。
- 工具全文本地 artifact 可读，MinIO 只做持久化镜像；`minio_ref` 供审计/恢复。
- 摘要模型调用本身暂未计入 usage；`/compact` 命令入口在前端。

### 4.3 模型链路：Agent → LiteLLM → 模型提供方

```
Agent
  → create_model（OpenAI 兼容配置，model_provider=openai_reasoning）
    → LiteLLM /v1
      → 模型提供方
```

要点：

- 所有模型请求都应经过 LiteLLM，Agent 不直接持有各厂商密钥。
- `x-litellm-session-id` 由 `ReasoningChatModel` 在每次请求构造 payload 时从 ContextVar 注入，供网关做会话亲和与消费归集；图本身跨请求缓存，不会固定首个 thread 的 header。
- `ReasoningChatModel` 负责把 `reasoning_content` 提取为 content block，便于 Studio / Langfuse 展示思维链。
- LiteLLM 的 `litellm-config.yaml` 当前只开启 `session_affinity` 预检查，模型路由可在 LiteLLM 管理后台配置。
- 现状：Agent 侧通过 Langfuse `CallbackHandler` 上报 trace；LiteLLM 侧若要单独上报，需要在网关配置 `LANGFUSE_*` 凭据，当前 Compose 未注入这些变量。

### 4.4 改进链路：Langfuse → 开发者 → Agent

```
Agent / LiteLLM
  → Langfuse Trace（模型调用、工具调用、耗时、token）
    → Dataset / Experiment
      → Langfuse Webhook
        → mentor /api/webhooks/langfuse/experiments
          → run_benchmark → LangGraph SDK → Agent
            → LLM Judge（内容相似度 + 情绪相似度）
              → 加权总分 → Langfuse

Langfuse Alert
  → mentor /api/webhooks/langfuse/alerts
    → 通知渠道（当前为占位实现）
```

要点：

- 评测任务通过 `metadata.trace_id` / `parent_span_id` 把 Agent 调用挂到同一条实验 trace 下。
- 评测使用 `thread_id=None` 的无状态运行，不污染真实用户会话。
- 开发者根据 trace 与评测结果调整 Nacos 中的 Prompt / AgentSpec，不需要重新发版。

## 5. 部署拓扑与启动顺序

### 5.1 拓扑

```
宿主机
├─ Agent 服务（langgraph dev，:80）
├─ Docker Compose 网络
│  ├─ Nacos（:8080 / :8848 / :9848）
│  ├─ LiteLLM（:4000） + PostgreSQL + Redis
│  ├─ Langfuse（:3000） + PostgreSQL + Redis + ClickHouse + MinIO
│  ├─ OpenSandbox Server（127.0.0.1:8090）
│  ├─ Mentor Postgres（127.0.0.1:5433）
│  └─ Shared MinIO（:9002 / :9003）
└─ OpenSandbox 动态创建的沙箱容器（bridge 网络）
```

两个地址差异需要注意：

- Agent 在宿主机运行，访问服务用 `localhost:<port>`。
- 沙箱内访问宿主机服务用 `host.docker.internal:<port>`，对应 `NACOS_SERVER_ADDRESS_IN_SANDBOX`、`MINIO_ENDPOINT_IN_SANDBOX`。

### 5.2 启动顺序

```bash
# 1. 准备 .env（已存在则跳过）
[ -f .env ] || cp .env.example .env

# 2. 启动基础设施（Compose 会读取 .env 中的 OPEN_SANDBOX_API_KEY 等变量）
make docker-recreate

# 3. 首次使用或修改 Dockerfile 后，构建沙箱镜像
docker build -t opensandbox-python-node:1.0 -f docker/sandbox/Dockerfile .

# 4. 启动前体检：Nacos、AgentSpec 必填项、模型网关、MinIO 桶、sandbox_config
uv run mentor config doctor
# 或：make dev-check（先 doctor，再启动）

# 5. 启动 Agent
make dev

# 6. 可选：跑一次评测
make test-benchmark DS=all
```

如果只想启动 MinIO：

```bash
docker compose up -d shared-minio
```

## 6. 端口与地址速查

| 服务 | 地址 | 用途 |
|---|---|---|
| Agent API | http://localhost | LangGraph API |
| LangSmith Studio | https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:80 | 调试 Agent |
| Nacos 控制台 | http://localhost:8080 | 配置 / Prompt / MCP / Skill |
| Nacos HTTP API | http://localhost:8848 | Agent 与沙箱读取配置 |
| LiteLLM UI | http://localhost:4000/ui/ | 模型与密钥管理 |
| LiteLLM API | http://localhost:4000/v1 | OpenAI 兼容接口 |
| OpenSandbox API | http://127.0.0.1:8090/docs | 沙箱管理 |
| Mentor Postgres | 127.0.0.1:5433 | run_events 事件表 |
| Shared MinIO API | http://localhost:9002 | 产物读写 |
| Shared MinIO 控制台 | http://localhost:9003 | 桶与对象管理 |
| Langfuse Web | http://localhost:3000 | Trace / Dataset / Experiment |
| Langfuse MinIO | http://localhost:9001 | Langfuse 内部对象存储 |

## 7. 关键环境变量

| 变量 | 作用 |
|---|---|
| `NACOS_SERVER_ADDRESS` | 宿主机访问 Nacos 的地址 |
| `NACOS_SERVER_ADDRESS_IN_SANDBOX` | 沙箱内访问 Nacos 的地址，通常是 `http://host.docker.internal:8848` |
| `NACOS_NAMESPACE_ID` | Nacos 命名空间 |
| `NACOS_TTL` | Agent 规格 / Prompt / MCP 的本地缓存秒数 |
| `NACOS_LABEL` | AgentSpec/Prompt 的发布标签：`production` / `staging` |
| `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY` | MinIO 凭据；建议使用服务账号，不要长期使用 root |
| `MINIO_ENDPOINT` | 宿主机访问 MinIO 的地址 |
| `MINIO_ENDPOINT_IN_SANDBOX` | 沙箱内访问 MinIO 的地址 |
| `MINIO_BUCKET` | 产物桶名，默认 `agent-artifacts` |
| `MINIO_PRESIGN_EXPIRE_SECONDS` | 产物预签名 URL 的有效期，默认 3600 秒 |
| `OPENAI_API_KEY` / `OPENAI_BASE_URL` / `OPENAI_DEFAULT_MODEL` | LiteLLM 上游模型配置 |
| `OPEN_SANDBOX_DOMAIN` / `OPEN_SANDBOX_API_KEY` | OpenSandbox Server 连接配置 |
| `OPEN_SANDBOX_TENANT` | PVC 名称前缀，默认 `default` |
| `OPEN_SANDBOX_ALLOW_MEMORY_STORE` | 仅本地脚本可设为 true；生产禁止 InMemoryStore 回落 |
| `OPEN_SANDBOX_IDLE_TIMEOUT_SECONDS` | 进程内空闲沙箱回收时间，默认 1800 秒；0 表示交给 OpenSandbox TTL |
| `TOOL_ARTIFACT_DIR` | 工具超长输出落盘目录；生产建议持久卷 + 权限限制 |
| `RUN_EVENTS_DSN` | Run 事件表 Postgres 连接串，默认 `postgresql://mentor:mentor@127.0.0.1:5433/mentor` |
| `MENTOR_POSTGRES_PASSWORD` | `mentor-postgres` 密码，默认 `mentor` |
| `RUN_EVENTS_KEEP_LAST_TURNS` | checkpoint 保留最近轮数，默认 10 |
| `RUN_EVENTS_MAX_TOOL_CHARS` | 保留轮次中的工具输出上限，默认 500 |
| `RUN_EVENTS_OUTBOX_DIR` | Postgres 写入失败时的 JSONL outbox，默认 `/tmp/mentor-run-events-outbox` |
| `TOOL_POLICY_FILE` | 工具策略 YAML，默认 `config/tool_policy.yaml` |
| `TOOL_MINIO_ARTIFACTS` | 超长工具全文镜像 MinIO，默认 true |
| `CONTEXT_WINDOW_TOKENS` / `CONTEXT_TRIGGER_RATIO` / `CONTEXT_KEEP_MESSAGES` | 压缩 trigger/keep |
| `CONTEXT_TRIM_TOKENS_TO_SUMMARIZE` / `CONTEXT_AUTO_COMPACT` | 摘要输入上限与自动压缩开关 |
| `TOOL_POLICY_ENABLED` | 策略总开关；false 回退 allow |
| `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_BASE_URL` | Langfuse 连接配置 |
| `LANGFUSE_TRACE_RETENTION_DAYS` | Trace 保留天数，供清理任务使用 |
| `BENCHMARK_AGENT_SERVER` / `BENCHMARK_USER_ID` / `BENCHMARK_GRAPH_ID` | 评测任务连接 Agent 的参数 |

## 8. 边界与安全

### 8.1 信任边界

- **控制面（Nacos）**：保存 Prompt 与 Agent 规格，属于受信配置；修改权限应只开放给开发者。
- **执行面（OpenSandbox）**：运行模型生成的代码，按不可信代码处理；沙箱容器移除高危 capability，开启 `no_new_privileges`，限制 PID。
- **数据面（MinIO）**：产物桶是私有桶，`deploy` 返回短期预签名 URL；不要把桶改成匿名公共读。
- **观测面（Langfuse）**：trace 可能包含完整 Prompt / 输出，需控制访问权限并按保留策略清理。

### 8.2 凭据

- 本地开发可以用 MinIO root 账号；联调/生产建议用 `mc admin user svcacct add` 生成专用服务账号。
- `resource/minio-policy.json` 当前是 allow-all 示例，生成的服务账号等价于管理员；生产应改为只允许 `agent-artifacts` 桶的最小权限策略。
- 所有密钥只写 `.env`（已被 `.gitignore` 忽略），不要提交到仓库。

最小权限策略示例（如果桶已预创建，可以去掉 `s3:CreateBucket`）：

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "s3:GetObject",
        "s3:PutObject",
        "s3:DeleteObject",
        "s3:ListBucket",
        "s3:CreateBucket",
        "s3:GetBucketPolicy",
        "s3:PutBucketPolicy",
        "s3:DeleteBucketPolicy"
      ],
      "Resource": [
        "arn:aws:s3:::agent-artifacts",
        "arn:aws:s3:::agent-artifacts/*"
      ]
    }
  ]
}
```

### 8.3 已知限制

- Agent 鉴权当前使用 `MOCK_USER` 明文 token，仅适合本地开发；生产需要接 OIDC/JWT。
- OpenSandbox 依赖宿主 Docker socket，适合单机/受控环境；多租户生产环境应换成独立沙箱集群或 microVM。
- Langfuse 自托管栈较重（ClickHouse + PostgreSQL + Redis + MinIO），本地开发可按需启动。
- `SandboxBackend` 的 run 级缓存是进程内缓存；多 worker 部署时每个进程各自缓存，不会跨进程共享连接。
- PVC 名从旧的 `user_id.replace("_", "-")` 改为 `tenant-sha256(user_id)[:12]`；升级后旧 PVC 不会自动挂载，迁移时需保留旧 PVC 或复制 `/root` 数据。
- `get_sandbox` 生产默认禁止 `InMemoryStore` 回落；本地脚本需要显式设置 `OPEN_SANDBOX_ALLOW_MEMORY_STORE=true`。
- MCP 是 Nacos 管理端全局配置，不随 `NACOS_LABEL` 切换；Skill 下载仍固定 `production`，staging 技能尚未支持。
- 目录部署仍是「先清空、再上传」，不是原子替换；上传失败可能留下半成品。
- 工具 artifact 目录是进程本地目录，多 worker 不共享；生产需持久卷并限制权限。
- MCP 默认 `deferred`，模型需要先 `tool_search` 再 `call_tool`；旧行为需显式 `exposure: direct`。
- MCP `auth` 会存入 Nacos，属于敏感信息；生产应从密钥管理注入。
- `run_events` 使用 Postgres；多 worker 共享同一 DSN，事件表需按保留策略清理。
- `run_id` 优先取 LangGraph config；缺失时回退 thread_id，多 run 共用 thread 时事件会合并。
- 旧前端读取 state `turn_summary` 的，需要迁移到 `RunSummary`。
- 策略正则匹配参数 JSON，不是命令语义；复杂命令需要更精确的规则。
- TokenMeter 用 usage_metadata 回填成本，不实现自定义 tokenizer；摘要调用未计量。
- 工具全文 MinIO 镜像是尽力而为，失败仍保留本地 artifact。
- `/compact` 需要前端映射到 compact_conversation。
- 审批中断依赖 checkpointer/resume；前端审批卡未验证。
- 多账号/多租户上线前必须完成真实鉴权、Assistants owner 过滤、MinIO 凭据隔离、LiteLLM 配额等改造；完整清单见 [multi-account-hardening.md](multi-account-hardening.md)；4.2.5/4.2.7 的上线前待办见 [prelaunch-backlog.md](prelaunch-backlog.md)。

## 9. 一个具体请求的完整路径

以「生成一个静态网页并部署」为例：

1. 用户在 Agent API 发起对话。
2. Agent 从 Nacos 读取 `main_agent` 的 Prompt、工具白名单与 Skill。
3. Agent 通过 LiteLLM 请求模型；模型决定调用 `execute` / `write_file` / `deploy`。
4. `SandboxBackend` 按 `(user_id, thread_id)` 复用沙箱连接（没有则创建），在 `/root/workspace` 中构建网页；run 结束由 `sandbox_lifecycle` 关闭连接。
5. `deploy` 在沙箱内把 `dist/` 上传到 MinIO 的 `agent-artifacts/<user>/<project>/`，宿主生成预签名 URL 并返回；deploy 自己的 backend 在 `finally` 中关闭。
6. 全过程的模型调用、工具调用与耗时写入 Langfuse trace。
7. 开发者在 Langfuse 查看 trace，必要时调整 Nacos 中的 Prompt 或 Agent 规格；下一轮请求自动生效。
