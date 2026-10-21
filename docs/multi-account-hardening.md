# 多账号 / 多租户上线改造清单

> 状态：**未实施，当前不能直接上线多账号。**
> 适用范围：账号级隔离（同一产品内不同用户）与组织级隔离（不同租户/公司）。
> 使用方式：上线前逐项勾选 P0；P1 按是否需要组织隔离决定；每项都应附双用户 E2E 测试。

## 1. 结论

当前代码可以演示“不同用户各自使用 thread / Store / 沙箱 / MinIO 前缀”，但还不是生产级多账号系统：

- 登录仍是 `MOCK_USER` 明文 token，没有真实身份校验。
- `assistants` 只校验权限，没有 owner 过滤，存在跨账号读取。
- MinIO 把桶级凭据放进沙箱命令，用户可尝试从进程参数中提取，路径前缀隔离会被绕过。
- 没有 tenant 模型，`owner` 只有 `user_id`。
- LiteLLM 共用网关 key，没有账号级预算/限流。
- 沙箱默认没有出口策略，生产需要 deny + allowlist。

## 2. 现状风险矩阵

| 资源 | 当前实现 | 状态 | 代码位置 |
|---|---|---|---|
| 登录/身份 | `MOCK_USER` 明文 token；不校验 Bearer schema | 不可上线 | `security/auth.py` |
| Threads/消息 | `@auth.on` 注入 owner 并返回 owner filter | 基础具备，需 E2E | `security/auth.py` |
| Store | namespace 第一段必须等于 user identity | 具备 | `security/auth.py` |
| Assistants | 只校验动作权限，无 owner filter | **明确漏洞** | `security/auth.py` |
| 沙箱 `/root` | PVC 名 `<tenant>-<sha256(user_id)[:12]>` | 基本具备 | `backends/sandbox_backend.py` |
| MinIO 路径 | `user_id/target/...` 前缀 | 路径隔离具备 | `tools/deploy.py` |
| MinIO 凭据 | 沙箱命令内携带桶级 access/secret key | **高风险** | `tools/deploy.py` |
| LiteLLM | 共用 key；仅按 thread 归集 | 无账号配额 | `utils/create_model.py` |
| Nacos | 全局 AgentSpec/Prompt/Skill | 产品配置可共享 | `config/nacos/*` |
| Langfuse | trace 带 `langfuse_user_id` | 有归因，无访问隔离 | `utils/trace_wrapper.py` |
| 租户/组织 | 只有 user identity；tenant 是全局 env | 不支持 | `config/sandbox_settings.py` |

## 3. P0：账号级上线前必须完成

### P0-1 真实登录与身份校验

- [ ] 用 OIDC/JWT 替换 `MOCK_USER`。
- [ ] 校验签名、`iss`、`aud`、`exp`、`nbf`；拒绝 `alg=none` 和未知 kid。
- [ ] 强制 `Authorization: Bearer <token>`；拒绝 `Basic`、空 token、伪造前缀。
- [ ] 从 `sub` 映射稳定 `identity`；权限从 token claims 或用户中心读取，不信任客户端提交。
- [ ] 支持 token 过期/吊销；至少支持短时 access token + 刷新流程。
- [ ] 启动时校验 JWKS 可达；失败 fail-fast。

**代码**：`security/auth.py`、`utils/shared.py`、`langgraph.json` 的 `auth.path`。

**建议配置（当前代码未读取，需实现）**：

```bash
AUTH_ISSUER=https://id.example.com/
AUTH_AUDIENCE=mentor
AUTH_JWKS_URL=https://id.example.com/.well-known/jwks.json
AUTH_TENANT_CLAIM=tenant_id
```

**验收**：

- 无 token / 过期 token / 错误 audience 均返回 401。
- `Basic admin` 不再被接受。
- 两个真实用户 A/B 的 token 不互通。

### P0-2 修复 Assistants 跨账号读取

- [ ] 创建 Assistant 时写入 `metadata.owner = identity`。
- [ ] 查询/搜索/更新/删除时返回 owner filter。
- [ ] 不要用“只校验 `assistants:read`”作为隔离。
- [ ] 迁移已有 Assistant：补 owner，无法判断归属的进入隔离区/人工确认。

