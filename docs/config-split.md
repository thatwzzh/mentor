# 配置分工：Agent 代码 vs LiteLLM

> 原则：**LiteLLM 管请求级路由韧性，Agent 管 run 级预算与上下文。**
> 本文是唯一口径；不要在代码和 LiteLLM UI 里重复配置同一项。

## 1. LiteLLM 管理端 / DB 配置

LiteLLM 的模型与路由配置以管理端（`STORE_MODEL_IN_DB=True`）或 `litellm-config.yaml` 为准，不要两边各写一套。

| 配置 | 放在哪里 | 说明 |
|---|---|---|
| 模型名 / provider / 凭证 | LiteLLM UI 模型配置 | `model_name`、`litellm_params.model`、credential |
| 请求超时 | LiteLLM model `litellm_params.timeout` 或 router `timeout` | 建议 120–300s |
| 重试次数 | LiteLLM `litellm_params.max_retries` 或 router `num_retries` | 建议 2 |
| fallback | LiteLLM `Fallbacks` 页 | 必须显式配置备用模型 |
| 熔断/cooldown | `allowed_fails` + `cooldown_time` | 建议 allowed_fails=3、cooldown=30 |
| RPM / TPM | `litellm_params.rpm/tpm` | 保护上游配额 |
| 默认最大输出 | `litellm_params.max_tokens` | 不是 `model_info.max_tokens` |
| 模型价格 | `model_info.input_cost_per_token` / `output_cost_per_token` | 用于 LiteLLM 成本统计 |
| key/team 预算 | LiteLLM budget | 账单级成本控制 |
| 路由策略 | `routing_strategy` | 如 simple-shuffle / latency-based |

### 两个容易混的字段

- `model_info.max_tokens`：模型元数据，用于上下文/成本估算，**不保证限制单次输出**。
- `litellm_params.max_tokens`：请求默认输出上限，才会真正约束生成长度。

## 2. Agent 代码 / `.env` 配置

| 配置 | 是否保留 | 原因 |
|---|---|---|
| `MODEL_REQUEST_TIMEOUT_SECONDS` | 保留 | 客户端超时保护；必须 ≥ LiteLLM timeout，避免客户端先断、gateway fallback 走不完 |
| `max_retries=0` | **代码写死，无环境变量** | LiteLLM 已重试；Agent 再重试会放大 |
| `CONTEXT_WINDOW_TOKENS` / `CONTEXT_TRIGGER_RATIO` / `CONTEXT_KEEP_MESSAGES` | 保留 | Agent 压缩策略；LiteLLM 的 `model_info.max_input_tokens` 不会自动注入 ChatOpenAI profile |
| `CONTEXT_PRECOMPACT_RATIO` | 保留 | 回答后异步预压缩水位 |
| `RUN_BUDGET_MAX_TOKENS` / `RUN_BUDGET_MAX_SECONDS` / `RUN_BUDGET_MAX_OUTPUT_TOKENS` | 保留 | LiteLLM 的预算按 key/team/时间窗，不是单 run |
| `RUN_EVENTS_*` / `TOOL_*` / `TOOL_POLICY_*` | 保留 | 事件、工具、策略，与 LiteLLM 无关 |
| fallback / circuit breaker | **不写代码** | 全部交给 LiteLLM |

### 推荐 Agent `.env`

```bash
MODEL_REQUEST_TIMEOUT_SECONDS=300

CONTEXT_WINDOW_TOKENS=128000
CONTEXT_TRIGGER_RATIO=0.8
CONTEXT_PRECOMPACT_RATIO=0.60
CONTEXT_KEEP_MESSAGES=20

RUN_BUDGET_MAX_TOKENS=200000
RUN_BUDGET_MAX_SECONDS=900
RUN_BUDGET_MAX_OUTPUT_TOKENS=4096
```

如果模型确实有 991,808 输入窗口，再把 `CONTEXT_WINDOW_TOKENS` 调到 991808；否则 128k 是保守值。

## 3. `litellm-config.yaml` 当前定位

当前文件只保留：

```yaml
router_settings:
  optional_pre_call_checks:
    - session_affinity
```

- `session_affinity` 是 LiteLLM 路由能力，UI 不一定覆盖，所以保留。
- **不要**再往这里加 fallbacks/num_retries/cooldown；这些在 UI/DB 配。
- 如果确认 UI 也能配 session affinity，可把该文件缩成空配置或删除挂载；否则保留。

## 4. LiteLLM UI 推荐值

| 项 | 建议 |
|---|---:|
| `TIMEOUT` | 120–300s |
| `NUMBER OF RETRIES` | 2 |
| `ALLOWED FAILS` | 3 |
| `COOLDOWN TIME` | 30s |
| `RETRY AFTER` | 1s |
| `ENABLE_WEIGHTED_FAILOVER` | 多 deployment 时再开 |

注意：当前截图里 `TIMEOUT=6000`、`COOLDOWN=5` 需要调整。

## 5. 验证清单

1. LiteLLM UI 保存后重启 `mentor-litellm-1`，确认值仍在。
2. `Fallbacks` 页配置 primary → backup，且 backup 的 context window ≥ primary。
3. 故意触发 429/500，确认 LiteLLM 日志有 retry/fallback/cooldown。
4. Agent 侧设 `MODEL_REQUEST_TIMEOUT_SECONDS` ≥ LiteLLM timeout。
5. `usage` 事件里的 `served_model` 能看出是否发生了 fallback。
6. `RUN_BUDGET_*` 设置为非 0 后，验证超限会抛 `BudgetExceeded`。
