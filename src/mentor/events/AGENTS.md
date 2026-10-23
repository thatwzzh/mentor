# Run 事件与投影

## 职责与入口

| 文件 | 职责 |
| --- | --- |
| `models.py` | `RunEvent` / `RunSummary` 与 17 种 `kind`，前端卡片与审计共用的唯一契约 |
| `store.py` | Postgres 事件表与 `InMemoryRunEventStore`，按 `(run_id, seq)` 重放 |
| `writer.py` | 业务侧唯一写入口，失败转 outbox |
| `outbox.py` | 落 JSONL 并提供 `mentor events replay` |
| `approvals.py` | `approval_events` 审批审计（Postgres / InMemory） |
| `context_summary.py` | 跨轮预压缩摘要的原子占位与领取 |
| `projection.py` | 消息到事件的投影、checkpoint 修剪、`build_summary` |
| `context.py` | `runtime_ids()` 统一读取 run / thread / user |

## 存储与序号

- `seq` 由 `run_event_seq` 表 `INSERT ... ON CONFLICT DO UPDATE` 分配，`(run_id, event_key)` 唯一约束去重；重复 event_key 仍然消耗一个 seq，所以 seq 可以不连续。
- 建表在首次 `get_run_event_store()` 时执行，没有迁移框架；新增列要写成 `ADD COLUMN IF NOT EXISTS` 才能兼容存量库。
- `list` 强制按 `user_id` 过滤并支持 `after_seq`，跨用户读同一条 run 得到空结果而不是报错。
- `append_event` 吞掉所有写入异常并落 outbox，因此事件缺失不会中断 run；`replay_outbox` 成功回放后才删除文件。
- 三个存储各自持连接池，共用 `RUN_EVENTS_DSN`；单例用模块级锁创建。

## 投影契约

- `project_messages_to_events` 把消息转成 `message` / `tool_call` / `tool_result` / `approval` / `artifact`；`ask_user_question` 的 ToolMessage 记作 `approval`。
- `project_state` 保留最近 `RUN_EVENTS_KEEP_LAST_TURNS` 轮人类消息，把超长 ToolMessage 截到 `RUN_EVENTS_MAX_TOOL_CHARS` 并提示「完整内容见 run_events」，历史摘要按尾部截到 `RUN_EVENTS_SUMMARY_MAX_CHARS`，产物引用最多保留 100 个。
- `estimate_tokens` 是 4 字符≈1 token 的粗估，用于压缩日志、预算预检、`usage` 兜底与摘要裁剪，不是计费口径。
- `build_summary` 的 status 只有 `finished` / `incomplete` / `unknown`；`pending_tools` 是「有 tool_call 无对应结果」的差集；token 汇总优先取 `usage` 事件，旧数据回落到 `model_response`。
- 预压缩摘要 `try_claim` 用 `INSERT ON CONFLICT DO NOTHING` 占位，超过 600 秒的 `generating` 行会被下次 claim 清掉；`claim_ready` 用 `DELETE ... RETURNING`，并发 run 只有一个能注入。

## 修改与验证

- 新增事件类型：扩展 `RunEventKind`、写入方与 `build_summary`，并检查前端契约 `contract_version`；`schema_version` 目前只写入、不参与分支。
- 改 `RUN_EVENTS_*` 默认值会影响 checkpoint 大小与摘要长度，需同时核对 `middlewares/run_projection.py`。
- 单测 `tests/units/test_run_projection.py` 覆盖投影、去重与用户过滤、SSE、outbox 重放、摘要统计；Postgres 语义由 `tests/intergrations/` 下三个用例在真实库验证，用 `RUN_EVENTS_POSTGRES_DSN` 启用。
