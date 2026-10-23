# 环境与配置模型

## 职责与分工

- `base_settings.py:BaseSettingsWithEnv` 统一加载 `.env`（相对进程工作目录）并忽略未知字段；各 `*_settings.py` 在模块导入时实例化。
- `errors.py` 区分「不存在」与「不可用」：`ConfigNotFoundError` 允许降级，`ConfigUnavailableError` 必须 fail-fast。
- `layers.py` 实现四层合并与 `AgentSpecOverride` 白名单；`nacos/` 负责远程资源读取，详细规则见其指令文件。
- `doctor.py` 提供启动前体检；`sandbox_profiles.py` 按 `model.profile` 解析沙箱档位。`ToolPolicySettings` 不在本目录，在 `policy/tool_policy.py`。

## Settings 与环境变量前缀

| Settings | 前缀 |
| --- | --- |
| NacosSettings | `NACOS_` |
| OpenAISettings | `OPENAI_` |
| MinioSettings | `MINIO_` |
| LangfuseSettings | `LANGFUSE_` |
| SandboxSettings | `OPEN_SANDBOX_` |
| ContextSettings | `CONTEXT_` |
| ModelSettings | `MODEL_` |
| BudgetSettings | `RUN_BUDGET_` |
| EventSettings | `RUN_EVENTS_` |
| ToolSettings | `TOOL_` |
| SubAgentSettings | `SUBAGENT_` |
| BenchMarkSettings | `BENCHMARK_` |
| TavilySettings | `TAVILY_` |

- 实例在导入时构造，改环境变量不会重建已有对象；验证配置要先准备环境再导入模块。
- 连接参数多为空字符串默认值，默认值不代表已配置或已连通；缺参数时由调用方给出明确错误。
- `NacosSettings` 去掉地址末尾斜杠，`SandboxSettings.tenant` 规整为 DNS-1123 小写；MinIO 的 `endpoint` 不带协议，TLS 由 `secure` 控制。

## 合并与校验

- 四层顺序：Pydantic defaults < `.env` < Nacos < `configurable.agent_spec_override`；override 只允许 `AgentSpecOverride` 声明的字段，`extra="forbid"` 让拼写错误直接暴露。
- `merge_agent_spec` 只覆盖显式传入的模型字段，避免用默认 `name` 覆盖 Nacos 模型名；`resolve_label` 只接受 `staging` / `production`，缺省取 `NACOS_LABEL`。
- `resolve_sandbox_profile` 用 `model.profile`（缺省 `balanced`）取 `sandbox_profiles[name]`，未显式声明的字段回落到 `sandbox_config`；`Gi/Mi/G/M` 会统一换算成 MB。
- `doctor` 只读：读一次 AgentSpec、请求网关 `/models`、列一次产物桶、读 `sandbox_config`，不建桶也不起沙箱；任一项失败返回非 0 退出码。

## 修改与验证

- 新配置沿用服务前缀，补类型与校验，同步 `.env.example`、消费者与本文件；不要把真实凭据写进仓库或日志。
- 改名、改默认值或改单位时核对全部调用方：Nacos TTL 用秒、trace retention 用天、预签名有效期用秒。
- 单测 `tests/units/test_config_layers.py` 覆盖 override 白名单、label 校验、Schema、doctor 必填项与 CLI。配置解析通过不代表服务可用，连通性要单独验证。
