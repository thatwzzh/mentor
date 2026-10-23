# Agent 中间件

## 职责与挂载顺序

| 中间件 | 作用 |
| --- | --- |
| `run_events.py` | `run_started` / `turn_started` / `model_request` / `model_response` / `compression` / `run_finished` |
| `run_projection.py` | 写消息投影事件并修剪 checkpoint |
| `budget.py` | 模型调用前查预算，调用后回填用量 |
| `token_meter.py` | 每次模型调用写 `usage` 事件 |
| `context.py` | 显式 trigger/keep 的压缩与 `compact_conversation` 工具 |
| `precompaction.py` | 回答结束后异步生成摘要，下一轮注入 |
| `sandbox_profile.py` | 步数、工具次数、并发与输出上限护栏 |
| `tool_policy.py` | allow / prompt / deny 与审批审计 |
| `tool_executor.py` | 所有工具调用统一走 `ToolExecutor` |
| `sandbox_lifecycle.py` | run 结束关闭沙箱连接 |

挂载顺序在 `agents/agent_factory.py:create_agent` 固定：子类中间件（`RunEvents`、`run_projection`）→ Budget → TokenMeter → Summarization → compact 工具 →（仅 main_agent）`PendingCompaction` + `PostTurnCompaction` → SandboxProfileGuard → ToolPolicy → ToolExecutor → sandbox_lifecycle。策略必须排在执行器外层，否则 deny 也可能先产生副作用。

## 关键契约

- 事件写入失败只告警、不阻断业务；`run_projection` 用 `REMOVE_ALL_MESSAGES` 替换消息，`messages` 是 DeltaChannel，直接返回列表会变成追加。
- `run_projection` 只在内容变化时返回对应键，`history_summary` 与 `artifacts` 比较后才写回，避免每轮都触发 checkpoint 更新。
- `token_meter` 优先用模型回填的 `usage_metadata`，缺失时用粗估兜底，并记录 `served_model` 以识别 fallback。
- `precompaction` 依赖 DeepAgents 的私有方法 `_count_tokens` 与 `_acreate_summary`，升级 `deepagents` 必须回归；预压缩失败只写日志，不影响已完成的回答。
- `PendingCompaction` 用 `additional_kwargs["lc_source"]=="summarization"` 判断是否已注入，重复领取不会叠加摘要。
- `sandbox_profile` 的计数器在 `abefore_agent` 重置；超步数抛错终止 run，超工具次数返回 `status="error"` 的 ToolMessage 让模型继续。
- `sandbox_lifecycle` 只关闭连接，不终止沙箱、不删除 PVC。

## 修改与验证

- 新中间件要明确触发阶段、读取字段与返回值，并在工厂注册；增加状态字段同步 `agents/main_agent_state.py`。
- 调整顺序会影响事件完整性与副作用边界，改完必须重跑 golden（`tests/units/test_golden.py`）。
- 单测覆盖 `test_run_projection.py`、`test_token_meter.py`、`test_precompaction.py`、`test_tool_policy.py`、`test_tool_executor.py`、`test_sandbox_profile.py`、`test_sandbox_lifecycle.py`。真实串行行为仍需一次完整 run 验证。
