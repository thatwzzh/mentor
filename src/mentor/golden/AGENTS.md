# 可重放 Golden Session

## 职责与入口

- `models.py` 定义 `GoldenCase`、`GoldenStep`、`GoldenTool`、`GoldenPolicyRule`、`GoldenExpected`。
- `runner.py` 提供 `record_case` / `replay_case` / `load_case` / `save_case`，并内建 `ScriptedChatModel` 与 mock 工具。
- 例会在 `tests/golden/sessions/`；CLI 为 `uv run mentor golden replay --file <路径>` 与 `golden record --file <路径> [--out <路径>]`。

## 执行契约

- 模型按 `steps` 顺序返回固定 `AIMessage`，脚本耗尽抛 `RuntimeError`，`bind_tools` 是空实现；只有 step 显式给了 token 数才带 `usage_metadata`。
- mock 工具用 `StructuredTool` 包一层协程，`delay_s` 用来制造超时；风险等级决定策略判定结果。
- 图使用 `InMemorySaver` 与固定 `thread_id=golden:<name>`、`configurable.langgraph_auth_user_id=golden-user`，不碰真实存储。
- 装配的中间件是 RunEvents、TokenMeter、Budget、ToolPolicy（注入 `InMemoryApprovalStore`）与 ToolExecutor；事件写内存列表，不进 Postgres。
- `record` 跑一遍并写入 `expected`，默认覆盖 `--file`；`replay` 逐项比较 `event_kinds`、`event_names` 与最终输出，任一不一致抛 `AssertionError`。
- 只比较稳定量：不比较时间戳、`seq`、token 数与耗时，避免无关抖动导致误报。

## 修改与验证

- 改中间件顺序或事件语义会让所有 golden 失效，需要重新 `record` 并在改动说明里解释预期变化，不能直接手改 `expected` 让测试变绿。
- 新增用例放 `tests/golden/sessions/`，`tests/units/test_golden.py` 会自动遍历回放，另有用例验证事件漂移能被检出。
- golden 不调真实模型与网络，因此不能证明真实模型质量；真实模型录制能力尚未实现，不要写成既有能力。
