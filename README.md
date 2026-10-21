# Mentor

基于 LangGraph / DeepAgents 的 Agent 服务：Nacos 管理运行时配置，OpenSandbox 提供隔离执行环境，LiteLLM 统一模型网关，MinIO 保存运行时文件与产物，Langfuse 负责观测与评估。

完整架构说明见 [docs/architecture.md](docs/architecture.md)。

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
