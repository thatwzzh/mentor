# 记忆分层：短记忆预算与长记忆

> 状态：**P0 硬化完成**。  
> 已做：长记忆表 + 三工具 + 注入预算 + 租户/用户隔离 + pgvector 可选 + 异步抽取 + 过期清理 + 基础评测测试。  
> 未做：六份 ContextBudget、记忆管理 UI/API、importance/decay、完整记忆 golden 集。

## 1. 两层记忆

| 层 | 载体 | 用途 |
|---|---|---|
| 短记忆 | checkpoint / run_events / `history_summary` | 当前线程上下文 |
| 长记忆 | Postgres `agent_memories` | 跨 thread 的用户偏好、事实、决策、TODO |

checkpoint 是线程快照，run_events 是 UI 投影，都不是可复用记忆；长记忆单独存。

## 2. 短记忆预算

当前实现：

- `MEMORY_MAX_INJECT_TOKENS=800`：注入的长记忆块最多约 800 token。
- 工具结果超过 8KB：头 4KB + 尾 1KB，全文 artifact + MinIO，只回摘要。
- `pinned=True` 的记忆优先注入，且不参与摘要。
- `CONTEXT_*` 控制模型上下文压缩，与长记忆注入预算分开。

尚未做到六份配额（system prompt/工具 schema/记忆块/技能索引/工具结果/输出预留）的精确拆分；目前是“记忆块硬预算 + 上下文压缩 + 工具截断”的组合。

## 3. 长记忆表

`agent_memories`：

```text
id, tenant_id, user_id, kind, key, content, confidence,
source_thread_id, pinned, embedding, last_used_at,
expires_at, superseded_by, created_at
```

- 命名空间：`tenant_id + user_id`
- `kind`：`preference / fact / decision / todo / artifact`
- 同一个 `key` 新值写入时，旧值 `superseded_by = 新 id`，检索只看 active
- `expires_at`：过期不召回
- `forget`：物理删除，删除后不可检索
- `embedding` / `embedding_vec`：配置 embedding 且数据库有 pgvector 时参与检索；否则回落 lexical

## 4. 检索

当前支持两档：

```text
有 pgvector + MEMORY_EMBEDDING_MODEL：
  向量距离 + 关键词 + confidence + recency + pinned
无 pgvector / 未配置 embedding：
  关键词 + confidence + recency + pinned
```

- `docker-compose.yml` 的 `mentor-postgres` 已切到 `pgvector/pgvector:pg17`；`MemoryStore` 启动时尝试 `CREATE EXTENSION vector` 并补 `embedding_vec` 列。
- 旧库没有扩展时自动回落 lexical，不会阻断启动。
- 中文关键词仍是连续字符匹配，语义召回依赖 embedding。
- 当前输入与记忆冲突：system prompt 明确“当前用户输入优先”。

## 5. 写入

两档写入：

1. 显式：

```text
memory_write(kind, key, content, confidence, pinned, expires_in_days)
```

2. 回合结束异步抽取：

```text
MemoryExtractionMiddleware
  → 主模型抽取 JSON 候选
  → 置信度门槛
  → 敏感信息（api_key/password/token/sk-/BEGIN）直接跳过
  → 同 key supersede
```

- 异步任务不阻塞回答。
- 敏感信息当前是“跳过”，不是“脱敏后写 + 用户确认”。
- `memory_forget` 是 destructive 工具；主 Agent 调用时会走策略/审批（如果策略开启）。

## 6. 工具与中间件

| 组件 | 作用 |
|---|---|
| `memory_search` | 按当前 tenant/user 检索 |
| `memory_write` | 显式写入/覆盖 |
| `memory_forget` | 删除某个 key 的全部版本 |
| `MemoryMiddleware` | 模型调用前按预算注入 top 3–5 条 |

- 主 Agent 自动获得三个工具。
- `MemoryMiddleware` 只挂主 Agent；子 Agent 不自动注入长记忆。
- 长记忆注入是 `SystemMessage` 块，不是用户消息。

## 7. 配置

```bash
MEMORY_ENABLED=true
MEMORY_TENANT=default
MEMORY_MAX_INJECT_TOKENS=800
MEMORY_SEARCH_LIMIT=5
MEMORY_MIN_CONFIDENCE=0.3
MEMORY_EMBEDDING_MODEL=       # 留空 = lexical only
MEMORY_EMBEDDING_DIM=1536
MEMORY_AUTO_EXTRACT=true
```

当前身份体系里 `tenant_id` 已经是 auth claim：`get_tenant_id()` 先取 `langgraph_auth_user.tenant_id`，取不到才回落 `MEMORY_TENANT`。mock 用户也已带上 `tenant_id`，因此跨租户隔离可以在本地直接验证。

检索的已知边界：候选集先按 `pinned/confidence/created_at` 取前 50 条再重排，向量距离只参与重排、不参与取候选。记忆量很大（千条以上）时，最相似的条目可能进不了候选集；届时需要改成两路召回（关键词 Top50 + 向量 Top50 取并集）或直接用 `<=>` 做 ANN 排序。

## 8. 验收对照

| 验收 | 状态 |
|---|---|
| 新会话能召回用户偏好 | 已实现（关键词/置信度检索 + 注入） |
| 过期/冲突记忆有处理 | 过期不召回；同 key supersede |
| 删除后不可检索 | `memory_forget` 物理删除 |
| 跨租户隔离 | auth claim `tenant_id` 优先，缺失回落 `MEMORY_TENANT`；store 层过滤 |
| 注入后 token 不超预算 | `MEMORY_MAX_INJECT_TOKENS` 截断 |
| 异步抽取 | 已实现；敏感信息跳过，未做用户确认 |
| pgvector 向量召回 | 可选实现；未配置 embedding 时 lexical fallback |
| 过期清理 | `mentor memory cleanup --retention-days 30`（建议挂日级定时任务）；store 层同时提供 `cleanup_expired` |

清理规则：`expires_at` 已过的记录直接删；被 supersede 的旧版本保留 `retention_days` 天后删，保留窗口用于回溯“为什么改了这条记忆”。

## 9. 后续

1. 六份 ContextBudget 精确拆分（system/tools/memory/skills/tool-result/output）。
2. `importance` / `usage_count` / `max_unused_days` 衰减与淘汰。
3. 敏感记忆“脱敏 + 用户确认”流程。
4. 完整记忆 golden 集：应召回/不应召回/过期/冲突/跨租户/删除。
5. Langfuse `memory retrieval` / `memory write` span。
6. 记忆管理 HTTP API / UI。
7. embedding 维度变更/重建索引流程。