**代码**：`security/auth.py` 的 `on_assistants`。

**验收**：

- A 创建 Assistant 后，B 的 `search/list/get` 看不到。
- B 直接按 A 的 assistant_id 读取返回 404/403。
- B 不能更新/删除 A 的 Assistant。

### P0-3 MinIO 凭据与对象隔离

- [ ] 移除沙箱命令中的桶级 `access_key` / `secret_key`。
- [ ] 改为以下之一：
  - 每用户/租户独立 MinIO 服务账号，policy 只允许 `<tenant>/<user>/` 前缀；
  - 宿主侧生成短期预签名 PUT URL，沙箱只拿到单对象 URL；
  - STS 临时凭据 + 前缀 policy。
- [ ] `deploy` 的 prefix 固定为 `<tenant>/<user>/<target>/`，`target` 继续做相对路径校验。
- [ ] 预签名 URL 只返回给当前用户；日志中不打印 secret。
- [ ] 桶策略保持私有，定期检查无匿名公共读。

**代码**：`tools/deploy.py`、`utils/minio.py`、`resource/minio-policy.json`。

**验收**：

- 用户在沙箱内执行 `ps` / 读 `/proc` 拿不到桶级凭据。
- A 的凭据/预签名 URL 不能读写 B 的前缀。
- 部署失败不会把 secret 写入工具结果或日志。

### P0-4 双用户隔离 E2E 测试

至少覆盖以下用例：

- [ ] A 不能 list/get B 的 thread；不能向 B 的 thread 发消息。
- [ ] A 不能 search/get/update/delete B 的 Assistant。
- [ ] A 不能读 B 的 Store namespace；namespace 第一段不是自己时被拒绝。
- [ ] A 的沙箱 `/root` 看不到 B 的文件；PVC 名不同。
- [ ] A 不能通过 MinIO SDK / 预签名 URL 访问 B 的对象。
- [ ] A 的 token 不能调用 B 的 cron / 后台任务。
- [ ] 过期 token、错误 audience、无 token 均失败。
- [ ] 并发场景：A/B 同时创建沙箱，缓存 key 不串。

**建议位置**：`tests/intergrations/test_multi_account.py`（待新增）。

### P0-5 沙箱网络出口

- [ ] 生产设置 `sandbox_config.network_default_action: deny`。
- [ ] `network_allow` 只放行必要目标：
  - Nacos（技能刷新）
  - MinIO（deploy）
  - 包仓库（如 pypi/npm，按业务需要）
- [ ] 禁止用户沙箱直接访问宿主机管理端口、数据库、内部管理 API。
- [ ] 验证 deny 后技能刷新与 deploy 仍可用。

**代码/配置**：`utils/sandbox.py`、Nacos `sandbox_config`。

### P0-6 LiteLLM 账号级配额

- [ ] 为每个 user（或 tenant）创建 LiteLLM virtual key。
- [ ] 设置 budget、RPM/TPM、并发限制。
- [ ] 模型请求带 user/tenant 归集字段；当前只有 `x-litellm-session-id`（thread），不够。
- [ ] 超额返回明确错误，不静默降级到共享 key。
- [ ] 定期对账：user/tenant → token/cost。

**代码**：`utils/create_model.py`、`utils/reasoning_chat_model.py`、LiteLLM 管理端。

**验收**：

- A 超预算不影响 B。
- 能按 user/tenant 查询消费。
- 日志/trace 能看到 user_id 与 tenant_id。

## 4. P1：组织级多租户（按需）

如果“账号”属于不同公司/组织，需要额外做：

