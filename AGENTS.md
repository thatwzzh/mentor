# Mentor 开发指令

## 适用范围与阅读顺序

- 本文件适用于整个项目。改源码前沿目标路径逐级阅读 `src/AGENTS.md`、`src/mentor/AGENTS.md` 与模块内的 `AGENTS.md`；子目录只补充局部规则。
- 文件名统一用大写 `AGENTS.md`，不要同时创建 `Agents.md` 等大小写变体。
- `docs/` 与指令文件是两套东西：`docs/status/` 是历史快照，当前行为以代码和各层 `AGENTS.md` 为准。

## 沟通与操作边界

- 用户提问、讨论或征求意见时，不改动工程的任何东西；可以按需做无副作用的只读查看，不把讨论自动视为实施授权。
- 只有明确要求执行时，才在授权范围内改动；不因「发现问题」或「认为某项修改有益」自行修改代码、配置、依赖或指令文件。
- 保留已有修改，不顺手重构、提交或变更无关文件。只读讨论中发现指令或文档过时，应告知用户并建议更新，不自动落盘。

## 项目定位与当前边界

- 基于 LangGraph / DeepAgents 的 Agent 服务：Nacos 管运行时配置，LiteLLM 统一模型网关，OpenSandbox 提供隔离执行环境，MinIO 保存产物，Langfuse 负责追踪与评估。
- 鉴权仍使用 `utils/mock_user.py` 的四个模拟账号，生产用户中心尚未接入。
- 告警 Webhook 只验签并接收事件，短信、邮件与 IM 通知尚未实现。
- 长记忆 / RAG 模块已移除，`docs/` 中相关章节标注「已移除，待替换」；不要凭文档或目录名推断该能力存在。
- 没有独立的 LangGraph 普通图目录：`utils/cleanup_expired_traces.py` 只有函数，没有图与调度入口，全仓无调用方。
- 回归分三层：单元测试（无外部依赖）、集成测试（真实 MinIO / OpenSandbox / Postgres）、Benchmark（真实 Agent + Langfuse）。三层不能互相替代。

## 架构与目录导航

| 路径 | 职责 |
| --- | --- |
| `src/mentor/agents` | Agent 装配、编译缓存、子 Agent 与异步子代理 |
| `src/mentor/backends` | DeepAgent 后端到 OpenSandbox 的适配 |
| `src/mentor/budget` | 单 run token 与时间预算 |
| `src/mentor/config` | 环境配置与四层合并；`nacos` 子目录读远程资源 |
| `src/mentor/events` | Run 事件、投影、审批与预压缩摘要存储 |
| `src/mentor/golden` | 脚本化 golden session 的录制与回放 |
| `src/mentor/middlewares` | Agent 执行阶段的状态处理与护栏 |
| `src/mentor/policy` | 工具策略规则判定 |
| `src/mentor/security` | LangGraph 身份认证与资源授权 |
| `src/mentor/tools` | 模型可调用工具、注册表与统一执行器 |
| `src/mentor/utils` | 模型、追踪、缓存、沙箱、MinIO 等公共能力 |
| `src/mentor/app` | FastAPI 自定义路由与 Webhook |
| `src/mentor/benchmarks` | Langfuse 数据集、实验与评分 |
| `config/tool_policy.yaml` | 工具策略规则文件，`TOOL_POLICY_FILE` 的默认值 |
| `docker/sandbox/Dockerfile` | 沙箱镜像，包含部署上传所需依赖 |
| `tests/units` | 无外部依赖的单元测试 |
| `tests/intergrations` | MinIO / Deploy / Postgres 集成测试，保留现有目录拼写 |
| `tests/benchmarks` | 依赖真实 Agent 与 Langfuse 的质量回归 |
| `tests/golden/sessions` | golden 用例数据 |

运行入口由 `langgraph.json` 定义：图 `main_agent` 指向 `mentor.agents.main:factory_with_trace`，鉴权入口 `mentor.security.auth:auth`，自定义 HTTP 应用 `mentor.app.main:app`。改导出名或模块路径时同步检查该文件。

## 环境与常用命令

- 从项目根目录执行命令。Python 版本按 `pyproject.toml` 固定为 `3.13.11`，依赖用 uv 管理，安装用 `uv sync`。
- 配置项见 `.env.example` 与各 Settings 类；不要将 `.env` 内容、访问密钥或令牌写入文档、日志和测试输出。

