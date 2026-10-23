# 模型可调用工具

## 职责与注册

- 业务工具用 `@register_tool(risk=..., timeout_s=...)` 配合 `@tool` 登记，落到 `specs.py` 的 `_APP_SPECS`；`__init__.py` 导入具体模块以触发注册，新增模块要在这里补导入。
- `get_tool_bundle(names)` 按 Nacos 白名单选择工具，未知名称静默忽略；`read_artifact` 总会被追加，避免截断引用无法读回。
- 存在 deferred 工具时自动补 `tool_search` / `call_tool`；前者是 `read`，后者是 `external`，`call_tool` 只允许调用 deferred 工具。
- `ToolRegistry` 预置 DeepAgents 内置工具名（`ls`、`read_file`、`execute`、`task` 等）的默认契约，`tool=None` 但保留风险等级与超时，因此中间件能统一限时；同名业务工具可以覆盖这些默认值。
- 未登记工具按 `write` / 30 秒兜底，不视为无障碍。

## 执行契约

- `executor.py:ToolExecutor` 是所有工具的唯一入口：限时（`asyncio.wait_for`）→ 执行 → 截断与落盘 → 错误分类 → 结果信封。
- 错误分四类：`timeout`、`policy`、`input`（`ToolException` / `ValidationError` / `ValueError`）、`system`；`system` 直接上抛终止 run，其余转成 `status="error"` 的 ToolMessage 让模型改参数。
- `GraphInterrupt` 必须原样抛出，不能当成工具错误，否则审批与提问失效。
- 超过 `max_output_chars` 时写本地 artifact，预览保留头 4KB + 尾 1KB 并附 `artifact://` 引用；配了 MinIO 凭据还会镜像一份 `minio://` 引用。
- `read_artifact` 只接受服务端生成的 `artifact://<filename>`，拒绝含 `/`、`\`、`..` 的引用。
- `deploy` 是 destructive：在沙箱内直传 MinIO，上传前先把产物桶置为私有，返回短期预签名 URL；目录部署先删目标前缀再上传，非原子、失败不恢复旧文件；输出中的密钥统一打码。
- `ask_user_question` 用 `interrupt` 暂停，`timeout_s=None` 不受工具超时打断；选项 2–5 个、标题不重复、最多一个推荐项。
- `web_search` / `fetch_url` 把同步 Tavily SDK 放进 `asyncio.to_thread`；`fetch_url` 没有单独超时，靠 30 秒工具超时兜底。
- `get_current_time` 固定用 UTC+8（`BEIJING_TZ`，无夏令时），不依赖容器 `TZ` 或镜像里的 tzdata；改时间语义时同步工具描述与测试。

## 修改与验证

- 新工具同步注册、`ToolSpec` 元数据（risk / exposure / timeout）、Nacos 白名单与本文件；身份类信息从运行上下文读，不能做成模型可填参数。
- 改 `deploy` 语义同步 docstring、`DeployInput` schema 与 `tests/intergrations/test_deploy.py`（需真实 MinIO 与沙箱镜像）。
- 单测覆盖 `test_tool_executor.py`、`test_tool_policy.py`、`test_sandbox_profile.py`；模型是否选对工具不在测试范围内。
