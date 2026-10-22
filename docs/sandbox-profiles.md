# 沙箱 Profile：按模型能力档位调优

> 状态：**最小闭环已实现**。  
> 已做：Nacos profile 解析、资源/超时/网络映射、步数/工具/并发/输出护栏。  
> 未做：预热池/快照、按 p95/OOM 自动调参、真实压测基线。

## 1. 为什么按档位，不按模型名

弱模型容易循环、工具调用不稳；强模型需要更长任务链和更大工作区。  
按模型名 fork Backend 会失控，所以收敛为能力档位：

```text
fast / balanced / reasoning / browser / data
```

模型只声明档位：

```json
{
  "model": {
    "name": "chat-standard",
    "profile": "reasoning"
  }
}
```

## 2. Nacos AgentSpec 配置

```json
{
  "sandbox_profiles": {
    "fast": {
      "image": "python:3.12-slim",
      "cpu": "1",
      "memory_mb": 1024,
      "command_timeout_seconds": 60,
      "max_steps": 12,
      "max_tool_calls": 8,
      "max_parallel_tools": 1,
      "max_output_chars": 4000
    },
    "balanced": {
      "cpu": "2",
      "memory_mb": 4096,
      "command_timeout_seconds": 180,
      "max_steps": 30,
      "max_tool_calls": 20,
      "max_parallel_tools": 2,
      "max_output_chars": 8000
    },
    "reasoning": {
      "cpu": "4",
      "memory_mb": 8192,
      "command_timeout_seconds": 600,
      "ready_timeout_seconds": 240,
      "max_steps": 80,
      "max_tool_calls": 60,
      "max_parallel_tools": 3,
      "max_output_chars": 12000
    },
    "browser": {
      "cpu": "2",
      "memory_mb": 4096,
      "command_timeout_seconds": 300,
      "network_default_action": "deny",
      "network_allow": ["example.com"],
      "max_steps": 40,
      "max_tool_calls": 30
    }
  }
}
```

未显式写的字段回落到全局 `sandbox_config`：

- `image`
- `cpu`
- `memory`
- `timeout_seconds`
- `ready_timeout_seconds`
- `command_timeout_seconds`
- `network_default_action`
- `network_allow`

## 3. Profile 字段

| 字段 | 作用 |
|---|---|
| `image` / `cpu` / `memory_mb` | 容器资源 |
| `timeout_seconds` | 沙箱存活 TTL |
| `ready_timeout_seconds` | 容器拉起等待 |
| `command_timeout_seconds` | 单条命令默认超时 |
| `network_default_action` / `network_allow` | 出口网络策略 |
| `max_steps` | 模型调用步数上限 |
| `max_tool_calls` | 工具调用次数上限 |
| `max_parallel_tools` | 并发工具上限 |
| `max_output_chars` | 单工具输出上限 |

## 4. 运行时行为

- `AgentFactory` 按 `AgentModelConfig.profile` 选 profile，设置 run 级 ContextVar。
- `SandboxBackend(profile=...)` 创建沙箱时传 profile 的镜像/CPU/内存/网络/TTL。
- `aexecute` 默认使用 profile 的 `command_timeout_seconds`。
- `SandboxProfileGuardMiddleware`：
  - 超过 `max_steps` 抛 `SandboxProfileLimitError`，终止 run。
  - 超过 `max_tool_calls` 返回 `status=error` 的 ToolMessage。
  - `max_parallel_tools` 用 semaphore 限制并发工具。
- `ToolExecutorMiddleware` 把 `max_output_chars` 作为上限；工具自身更小则保留更小值。

## 5. 不做

- 不为每个模型 fork 一套 Backend。
- 不替换 OpenSandbox 的容器隔离。
- 不为“适配弱模型”放宽网络/审批。
- 不做预热池/快照（后续；当前只在进程内按 thread 复用连接）。
- 不做基于 Langfuse 的自动调参。

## 6. 指标与调参

应从 Langfuse 看：

- 工具 p95 耗时
- OOM 率
- 工具错误率
- 循环步数 / 工具调用次数
- 每任务成本

指标不达标时先调 profile 和工具面，不要先换模型。

## 7. 验收对照

| 验收 | 状态 |
|---|---|
| 不同 profile 的 p95/成本可对比 | 需真实压测，当前未落基线 |
| 弱模型不因工具过多循环 | `max_steps/max_tool_calls` 已硬限制 |
| 强模型不被统一超时截断 | `reasoning` 可单独配置 command/timeout |
| 安全基线不回归 | 未放宽网络/审批；profile 只覆盖资源与限额 |
| 预热池/快照 | 未实现 |

## 8. 迁移注意

- 新增 `sandbox_profiles` 字段是向后兼容的；未配置时回落 `sandbox_config`。
- `AgentModelConfig.profile` 默认 `balanced`，所以没有 profile 的旧 Agent 也会走默认护栏（30 steps / 20 tools / 8k output）。
- 如果旧 Agent 任务链超过默认值，先在 Nacos 加 profile 调大，不要直接关护栏。
- 已存在沙箱连接不会因为 profile 变化重建；需要等 TTL/下次创建或显式回收。
