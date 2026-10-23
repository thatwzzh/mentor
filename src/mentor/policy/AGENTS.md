# 工具策略规则

## 职责与入口

`tool_policy.py` 定义 `ToolPolicySettings`、`PolicyRule`、`ToolPolicyConfig`、`ToolPolicy` 与进程级单例 `get_tool_policy()`。这里只做判定、不依赖 LangGraph，可单测；执行侧在 `middlewares/tool_policy.py`。

## 判定契约

- 规则来自 `TOOL_POLICY_FILE`（默认 `config/tool_policy.yaml`，相对进程工作目录）；文件不存在时使用内置的三条默认规则，不是「无策略」。
- `TOOL_POLICY_ENABLED=false` 时整体回退为全部 allow，`decide` 仍返回 `PolicyDecision`，reason 为 `policy disabled`。
- `tool` 用 `fnmatch` 做 glob 匹配，`pattern` 是对「排序后的参数 JSON」做不区分大小写的正则，第一条命中即生效；未命中按 `defaults[risk]` 判定，缺省 `destructive→prompt`、其余 allow。
- 参数是整段 JSON，规则无法区分字段：把 `tool` 写成 `"*"` 再配宽泛正则，会连只读工具一起命中。
- `max_prompts_per_run` 默认 3，是单 run 审批次数上限；计数在 `approval_events` 的 `requested` 行上做，进程重启后仍有效。
- `get_tool_policy()` 缓存单例，改 YAML 后需要重启进程才生效，没有热加载。

## 修改与验证

- 新规则同时改 `config/tool_policy.yaml` 与 `_DEFAULT_RULES`（文件缺失时的兜底），两处语义保持一致。
- 规则顺序有意义：宽泛规则放前面会遮住后面更精确的规则。
- `tests/units/test_tool_policy.py` 覆盖 YAML 加载、顺序与默认值、deny 不调用 handler、prompt 记录 `requested→allow`、上限拒绝。策略与真实工具副作用的组合仍需在 Agent 中验证。
