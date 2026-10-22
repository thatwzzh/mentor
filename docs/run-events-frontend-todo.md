# 前端 Run Events 待办（5.2 前端部分）

> 状态：**后端已就绪，前端未实现/未验证。**
> 后端契约见 `RunEvent` / `RunSummary`；本次不包含前端代码。

## 1. 后端接口

| 接口 | 用途 |
|---|---|
| `GET /runs/{run_id}/summary?after_seq=0` | session_summary：状态、卡片、产物、token |
| `GET /runs/{run_id}/trace?after_seq=0` | 详情：status、last_completed_tool、pending_tools、events |
| `GET /runs/{run_id}/usage?after_seq=0` | token_usage、compression |
| `GET /runs/{run_id}/events` | SSE 实时事件；支持 `Last-Event-ID` 重连 |
| `GET /runs/{run_id}/approvals` | 审批审计（approval_events） |

所有接口要求 `Authorization: Bearer <token>`，并按 user 过滤。

## 2. 前端待办

- [ ] SSE 客户端：浏览器 `EventSource` 不能自定义 Authorization header；用 `fetch` + `ReadableStream` 或 BFF 代理。
- [ ] 断线重连：保存最后 `id`，重连时带 `Last-Event-ID`，按 seq 去重。
- [ ] 卡片渲染：
  - `run_started` / `run_finished`：run 状态条
  - `turn_started` / `message`：用户/助手消息
  - `model_request` / `model_response`：模型调用耗时/token/tool_calls
  - `tool_call`：工具开始（名称、args、pending）
  - `tool_result`：工具结果/错误分类/耗时
  - `approval`：审批卡（用户选择）
  - `artifact`：产物卡（`artifact://` / 预签名 URL）
  - `compression`：上下文压缩提示
- [ ] `RunSummary.status`：`running / finished / incomplete / unknown`
- [ ] `pending_tools`：展示未完成工具；崩溃/断线后提示“最后完成工具”
- [ ] 审批交互：`tool_approval` interrupt 的 resume；审批卡展示 tool/args/reason，调用 `/approvals` 对账。
- [ ] 策略 deny/allow 的错误卡：区分 `error_kind=policy` 与工具失败。
- [ ] 失败/重连测试：kill 进程、断网、重复 seq、空 run、404。
- [ ] 前端 E2E：工具卡/审批卡/产物卡与 `run_events` 同源，不另建一套消息口径。

## 3. 验收

- 任一 run 可按 seq 重放；
- 崩溃后能看到 `status=incomplete` 和 `pending_tools`；
- UI 工具卡、审批卡、产物卡均来自同一 `RunEvent` 流；
- 断线重连不重复、不丢卡。

## 4. 备注

- 后端 SSE 在收到 `run_finished` 后主动结束；前端应正常关闭连接。
- SSE 心跳为注释行 `: ping`，前端忽略即可。
- 事件表写入失败会落 outbox；前端可能看到事件延迟，需容忍。
