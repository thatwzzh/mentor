# Mentor 基础设施架构

> 本文根据「基础设施全貌」图整理，并结合 mentor 仓库中实际的 `docker-compose.yml`、`opensandbox_config.toml`、`langgraph.json` 与 `.env.example` 补充了端口、配置和调用关系。

![基础设施全貌](infrastructure-overview.jpg)

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
  └─ AgentFactory._get_tools / _get_subagents / save_skills

Nacos sandbox_config（YAML）
  └─ SandboxBackend.aexecute → OpenSandbox.create(image, timeout, volumes)
```

要点：

- Agent 规格按 `label=production` 读取，`NACOS_TTL` 控制本地缓存时间。
- Skill 包由共享沙箱下载并按内容哈希落到 `/shared/skills/<agent_name>/<version>/`，`current` 符号链接原子指向可用版本；用户沙箱以只读方式挂载 `/shared`。
- `sandbox_config` 不直接发给 OpenSandbox；它是 Agent 创建沙箱时的参数来源。

### 4.2 执行链路：Agent → OpenSandbox → MinIO

```
用户请求
  → Agent（模型决定调用 execute / write_file / deploy 等工具）
    → SandboxBackend
      → enter_sandbox（按用户复用沙箱）
        → OpenSandbox Server
          → 沙箱容器
            ├─ 普通文件：/root/workspace（用户级 PVC，持久）
            └─ deploy：沙箱内脚本直传 MinIO
                → agent-artifacts/<user_id>/<target_dirname>/
                → 宿主生成预签名 URL（默认 1 小时有效）
```

要点：

- `/root` 是用户持久工作区，沙箱销毁后 PVC 保留。
- `/shared` 是只读共享卷，存放 Skill 与跨用户复用的依赖。
- 部署工具在沙箱内执行上传，产物不经过 Agent 进程，避免大文件搬运。
- 目录部署是「先清空目标目录、再递归上传」，不是原子操作；失败时可能留下半成品。

### 4.3 模型链路：Agent → LiteLLM → 模型提供方

```
Agent
  → create_model（OpenAI 兼容配置，model_provider=openai_reasoning）
    → LiteLLM /v1
      → 模型提供方
```

要点：

- 所有模型请求都应经过 LiteLLM，Agent 不直接持有各厂商密钥。
- Agent 会把 `thread_id` 写入 `x-litellm-session-id`，供网关做会话亲和与消费归集。
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

# 4. 启动 Agent
make dev

# 5. 可选：跑一次评测
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
| `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY` | MinIO 凭据；建议使用服务账号，不要长期使用 root |
| `MINIO_ENDPOINT` | 宿主机访问 MinIO 的地址 |
| `MINIO_ENDPOINT_IN_SANDBOX` | 沙箱内访问 MinIO 的地址 |
| `MINIO_PRESIGN_EXPIRE_SECONDS` | 产物预签名 URL 的有效期，默认 3600 秒 |
| `OPENAI_API_KEY` / `OPENAI_BASE_URL` / `OPENAI_DEFAULT_MODEL` | LiteLLM 上游模型配置 |
| `OPEN_SANDBOX_DOMAIN` / `OPEN_SANDBOX_API_KEY` | OpenSandbox Server 连接配置 |
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

## 9. 一个具体请求的完整路径

以「生成一个静态网页并部署」为例：

1. 用户在 Agent API 发起对话。
2. Agent 从 Nacos 读取 `main_agent` 的 Prompt、工具白名单与 Skill。
3. Agent 通过 LiteLLM 请求模型；模型决定调用 `execute` / `write_file` / `deploy`。
4. `SandboxBackend` 找到该用户的沙箱（没有则创建），在 `/root/workspace` 中构建网页。
5. `deploy` 在沙箱内把 `dist/` 上传到 MinIO 的 `agent-artifacts/<user>/<project>/`，宿主生成预签名 URL 并返回。
6. 全过程的模型调用、工具调用与耗时写入 Langfuse trace。
7. 开发者在 Langfuse 查看 trace，必要时调整 Nacos 中的 Prompt 或 Agent 规格；下一轮请求自动生效。
