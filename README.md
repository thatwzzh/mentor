# Mentor

基于 LangGraph / DeepAgents 的 Agent 服务：Nacos 管理运行时配置，OpenSandbox 提供隔离执行环境，LiteLLM 统一模型网关，MinIO 保存运行时文件与产物，Langfuse 负责观测与评估。

文档索引见 [docs/README.md](docs/README.md)。完整架构说明见 [docs/architecture.md](docs/architecture.md)；开发、配置、沙箱与发布 runbook 见 [docs/project-guide.md](docs/project-guide.md)；多账号/多租户上线改造清单见 [docs/launch/multi-account-hardening.md](docs/launch/multi-account-hardening.md)；4.2.5/4.2.7 上线前待办见 [docs/launch/prelaunch-backlog.md](docs/launch/prelaunch-backlog.md)。

## 启动服务

**启动基础设施环境**

```shell
make docker-recreate
```

**启动 Agent 服务**

```shell
# 开发模式
make dev
# debug 模式
make debug
```

## 配置校验与体检

AgentSpec 支持四层配置：Pydantic defaults < `.env` < Nacos < 单次 run 的 request override。
`NACOS_LABEL` 可设为 `production` 或 `staging`，用于 Nacos 侧灰度发布。Prompt 跟随 label；
MCP 是管理端全局配置、Skill 下载仍固定 `production`，切换前请确认对应资源已发布。

```bash
# 输出 AgentSpec JSON Schema，可交给 CI/编辑器做补全与校验
uv run mentor config schema

# 校验本地 JSON/YAML，或直接校验 Nacos 中的 Agent
uv run mentor config validate --file path/to/agent_spec.json
uv run mentor config validate --agent main_agent

# 启动前体检：Nacos、必填字段、模型网关、MinIO 桶、sandbox_config
uv run mentor config doctor

# 先体检再启动（任一项失败会阻止启动）
make dev-check
```

工具层：所有工具经过统一 `ToolExecutor`；超过 8KB 的输出落到 `TOOL_ARTIFACT_DIR` 并返回
`artifact://` 引用。MCP 工具默认 `deferred`，模型通过 `tool_search` / `call_tool` 使用；
需要旧行为时在 MCP server 配置里设 `exposure: direct`。

会话层：`turn_summary` 已改为 `run_events` 投影；`/runs/{run_id}/summary`、`/trace`、`/usage`
提供投影，`/runs/{run_id}/events` 提供 SSE。checkpoint 只保留最近 10 轮 + 历史摘要 + 产物列表，
事件表使用 Postgres（compose 的 `mentor-postgres`，默认 `RUN_EVENTS_DSN=postgresql://mentor:mentor@127.0.0.1:5433/mentor`）。
Postgres 写失败会落 `RUN_EVENTS_OUTBOX_DIR`，可用 `mentor events replay` 重放。

工具策略：`config/tool_policy.yaml` 按工具名/参数正则判定 `allow/prompt/deny`；prompt 通过
LangGraph interrupt 暂停，审批写 `approval_events`，`GET /runs/{run_id}/approvals` 可查。
`TOOL_POLICY_ENABLED=false` 可整体回退。

上下文：`TokenMeterMiddleware` 写 usage 事件；超长工具输出保留头 4KB + 尾 1KB，全文本地
artifact + MinIO 镜像；压缩使用显式 `CONTEXT_*` trigger/keep，`compact_conversation`
工具供 `/compact` 调用。回答结束后 token 达到 `CONTEXT_PRECOMPACT_RATIO=0.60` 时后台预生成摘要，
下一轮只注入摘要；0.8 自动压缩仍作兜底。

韧性：LiteLLM 负责 provider 重试/fallback/熔断；Agent 侧显式设置 `MODEL_REQUEST_TIMEOUT_SECONDS`，
Agent 侧 `max_retries` 固定为 0，避免与 LiteLLM 重复重试。
配置分工见 [docs/config-split.md](docs/config-split.md)：LiteLLM 管超时/重试/fallback/熔断，Agent 管客户端超时、上下文压缩和单 run 预算。`RUN_BUDGET_MAX_TOKENS/MAX_SECONDS` 提供单 run 预算，
超限抛 `BudgetExceeded` 终止；子 Agent 共享父 run 预算。

异步子代理：有 `subagents` 的 Agent 自动获得 `spawn_subagent` / `check_subagent` /
`cancel_subagent`；后台执行、默认深度 1、默认只读、写 `subagent_*` 事件，父级只拿
job_id 和摘要。前端接入见 [docs/frontend/async-subagent-frontend.md](docs/frontend/async-subagent-frontend.md)。

## 服务说明

> 以下账号密码均为本地开发默认值，请勿用于生产环境。

### 常用服务

