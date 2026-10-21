# 上线前待改动项（Backlog）

> 状态：**待实施**。以下两项不阻塞本地开发，但**不完成不建议上线**。
> 4.2.6 会话投影已完成；本清单只保留上线前必须处理的事项。

## 1. 总览

| 编号 | 项目 | 优先级 | 状态 | 详细说明 |
|---|---|---|---|---|
| 4.2.5 | 鉴权与多租户：真实身份 + 双层隔离 | P0 | 待实施 | [multi-account-hardening.md](multi-account-hardening.md) |
| 4.2.7 | 可观测与评测：指标 + golden 回归 | P0/P1 | 待实施 | 本文 §3 |

上线 Go/No-Go 条件：**4.2.5 的 P0-1 ~ P0-6 全部完成，4.2.7 的指标与 golden 回归通过**。

## 2. 4.2.5 鉴权与多租户（P0）

必须完成：

- [ ] OIDC/JWT 替换 `MOCK_USER`；校验签名、`iss/aud/exp`，强制 Bearer。
- [ ] Assistants 增加 owner 过滤，修复跨账号读取。
- [ ] Threads / Store / Cron 双用户隔离 E2E。
- [ ] MinIO 移除沙箱内桶级凭据，改 STS/预签名上传或按用户前缀的最小权限账号。
- [ ] 沙箱网络默认 deny + allowlist。
- [ ] LiteLLM 账号级 virtual key / budget / RPM/TPM。
- [ ] 需要组织隔离时引入 tenant：Store namespace、Assistant owner、PVC、MinIO 前缀、LiteLLM key。

验收：用户 A 不能读取/修改 B 的 thread、assistant、store、`/root` 文件、MinIO 对象；过期/错误 token 全部 401。

> 完整风险矩阵与逐项验收见 [multi-account-hardening.md](multi-account-hardening.md)。

## 3. 4.2.7 可观测与评测（P0/P1）

### 3.1 指标（P0）

- [ ] 工具指标：调用次数、成功率、`input/policy/timeout/system` 分类、截断率、artifact 写入失败。
- [ ] Run 指标：耗时、turn 数、token 用量、压缩前后 token、checkpoint 大小。
- [ ] 沙箱指标：创建/复用/关闭、空闲回收、创建失败、超时。
- [ ] 配置指标：spec_hash 命中/重编译、Nacos 读取失败、doctor 失败。
- [ ] 安全指标：401/403、跨租户拒绝、MinIO policy 变更。
- [ ] 成本指标：按 user/tenant 的 token/费用。

### 3.2 Trace（P0）

- [ ] `spec_hash`、`thread_id`、`run_id`、`user_id`、`tenant_id` 进入 Langfuse metadata。
- [ ] 工具事件与 `run_events` 的 seq 对齐，便于 trace ↔ UI 对账。
- [ ] 错误 trace 保留 error_kind、tool name、duration_ms，不记录 secret/完整工具输出。

### 3.3 Golden 回归（P0）

- [ ] 固定一组 golden case（正常问答、工具调用、审批、产物部署、超时、配置失败）。
- [ ] 每次发布前跑 golden suite；失败阻断发布。
- [ ] 记录 baseline：输出、工具序列、token、耗时；允许阈值内的非确定性。
- [ ] 失败样例进入 Langfuse Dataset，形成回归集。

### 3.4 告警与 SLO（P1）

- [ ] 工具 system 错误率、run 失败率、p95 延迟、Nacos/MinIO/OpenSandbox 可用性。
- [ ] 成本异常（单 user/tenant token 突增）。
- [ ] 告警路由到值班渠道；保留 run_id/thread_id 便于定位。

验收：能在一次发布前回答“这次改动影响了哪些工具/模型/成本”，并能用 golden 回归判断是否可回滚。

## 4. 与 4.2.6 的关系

4.2.6 已提供：

- `run_events` 事件表与 `GET /runs/{id}/summary`
- `ToolMessage.artifact` 结果信封
- 压缩日志：`before_tokens / after_tokens / kept_messages / summary_hash`

4.2.7 直接复用这些字段做指标与 golden 回归，不需要再建一套事件口径。

## 5. 实施顺序

1. 4.2.5 P0-1 真实鉴权。
2. 4.2.5 P0-2 Assistants owner + 双用户 E2E。
3. 4.2.5 P0-3 MinIO 凭据隔离。
4. 4.2.7 指标 + trace metadata + golden 回归。
5. 4.2.5 P0-5/6 网络与 LiteLLM 配额。
6. 全量 Go/No-Go 检查。
