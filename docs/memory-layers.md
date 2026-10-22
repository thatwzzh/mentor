# 记忆分层：短记忆预算与长记忆

> 状态：**最小闭环已实现**。  
> 已做：长记忆表 + 三工具 + system prompt 注入 + token 预算 + 租户/用户隔离。  
> 未做：pgvector/embedding 检索、回合结束异步抽取、rerank、记忆评测集。

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
- `embedding`：字段预留，当前不参与检索

## 4. 检索

当前是**混合检索的降级版**：

```text
关键词匹配（content/key）
  + confidence
  + recency
  + pinned 加权
  → 取 top 3–5
```

- 没有 pgvector，所以没有向量召回。
- 中文按连续字符做关键词，语义召回能力有限。
- 当前输入与记忆冲突：system prompt 明确“当前用户输入优先”。

## 5. 写入

当前只做**显式写入**：

```text
memory_write(kind, key, content, confidence, pinned, expires_in_days)
```

- 用户明确说“记住”时调用。
- 同 key 覆盖并保留旧版本（superseded）。
- 敏感信息脱敏、异步抽取候选、置信度门槛仍未实现。

`memory_forget` 是 destructive 工具；主 Agent 调用时会走策略/审批（如果策略开启）。

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
```

当前身份体系没有 tenant claim，`MEMORY_TENANT` 是全局默认值；接入真实多租户时，应改为从 auth claims 取 `tenant_id`。

## 8. 验收对照

| 验收 | 状态 |
|---|---|
| 新会话能召回用户偏好 | 已实现（关键词/置信度检索 + 注入） |
| 过期/冲突记忆有处理 | 过期不召回；同 key supersede |
| 删除后不可检索 | `memory_forget` 物理删除 |
| 跨租户隔离 | store 层按 tenant_id+user_id 过滤；需多租户 E2E |
| 注入后 token 不超预算 | `MEMORY_MAX_INJECT_TOKENS` 截断 |
| 异步抽取/敏感信息确认 | 未实现 |
| pgvector 向量召回 | 未实现，字段/reserved |

## 9. 后续

1. 接 pgvector（换镜像/扩展）+ embedding，混合检索。
2. 回合结束异步抽取候选：去重、冲突检测、置信度门槛。
3. 敏感信息脱敏 + 用户确认。
4. `memory_search` 评测集：应召回/不应召回/过期/冲突/跨租户/删除。
5. Langfuse span：memory retrieval / write。
6. 多租户 auth claim 替换 `MEMORY_TENANT`。
