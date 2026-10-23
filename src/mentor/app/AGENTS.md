# HTTP 路由与 Webhook

## 职责与入口

- `main.py:app` 是 `langgraph.json` 挂载的 FastAPI 应用，注册 5 个 router：实验 Webhook、告警 Webhook、审批查询、run 摘要、run 事件 SSE。
- `alert_webhook.py` 接 `/api/webhooks/langfuse/alerts`；`experiment_webhook.py` 接 `/api/webhooks/langfuse/experiments`。
- `approvals.py` 提供 `GET /runs/{run_id}/approvals`；`run_summary.py` 提供 `/summary`、`/trace`、`/usage`；`run_events.py` 提供 `/runs/{run_id}/events`。

## 认证与签名

- `langgraph.json` 设置 `enable_custom_route_auth=false`，自定义路由不受平台鉴权保护；`auth.py:require_user` 要求 `Authorization: Bearer ` 前缀并调用 `security.authenticate`，缺失或无效返回 401。
- 两个 Webhook 用独立密钥验签：实验用 `LANGFUSE_EXPERIMENT_WEBHOOK_SECRET`，告警用 `LANGFUSE_ALERT_WEBHOOK_SECRET`。
- 密钥留空时 `verify_signature` 直接返回 False，路由返回 401（与 `.env.example` 注释一致）。
- 验签只校验 HMAC，不校验时间戳新鲜度，也没有防重放。

## 数据与返回契约

- 审批、摘要、SSE 三个查询路由都按 `user_id` 过滤事件，读不到他人 run。
- `/approvals` 无记录时返回 404 而不是空列表；`limit` 范围 1–1000、默认 200，存储调用经 `asyncio.to_thread` 包装同步 psycopg。
- `/summary`、`/trace`、`/usage` 共用 `build_summary`，无事件时 404；`/trace` 返回状态、最后完成工具、待完成工具与事件，`/usage` 返回 token 汇总与压缩记录。
- SSE 用 `max(after_seq, Last-Event-ID)` 作为游标，每 0.5 秒轮询事件表，空闲约 15 秒发一次 `: ping`，见到 `run_finished` 立即结束；轮询间隔写在生成器里，改推送方式不要只改响应头。
- 实验 Webhook 读取 `datasetName`，缺失会抛 `KeyError` 返回 500；`BackgroundTasks` 是进程内任务，重启丢失，返回 202 只表示已提交。

## 修改与验证

- 新路由在 `main.py` 显式 `include_router`，并自己加认证，不能假设平台会兜底。
- 告警路由当前只验签并解析 JSON，通知渠道未实现，不能声称已接通邮件、短信或 IM。
- 单测覆盖：`tests/units/test_run_projection.py` 验证 Bearer 校验、用户过滤、SSE 到 `run_finished` 结束、outbox 重放；`test_tool_policy.py` 验证审批查询的用户过滤。真实 Webhook 需要 Langfuse 与有效密钥。