- **Agent 服务**

  API 访问地址：http://localhost

  LangSmith 调试地址：https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:80

- **Langfuse 管理后台**

  访问地址：http://localhost:3000

- **LiteLLM**

  管理后台：http://localhost:4000/ui/

  账号：admin

  密码：123456

  API 地址：http://localhost:4000/v1

- **Nacos 管理后台**

  访问地址：http://localhost:8080/

  账号：nacos

  密码：初始界面自行设定

  API 接口文档：https://nacos.io/swagger/client/

- **OpenSandbox Server**

  API 接口：http://127.0.0.1:8090/docs

- **Shared MinIO（共享对象存储）**

  管理后台：http://localhost:9003

  账号：root

  密码：12345678

  宿主机访问 API：http://localhost:9002

### 不常用服务

- Langfuse MinIO 管理后台

  访问地址：http://localhost:9001

  账号：root

  密码：12345678

## 常用 Docker Compose 命令

```shell
# 创建并在后台启动所有容器
docker compose up -d

# 查看容器状态
docker compose ps

# 持续查看所有容器的日志，按 Ctrl+C 退出
docker compose logs -f

# 停止容器，但保留容器
docker compose stop

# 启动已经停止的容器
docker compose start

# 重启所有容器
docker compose restart

# 停止并删除容器和网络，不会删除数据卷
docker compose down

# 重新创建并在后台启动所有容器
docker compose up -d --force-recreate
```

项目里 `make docker-recreate` 等价于 `docker compose up -d --force-recreate`。

> `docker compose down -v` 会连同数据卷一起删除，Nacos / Langfuse / MinIO 中的数据都会丢失，谨慎使用。

## 自定义沙箱镜像

镜像文件：`docker/sandbox/Dockerfile`

### 构建镜像

```shell
docker build -t opensandbox-python-node:1.0 -f docker/sandbox/Dockerfile .
```

构建上下文必须是项目根目录，因为 Dockerfile 会复制 `src/mentor/utils/minio.py` 到镜像内。

### 容器环境

| 组件 | 版本 |
|---|---|
| Node.js | 24.21.0 |
| pnpm | 12.6.0 |
| Python | 3.13.15 |
| uv | 0.12.20 |
| 工作目录 | `/root/workspace` |

版本以实际构建结果为准，可用下面的命令核对：

```shell
docker run --rm opensandbox-python-node:1.0 sh -c 'node -v; pnpm -v; python3 -V; uv --version'
```

## MinIO 专用凭据

生产或联调时建议不要再让应用直接使用 MinIO 的 root 账号，而是生成一个受策略限制的服务账号。

### 1. 安装 MinIO Client（mc）

`mc` 是 MinIO 官方命令行管理工具，独立于 Python 的 `minio` SDK。下载地址：https://min.io/download

### 2. 保存管理员连接

```bash
mc alias set shared http://localhost:9002 root 12345678
```

| 参数 | 含义 |
|---|---|
| `mc alias set` | 保存或更新本机连接配置 |
| `shared` | 自定义连接别名，后续命令使用它找到连接 |
| `http://localhost:9002` | Shared MinIO 的对象 API 地址 |
| `root` | 当前 Compose 中的管理员 Access Key |
| `12345678` | 当前 Compose 中的管理员 Secret Key |

### 3. 准备专用权限策略

策略文件位于 `resource/minio-policy.json`。

> 当前仓库中的策略是 allow-all 示例（`Action: "*"`、`Resource: "*"`），按它生成的服务账号等价于 MinIO 管理员。仅本地开发可以使用；生产环境请改成只允许 `agent-artifacts` 桶读写的最小权限策略。

### 4. 自动生成专用凭据

```bash
mc admin user svcacct add \
  --policy resource/minio-policy.json \
  shared root
```

命令会输出新服务账号的 `accessKey` 和 `secretKey`。

### 5. 写入环境变量

把生成结果写入 `.env`（该文件已被 `.gitignore` 忽略，不要提交凭据）：

```dotenv
MINIO_ACCESS_KEY=<accessKey>
MINIO_SECRET_KEY=<secretKey>
MINIO_ENDPOINT=localhost:9002
MINIO_ENDPOINT_IN_SANDBOX=host.docker.internal:9002
MINIO_PRESIGN_EXPIRE_SECONDS=3600
```

`MINIO_ENDPOINT` 供宿主机上的 Agent 使用，`MINIO_ENDPOINT_IN_SANDBOX` 供沙箱内进程使用。

### 6. 查看和撤销专用凭据

```bash
# 列出 root 下的服务账号
mc admin user svcacct ls shared root

# 删除指定服务账号，把占位值替换为实际 Access Key
mc admin user svcacct rm shared '<accessKey>'
```
