# 异步子代理：前端接入说明

> 状态：**后端事件已就绪；前端状态查询、卡片、结果反显和“继续”入口未实现。**
> 后端实现见 `src/mentor/agents/async_subagent.py` / `src/mentor/tools/async_subagent.py`。

## 1. 事件契约

父 run 的 `run_events` 会出现：

| 事件 | 含义 | 关键 data |
|---|---|---|
| `subagent_started` | 后台任务已启动 | `job_id`、`agent`、`task`、`status`、`timeout_seconds`、`max_depth` |
| `subagent_finished` | 成功 | 同上 + `result` |
| `subagent_failed` | 失败/超时 | 同上 + `error` |
| `subagent_cancelled` | 被取消 | 同上 + `error` |

事件 `event_key` 形如：

```text
subagent_started:<job_id>
subagent_finished:<job_id>
subagent_failed:<job_id>
subagent_cancelled:<job_id>
```

注意：

- 一个 job 只会有一个终态事件；前端以 `job_id` 聚合，不要按 `agent` 聚合。
- `result` 可能被截断到 2,000 字符（后端 `check_subagent`/snapshot 的口径）。
- 事件里的 `result` 是子代理返回摘要，不是完整 transcript。

## 2. 前端状态查询

### 推荐：SSE + 事件归并

订阅：

```http
GET /runs/{run_id}/events
Authorization: Bearer <token>
Last-Event-ID: <seq>
```

收到 `subagent_*` 事件时按 `job_id` 更新卡片状态。

### 页面刷新/重连

- 用 `GET /runs/{run_id}/summary?after_seq=0` 拉完整事件，过滤 `subagent_*`。
- 用最后 `seq` 作为 SSE 的 `Last-Event-ID`。
- 如果只拿到 `subagent_started`，不要假设任务还在；可能进程重启丢失，显示“状态未知/可能已丢失”。

### 当前缺口

后端**没有**独立的 HTTP 子代理查询接口；`check_subagent` 是 Agent 工具，不是前端 API。

前端目前只有两条路：

1. 只看 SSE / summary 事件（推荐作为主路径）。
2. 让 Agent 调 `check_subagent`（需要发一条用户消息，不推荐作为状态轮询）。

**建议补后端接口（未实现）：**

```http
GET  /subagents/{job_id}
POST /subagents/{job_id}/cancel
GET  /runs/{run_id}/subagents
```

注意 job 状态目前是进程内；多 worker 部署下接口可能查不到另一个 worker 的 job，事件表才是跨进程事实源。

## 3. 结果反显

### 卡片字段

- `agent`：子代理名称
- `task`：任务摘要（最多 500 字符）
- `job_id`：可复制，便于排查
- `status`：`running / succeeded / failed / cancelled`
- `elapsed`：前端按 `subagent_started` 时间计算
- `result` / `error`
- `timeout_seconds` / `max_depth`

### 状态展示

| 状态 | UI |
|---|---|
| `running` | 加载态 + 已运行时长 + 取消按钮 |
| `succeeded` | 结果摘要 + “查看完整结果” + “继续处理” |
| `failed` | 错误 + 重试（重新 spawn） |
| `cancelled` | 已取消；可重新发起 |

### 结果反显原则

- 父级只拿 2,000 字符摘要；UI 可以展示事件里的 `result`，但要标注“摘要”。
- 不要把子代理 transcript 直接当主对话消息渲染。
- “继续处理”不要自动无限触发，建议：
  - 用户点击后，构造一条 user message：“子代理 `<job_id>` 已完成，结果如下：...”
  - 或由编排层在下一轮注入摘要。
- 失败/超时不要自动重试，避免后台成本放大；由用户触发。

## 4. “唤醒”语义

当前后端**不会自动 resume 已结束的父图**。

前端需要处理：

```text
subagent_finished 事件
  → 更新卡片
  → 如果当前对话仍活跃：提示“结果已就绪”
  → 用户点击“继续”/自动发送下一条消息
  → 父 Agent 调 check_subagent 或直接使用摘要
```

不要假设子代理完成后主 Agent 会自动收到结果。

## 5. 验收清单

- [ ] 同一 job 的 started/finished 事件能正确归并，不重复渲染。
- [ ] 刷新页面后，从 summary 重建 running/succeeded/failed/cancelled 卡片。
- [ ] 断线重连带 `Last-Event-ID`，不丢终态事件。
- [ ] `succeeded` 显示摘要和“继续处理”。
- [ ] `failed` 显示 error，手动重试。
- [ ] `cancelled` 可重新发起。
- [ ] 多 worker 下状态未知时，以事件表最后状态为准，不误报 running。
- [ ] 不把子代理完整 transcript 注入主对话。