| 命令 | 用途与前提 |
| --- | --- |
| `make docker-recreate` | 强制重建并启动全部基础设施容器 |
| `make dev` | 启动 LangGraph 开发服务，端口 80；依赖 Nacos、LiteLLM、OpenSandbox 等 |
| `make dev-check` | 先跑 `config doctor`，任一项失败则不启动 |
| `make debug` | 开发服务另开 5678 调试端口 |
| `make build TAG=<镜像标签>` | 构建 Agent 服务镜像 |
| `make config-schema` / `make config-doctor` | 输出 AgentSpec Schema / 启动前配置体检 |
| `make test-units` | 本地单元测试，无需外部服务 |
| `make test-integrations` | 真实 MinIO 与 OpenSandbox 部署验证，固定镜像 `opensandbox-python-node:1.0` |
| `make test-run-events` | 真实 Postgres 的事件表集成测试，读 `RUN_EVENTS_DSN` |
| `make test-benchmark DS=<数据集名称>` | 单数据集质量回归，会调用真实 Agent 与模型 |
| `make test-benchmark DS=all` | 全量评测，注意调用量与费用 |
| `uv run mentor golden replay --file <json>` | 回放 golden 用例，不调真实模型与网络 |
| `uv run pyright src/mentor` | 类型检查；直接调 `.venv/bin/pyright` 不认虚拟环境 |

- 沙箱镜像构建：`docker build -t opensandbox-python-node:1.0 -f docker/sandbox/Dockerfile .`，构建上下文必须是项目根目录，因为 Dockerfile 会复制 `src/mentor/utils/minio.py`。
- `docker compose down -v` 会连同数据卷删除，Nacos / Langfuse / MinIO 的数据都会丢失。
- 项目没有 formatter 配置，也没有 `pyrightconfig.json`，不要虚构 lint 或 format 命令。

## 全局修改与验证要求

- 遵循局部现有风格，新接口补准确的类型标注；不要为了统一排版扩大改动范围。
- 异步路径使用异步客户端；同步 SDK 的阻塞调用要隔离（如 `asyncio.to_thread`）；不要在已有事件循环内调用 `asyncio.run`。
- 用户身份只从认证上下文取，不能通过模型可填写的参数选择他人的沙箱或对象前缀。
- 模型可修复的参数错误与认证、配置、服务错误必须区分：前者返回工具错误让模型改，后者上抛终止 run；不捕获所有异常后伪装成功。
- `GraphInterrupt`（审批、提问）必须原样抛出，不能包成工具错误。
- 按改动选择验证：文档改动核对路径与命令；缓存、策略、投影跑对应单测；存储与部署跑集成测试；Agent 行为改动按场景评测。不要默认执行整个测试目录。
- 缺少外部服务时报告未验证项，区分源码检查、本地单测、Mock 与真实服务验证；测试跳过不等于验证通过。
- 模块接口、入口、环境变量、资源路径或行为契约变化时，同步检查调用方、测试与相关 `AGENTS.md`。

## 保持的设计约束

- 配置分工：LiteLLM 负责重试、fallback 与熔断，Agent 侧 `max_retries=0` 写死，只保留客户端超时；不要在代码里再实现一层重试。
- 配置四层：Pydantic 默认值 < `.env` < Nacos < 单次 run 的 `agent_spec_override`；override 不写回 Nacos，也不影响其他 run。
- 工具策略在 `ToolExecutor` 外层：deny 与 prompt 都不能先产生副作用；审批记录写 `approval_events`。
- 普通用户沙箱 `/root` 私有可写、`/shared` 只读；共享维护沙箱可写 `/shared`，不能把该权限扩展到用户沙箱。
- 事件表是 UI 与审计的唯一来源；checkpoint 只保留最近 N 轮、历史摘要与产物列表，不再复制全量消息。
- 压缩：回答结束后按 0.60 水位异步预生成摘要，0.80 自动压缩兜底，`/compact` 走 `compact_conversation` 工具。
- 工具输出超过阈值时落 artifact，模型通过 `read_artifact` 分段读回；不要把超长文本直接塞回上下文。

## AGENTS.md 的维护规则

- 维护范围包括根目录、`src/`、`src/mentor/` 及各子目录的 `AGENTS.md`。上层记录全局事实与共同规则，子目录记录本目录职责、实际行为与局部约束，避免重复，不把其他目录的实现写成本目录的能力。
- **何时新增**：新增源码目录或独立业务模块时同步新增；已有目录缺少指令且需要明确职责、关键契约或局部验证方式时补齐。不为 `__pycache__`、构建产物、虚拟环境等生成目录创建指令文件。
- **何时修改**：目录职责、调用流程、入口或导出、接口与数据结构、配置项与默认值、资源路径、权限边界、错误语义、依赖要求、测试命令或覆盖范围发生变化时，在同一轮改动中更新。
- **目录移动或删除**：同步调整适用范围、路径与上下层引用；删除模块时清理失效链接。
- **以当前事实为依据**：编写前核对本目录源码、配置、入口与测试；命令以 `Makefile` 和 `pyproject.toml` 为准，不凭目录名或旧文档推断能力已经实现。
- **区分事实与要求**：当前实现、开发约束、已确定但未实现的方案要分开写；代码未满足某项约束时记录差距，不把约束表述成已有保证。
- **同步上下层**：局部变化影响全局架构、导航或共同契约时同时更新上层；跨模块调用关系变化时检查并更新相应目录。
- **完成前核对**：路径存在、命令有效、状态准确、各层描述不矛盾。维护指令不能成为扩大业务修改范围的理由。
