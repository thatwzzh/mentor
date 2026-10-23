# 公共运行能力

## 职责与入口

| 文件 | 职责 |
| --- | --- |
| `shared.py` | 从 LangGraph config 读 `thread_id` 与认证用户 ID |
| `session_context.py` | `litellm_session_id` 上下文，供模型请求注入会话头 |
| `subagent_context.py` | 子代理深度上下文 |
| `create_model.py` | 按 `AgentModelConfig` 创建 LiteLLM / OpenAI 兼容模型 |
| `reasoning_chat_model.py` | 补全 `reasoning_content` 的 `ChatOpenAI` 适配层 |
| `trace_wrapper.py` | 挂 Langfuse CallbackHandler、合并 trace metadata、设置预算与会话上下文 |
| `func_tools.py` | 函数级进程内缓存 `func_cache` |
| `sandbox.py` | 沙箱连接、创建、复用与共享维护沙箱 |
| `minio.py` | 独立的异步 MinIO 客户端，会被复制进沙箱镜像 |
| `langfuse_signature.py` | Webhook HMAC 验签 |
| `cleanup_expired_traces.py` | 按保留期提交 trace 删除请求 |
| `mock_user.py` | 本地模拟身份数据，不能当作生产用户中心 |

## 身份、模型与追踪

- `get_user_id` 读 `configurable.langgraph_auth_user_id`，缺失抛 `RuntimeError`；不要从模型输入或任意 metadata 推断身份。`get_thread_id` 缺失时返回 `None`，两者语义不同。
- `create_model` 固定 `max_retries=0`、`request_timeout` 取 `MODEL_REQUEST_TIMEOUT_SECONDS`，`extra_body.enable_thinking` 来自 AgentSpec；重试、fallback 与熔断交给 LiteLLM，不在 Agent 侧重复实现。
- `reasoning_chat_model` 把 provider 注册进 LangChain 的 `_BUILTIN_PROVIDERS`（私有表），`utils/__init__.py` 的导入就是注册动作，升级 `langchain` 时必须检查兼容性，不能顺手删除。
- 会话头不能写死在模型构造参数里：图被跨请求缓存，`x-litellm-session-id` 在 `_get_request_payload` 里每次从 `session_context` 读取。
- `trace_wrapper` 从 metadata 取 `trace_id` / `parent_span_id`，两者同时存在才建立父子关联；`_merge_trace_metadata` 先取图原有 metadata 再合并，否则会覆盖 `agent_spec_hash`。
- 同 thread 的串行锁是进程内实现，多 worker 部署仍需 Redis/Postgres 分布式锁，不能声称全局互斥。

## 缓存契约

- `func_cache` 支持同步与异步，按函数参数归一化缓存键（保留容器类型），使用单调时间，`ttl` 单位为秒且不小于零。
- 同一 key 的首次计算或刷新只执行一次，其余调用共享同一结果；等待者取消不会取消共享计算（`asyncio.shield`）。
- `lazy=True` 且有过期旧值时立即返回旧值并后台刷新，刷新失败保留旧值；首次失败向等待者传播。
- 仅进程内，没有容量淘汰与跨进程一致性；结果按引用复用，调用方不应直接修改。

## 沙箱与存储

- 沙箱 id 存 LangGraph Store 的 `("__sandbox__",)` 命名空间，以逻辑 key 记录 `sandbox_id`；拿不到持久 Store 时默认报错，只有显式 `OPEN_SANDBOX_ALLOW_MEMORY_STORE=true` 才回落 `InMemoryStore`。
- 同 key 的并发连接用弱引用锁表；`connect` 遇 404 才重建，其他服务错误照常抛。
- PVC 用 `createIfNotExists=True` 且 `deleteOnSandboxTermination=False`；共享沙箱固定 key `sandbox-shared`，只挂可写 `/shared`。
- `enter_sandbox` 在进出各续租一次并在 finally 关闭连接；关闭的是连接，不是终止沙箱或删除卷。
- `minio.py` 只依赖 aiobotocore 与标准库，`docker/sandbox/Dockerfile` 会把它复制成独立模块，不要加入 `mentor` 包内依赖。
- MinIO 每次操作创建并关闭独立客户端；除 `create_bucket` 外不隐式建桶或改权限。缓存与 Content-Type 策略只影响新上传，永久 URL 不代表私有对象可匿名读，预签名 URL 不检查对象是否存在。

## 验签、清理与验证

- Webhook 签名对「时间戳 + `.` + 原始 body」做 HMAC-SHA256 并常量时间比较；缺密钥或格式错误直接拒绝，当前没有时间戳新鲜度与防重放检查。
- `cleanup_expired_traces` 保留天数缺失或不大于零时回落到 30 天（`.env.example` 已按此说明），最多读 10 页、每页 50 条，按每批 50 个提交删除，返回已提交数量而非已删除数量。
- 该函数当前在仓库内没有调用方，需要外部调度；不要写成已自动清理。
- 缓存改动运行 `make test-units`；MinIO 与沙箱改动运行对应集成测试（`MINIO_INTEGRATION=1`，Deploy 还需 `DEPLOY_INTEGRATION=1` 与已构建镜像）。其余 MinIO、追踪与沙箱行为没有自动化覆盖。
