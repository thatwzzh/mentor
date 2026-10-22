"""Golden session 数据模型：脚本化模型、mock 工具、期望事件。"""

from typing import Literal

from pydantic import BaseModel, Field

from mentor.tools.specs import ToolRisk


class GoldenStep(BaseModel):
    """一步脚本化模型响应。"""

    content: str = ""
    tool_calls: list[dict] = Field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0


class GoldenTool(BaseModel):
    """一个 mock 工具；结果固定，可选延迟制造超时。"""

    name: str
    result: str
    risk: ToolRisk = "read"
    timeout_s: float = 10
    delay_s: float = 0


class GoldenPolicyRule(BaseModel):
    """golden case 的策略规则。"""

    tool: str
    decision: Literal["allow", "prompt", "deny"]
    reason: str = ""


class GoldenExpected(BaseModel):
    """回放断言：只比较稳定的事件序列、工具名和最终输出。"""

    event_kinds: list[str]
    event_names: list[str | None]
    final: str


class GoldenCase(BaseModel):
    """一条可录制/回放的 golden session。"""

    version: str = "1"
    name: str
    description: str = ""
    input: str
    steps: list[GoldenStep]
    tools: list[GoldenTool] = Field(default_factory=list)
    policy_rules: list[GoldenPolicyRule] = Field(default_factory=list)
    expected: GoldenExpected | None = None
