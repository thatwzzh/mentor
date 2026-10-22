# 章节 4 优化项复核（2026-10-22）

> 范围：§4.2.1、4.2.2、4.2.3、4.2.4、4.2.6；以及 §4.1.1 的 S1–S20。
> 不包含：4.2.5（鉴权/多租户）、4.2.7（可观测/评测），这两项已在 `prelaunch-backlog.md`。
> 核对口径：以当前 main 代码、pytest 和可复现命令为准；不以文档计划为准。

## 1. 总览

| 章节 | 主体状态 | 主要遗留 |
|---|---|---|
| 4.2.1 Agent 工厂 | 已完成 | 首次构建仍等待 skill 下载；缓存只有 per-agent 上限，无全局上限 |
| 4.2.2 配置 | 已完成 | 无 CI 门禁；doctor 只检查网关可达，不校验具体模型/镜像是否存在；staging 仅覆盖 AgentSpec/Prompt |
| 4.2.3 沙箱 | 主体完成 | S7 同步接口仍用 `asyncio.run`；空闲回收只在创建新沙箱时触发；无启动时 `runtime.store` 检查；网络/配额是可选配置，非默认 deny |
| 4.2.4 工具 | 已完成 | MCP 的 auth/timeout/allowed/exposure 依赖 Nacos 返回字段，未验证管理端真实透出；risk 只是元数据，策略层尚未消费 |
| 4.2.6 会话投影 | 主体完成 | TokenMeter 已由 5.3 补齐，仍无预算/成本阈值；事件类型已补齐；仓库无前端，UI 卡片只有后端契约 |
| §4.1.1 S1–S20 | 9 项完成、3 项部分、6 项未完成 | S7/S13/S17/S18/S19/S20 仍未完成；S12/S16/S15 部分 |

## 2. 4.2.1 Agent 工厂

**已完成**

- `(agent_name, spec_hash)` 编译缓存，per-agent 3 版本 LRU。
- spec_hash 覆盖主/子 Agent、Prompt、模型、MCP、工具、子 Agent、技能名。
- per-agent `asyncio.Lock` 单飞，20 并发只编译一次。
- 子 Agent 装饰器注册，移除 `_AGENT_FACTORIES`。
- trace metadata 合并保留 `agent_spec_hash`；LiteLLM session header 按请求注入。
- 测试：`test_agent_cache.py` 7 例。

**遗留/边界**

- 首次构建仍 `await ensure_skills`；技能列表首次出现时会同步下载，不是纯后台任务。后续过期刷新才是后台。
- `AgentCache` 只限制每个 Agent 3 个版本，没有全局条目上限；Agent 名称数量增加时缓存无总量上限。
- “Spec 变更 5 秒内生效”依赖 `NACOS_TTL`；当前 `NacosSettings.ttl` 默认仍为 0（见 S19），不是显式 5 秒策略。

## 3. 4.2.2 配置

**已完成**

- 四层合并：defaults < `.env` < Nacos < request override。
- `AgentSpecOverride` 白名单；嵌套 model 只覆盖显式字段。
- `AgentSpecNotFound`/`AgentSpecUnavailable`；5 秒超时；404 last-known-good；5xx/鉴权/JSON fail-fast。
- `mentor config schema/validate/doctor`；`NACOS_LABEL`；`spec_hash` 进入缓存与 trace；`rollback`。
- 测试：`test_config_layers.py` 8 例。

**遗留/边界**

- **没有 CI 门禁**：CLI 可校验，但仓库没有 `.github/workflows` 或等价 CI 配置；S13 仍未完成。
- `doctor` 只请求模型网关 `/models`，不验证 Nacos 中 `model.name` 是否真的存在；sandbox 只检查镜像字符串，不检查镜像是否已构建/可拉取。
- `NACOS_LABEL=staging` 只覆盖 AgentSpec 与 Prompt；MCP 全局、Skill 固定 production，已在文档标注。
- Nacos 宕机“有缓存继续服务”依赖 `func_cache(lazy=True)`；没有专门的“缓存命中/降级”测试用例，只有错误映射测试。

## 4. 4.2.3 沙箱

**已完成**

- `enter_sandbox` try/finally；配置读取失败也 close。
- run 级缓存 `(user_id, thread_id)`；per-key lock；after_agent 关闭；命令异常丢弃坏连接；deploy finally 关闭。
- 严格 Store：生产禁止 InMemoryStore 回落。
- PVC `tenant-sha256(user)[:12]`。
- 资源/网络策略字段；`cpu`、`memory`、`network_default_action`、`network_allow`。
- 测试：`test_sandbox_lifecycle.py` 6 例 + 部署集成 33 例。

**遗留/边界**

- **S7 未完成**：`execute/upload_files/download_files` 仍用 `asyncio.run`；在已有事件循环中调用会出问题。
- **空闲回收不是后台定时器**：`_close_idle()` 只在创建新沙箱时调用；没有新创建时，泄漏的连接不会按 30 分钟主动回收。`OPEN_SANDBOX_IDLE_TIMEOUT_SECONDS=0` 只是关闭该检查。
- **启动时检查 `runtime.store` 未实现**：现在是首次 `get_sandbox` 时严格失败，不是启动/健康检查时发现。
- 网络策略默认 `None`（不传 NetworkPolicy），不是默认 deny；资源配额默认 None。作为“可选能力”完成，作为“生产默认安全”未完成。