- [ ] `identity` 增加 `tenant_id`（来自 JWT claim，例如 `tenant_id`）。
- [ ] Store namespace 改为 `(tenant_id, user_id, ...)`；`on_store` 校验前两段。
- [ ] Assistant/Thread/Cron metadata 同时写 `tenant_id` 与 `owner`，查询过滤两者。
- [ ] 沙箱 PVC 改为 `<tenant_id>-<sha256(user_id)[:12]>`；或 `<tenant>-<user-hash>`。
- [ ] MinIO 前缀改为 `<tenant_id>/<user_id>/...`；服务账号 policy 按 tenant 前缀。
- [ ] LiteLLM 按 tenant 建 key/budget；user 作为子配额。
- [ ] Nacos 按 tenant 分 namespace 或 label；当前全局配置无法按租户隔离。
- [ ] Langfuse 按 tenant 分项目，或至少按 tenant 做访问控制。
- [ ] 管理端增加 tenant 维度：成员、配额、审计。

**验收**：

- A 租户不能看到 B 租户的任何 thread/assistant/store/对象/消费记录。
- 同一租户内不同用户仍互相隔离。
- 租户切换/禁用后，旧 token 不能继续访问。

## 5. P2：运维与观测

- [ ] 审计日志：登录、Assistant 变更、deploy、沙箱创建/关闭、配额超限。
- [ ] 密钥轮换：JWT JWKS、MinIO 服务账号、LiteLLM key。
- [ ] 失败告警：401/403 激增、跨租户拒绝、MinIO policy 变更。
- [ ] 数据保留：thread/Store/Langfuse/产物桶按租户保留与删除。
- [ ] 备份与恢复：PVC、MinIO、LangGraph Store。
- [ ] 安全测试：越权、IDOR、路径穿越、命令注入、凭据泄漏。

## 6. 配置变更清单（实现后）

| 位置 | 配置 | 用途 | 状态 |
|---|---|---|---|
| `.env` | `AUTH_ISSUER` / `AUTH_AUDIENCE` / `AUTH_JWKS_URL` | OIDC/JWT | 待实现 |
| `.env` | `AUTH_TENANT_CLAIM` | 租户 claim | 待实现 |
| Nacos | `sandbox_config.network_default_action` | 沙箱出口默认策略 | 已支持 |
| Nacos | `sandbox_config.network_allow` | 出口白名单 | 已支持 |
| MinIO | 每用户/租户服务账号 policy | 对象前缀隔离 | 待改造 |
| LiteLLM | virtual keys / budgets | 账号配额 | 待配置 |
| Langfuse | project / access control | 观测隔离 | 待配置 |

> 表中“待实现/待改造/待配置”表示当前代码或基础设施尚未完成，不能按“已有能力”上线。

## 7. 发布顺序建议

1. 先在 staging 接真实 OIDC/JWT，保留 `MOCK_USER` 仅本地开关。
2. 修 Assistants owner，补双用户 E2E。
3. 改 MinIO 凭据方案，验证沙箱内拿不到桶级 secret。
4. 加沙箱网络 deny + allowlist，回归技能刷新/deploy。
5. 接 LiteLLM virtual key/budget。
6. 需要组织隔离时再加 tenant 维度；先做数据模型和 Store/Assistant，再做沙箱/对象存储。
7. 全量上线前跑一遍“双用户 + 双租户”验收清单。

## 8. 明确不能做的事

- 不要把 `MOCK_USER` 带到生产。
- 不要用“路径前缀”代替 MinIO 凭据隔离。
- 不要把桶级 secret 放进用户沙箱命令/环境变量。
- 不要把用户数据写进 `/shared`（所有用户共享只读挂载）。
- 不要在没有 E2E 越权测试的情况下上线 Assistants/Store 隔离。
- 不要假设多 worker 共享沙箱缓存；缓存是进程内的。
- 不要依赖预签名 URL 的“保密性”做隔离；URL 本身可被分享。

## 9. 上线 Go / No-Go

只有同时满足以下条件才建议上线多账号：

- [ ] P0-1 ~ P0-6 全部完成。
- [ ] 双用户 E2E 全部通过。
- [ ] 跨账号越权测试无高危/中危问题。
- [ ] MinIO 沙箱内无法获取桶级凭据。
- [ ] LiteLLM 账号配额生效。
- [ ] 有审计日志与告警。
- [ ] 回滚方案验证过（token/JWKS、密钥、配置）。
