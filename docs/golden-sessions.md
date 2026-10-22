# 可重放评测：Golden Session

> 目标：改一行不坏一片。PR 门禁用**脚本化、无网络**的 golden；真实模型录制只做夜间/人工质量趋势，不进 CI gate。

## 1. 两种 golden

| 类型 | 用途 | 是否进 CI | 是否调真实模型 |
|---|---|---|---|
| Scripted golden | 回归工具选择、审批、超时、事件序列 | 是 | 否 |
| Real-model recording | 质量趋势、真实模型录制 | 否 | 是 |

当前仓库只实现 **Scripted golden**。真实模型录制见第 6 节，属于后续/夜间能力。

## 2. 目录

```text
src/mentor/golden/
├─ models.py      # GoldenCase / GoldenStep / GoldenTool / GoldenExpected
└─ runner.py      # record / replay / load / save
tests/golden/sessions/
├─ basic_echo.json
├─ approval_denied.json
└─ tool_timeout.json
tests/units/test_golden.py
```

## 3. Golden 结构

```json
{
  "version": "1",
  "name": "basic_echo",
  "description": "基础工具调用",
  "input": "请调用 echo 工具",
  "steps": [
    {
      "content": "",
      "tool_calls": [
        {"name": "echo", "args": {"text": "hi"}, "id": "call-echo"}
      ]
    },
    {"content": "工具返回 hi"}
  ],
  "tools": [
    {"name": "echo", "result": "hi", "risk": "read", "timeout_s": 10}
  ],
  "policy_rules": [],
  "expected": {
    "event_kinds": ["run_started", "..."],
    "event_names": [null, "..."],
    "final": "工具返回 hi"
  }
}
```

- `steps`：脚本化模型响应，可含 `tool_calls`；不访问真实模型。
- `tools`：mock 工具，结果固定；`delay_s` 可制造超时。
- `policy_rules`：本 case 的 `allow/prompt/deny` 规则。
- `expected`：只断言稳定字段，避免时间戳/随机 ID 脆弱。

## 4. 命令

```bash
# 回放；不调真实 LLM、不访问网络
uv run mentor golden replay --file tests/golden/sessions/basic_echo.json

# 录制/更新 expected（脚本化执行，仍不调真实模型）
uv run mentor golden record --file tests/golden/sessions/basic_echo.json

# 输出到新文件
uv run mentor golden record \
  --file /tmp/case.json \
  --out tests/golden/sessions/new_case.json
```

新增 case：

1. 写一个 `expected: null` 的 JSON。
2. `mentor golden record` 生成 expected。
3. 人工 review diff，确认事件序列符合预期。
4. 提交 case + expected。

## 5. 当前覆盖

| case | 覆盖 |
|---|---|
| `basic_echo` | 基础工具调用、模型两步收尾 |
| `approval_denied` | 策略 deny、工具不执行、approval 事件 |
| `tool_timeout` | 工具超时、tool_result error、模型继续收尾 |

尚未覆盖（后续加）：

- prompt 审批 + resume
- 上下文压缩 / 预压缩
- 会话恢复
- 异步子代理完成事件
- MCP deferred 工具
- 预算超限

## 6. 真实模型录制（未实现，仅文档）

真实模型录制**不要**塞进上面的 scripted golden。

### 建议流程

```text
真实 Agent run（LiteLLM + Nacos + 沙箱）
  → 记录输入、模型响应、工具调用、run_events
  → 脱敏（key/token/用户数据/文件内容）
  → 存到非 CI 目录或对象存储
  → 夜间跑 Langfuse Dataset 看质量趋势
```

### 硬规则

- **不进 PR gate**：真实模型输出非确定，模型升级必然漂移。
- **不提交原始 transcript**：可能含密钥、用户数据、内部文档。
- **不做回放断言**：真实录制用于质量/成本趋势，不是事件级回归。
- **保留模型版本**：记录 `model`、`served_model`、`agent_spec_hash`、`prompt_hash`。
- **脱敏后再存**：工具参数、文件路径、URL query、token 都要处理。

### 建议实现（后续）

1. `mentor golden record-real --thread <thread_id>` 从 run_events 导出。
2. 生成脱敏后的 `RealGolden`（非 `GoldenCase`）。
3. 夜间任务跑 Langfuse Dataset + LLM Judge，生成质量趋势。
4. 只在人工 review 后归档，不自动提交。

## 7. CI gate 建议

```text
pytest tests/units
pytest tests/units/test_golden.py
1 条 smoke（真实服务健康检查，可选）
```

性能回归（后续）：

- 沙箱创建
- 首 token
- 工具 p95
- 压缩耗时

> 20% 回归阻断；需要固定硬件/固定模型基线，否则噪声会误报。

## 8. 当前边界

- 只断言事件 `kind`、事件 `name`、最终输出；不断言完整 `data`/args。
- 模型脚本是固定的，不能发现“真实模型变笨”。
- 真实模型录制/夜间 Langfuse 尚未接。
- 性能基线尚未落。