## 5. 4.2.4 工具

**已完成**

- `ToolSpec` / `ToolRegistry` / `@register_tool`；risk/exposure/timeout/idempotent/max_output_chars。
- `ToolExecutor` + `ToolExecutorMiddleware` 覆盖内置/业务/MCP。
- 错误分类 input/policy/timeout/system；input/policy/timeout 返回错误 ToolMessage，system 终止。
- 8KB 截断 + `artifact://` + `read_artifact`。
- MCP 默认 deferred，自动补 `tool_search`/`call_tool`；auth/timeout/allowed_tools/exposure/risk 字段。
- 测试：`test_tool_executor.py` 6 例。

**遗留/边界**

- MCP 的 `auth`/`timeoutSeconds`/`allowedTools`/`exposure` 从 Nacos MCP 响应顶层读取；当前 Nacos 管理端是否真实透出这些字段未验证。若 Nacos 不返回，只能走默认 deferred/30s/全量工具。
- `risk` 只是 ToolSpec 元数据，尚无策略层消费；4.2.5/§5.1 才实现 allow/prompt/deny。
- 结果信封放在 `ToolMessage.artifact`，`content` 仍是预览；依赖原始 content 的前端/评测器需要适配。
- `ToolExecutorMiddleware` 覆盖的是 Agent 工具调用；自定义 `ToolNode` 图（如集成测试）不会自动经过它。

## 6. 4.2.6 会话投影

**已完成**

- 移除 `turn_summary`；`run_projection` after_agent。
- `run_events` Postgres 表；`RunEvent/RunSummary` 契约；user 过滤；`after_seq` 增量重放。
- checkpoint 最近 10 轮 + history_summary + artifacts；工具输出压到 500；REMOVE_ALL_MESSAGES 替换。
- 压缩日志 before/after tokens、kept_messages、summary_hash；usage metadata 汇总。
- 测试：`test_run_projection.py` 5 例 + Postgres 集成 1 例。

**遗留/边界**

- **TokenMeter 已由 5.3 补齐**：仍没有预算阈值、超限拒绝或成本告警；属于 4.2.7 的指标基础。
- **事件类型已补齐**：run_started/turn_started/model_request/model_response/run_finished 已由 5.2 后端实现；仍缺独立 TokenMeter/预算。
- **UI 卡片只有后端契约**：仓库没有前端；SSE、工具卡/审批卡/产物卡验收见 `run-events-frontend-todo.md`。
- `run_id` 优先 config，缺失回退 thread_id；多个 run 共用 thread 时事件合并。
- 4.2.5 未完成前，摘要接口仍使用 MOCK_USER 身份。

## 7. §4.1.1 S1–S20 复核

| 项 | 状态 | 说明 |
|---|---|---|
| S1 删除路径 | 已完成 | files API + realpath |
| S2 Nacos 错误 | 已完成 | 404/5xx/鉴权/JSON 分类 |
| S3 真实身份 | 未完成（4.2.5） | 仍是 MOCK_USER |
| S4 MinIO 私有 | 已完成 | set_bucket_private + 预签名 |
| S5 Skill 版本化 | 已完成 | 内容哈希 + current |
| S6 enter_sandbox | 已完成 | try/finally |
| S7 同步接口 | 未完成 | 仍 asyncio.run |
| S8 Agent 缓存 | 已完成 | spec_hash 缓存 |
| S9 turn_summary | 已完成 | run_events 投影 |
| S10 Assistants owner | 未完成（4.2.5） | 无 owner filter |
| S11 严格 Store | 已完成 | 禁止 InMemory 回落 |
| S12 web 工具 | 部分 | 执行器有 timeout/截断；无重试 |
| S13 CI/ruff | 未完成 | 无 CI 配置 |
| S14 PVC | 已完成 | tenant+sha256 |
| S15 私有 API | 部分 | 不再用 `_BUILTIN_PROVIDERS/_call`；仍有 `_get_request_payload`/`_create_chat_result` 私有 hook |
| S16 ask_user_question | 部分 | 工具仍抛 ValueError；执行器会归类 input，不再终止 run |
| S17 MCP admin | 未完成 | 仍调 admin 接口 |
| S18 deploy 原子性 | 未完成 | 先清空后上传 |
| S19 NACOS_TTL | 未完成 | 默认 0 |
| S20 Compose profiles | 未完成 | 12 服务默认全启 |

## 8. 建议优先级

**P0（上线前/阻塞）**

1. S3/S10 随 4.2.5 完成；S17 管理端权限。
2. S13 CI 门禁（至少 pyright + 单元测试 + config validate）。
3. S19 `NACOS_TTL` 默认 5 与校验。

**P1（稳定性）**

4. S7 同步接口改为显式 async 或在线程池中执行。
5. 4.2.3 增加后台空闲回收 + 启动时 store 检查。
6. 4.2.4 验证 Nacos MCP 字段真实透出；risk 接入策略层。
7. S18 deploy 原子化（临时前缀 + 切换/回滚）。
8. S20 Compose profiles。

**P2（体验/治理）**

9. 4.2.6 成本预算/告警；TokenMeter 与 run/model 事件已由 5.2/5.3 补齐。
10. S12 web 重试与退避。
11. S16 ask_user_question 直接返回可恢复错误，不依赖执行器兜底。

> 4.2.5、4.2.7 已在 `prelaunch-backlog.md`，不在此重复。
