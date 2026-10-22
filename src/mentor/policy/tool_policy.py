"""工具策略：YAML 规则 → allow / prompt / deny。

规则按顺序匹配工具名（glob）与参数 JSON（正则）；第一条命中生效。
未命中时按 ToolSpec.risk 的默认决策处理，高风险默认 prompt。
"""

import fnmatch
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator

from mentor.config.base_settings import BaseSettingsWithEnv
from mentor.tools.specs import ToolRisk

Decision = Literal["allow", "prompt", "deny"]


class ToolPolicySettings(BaseSettingsWithEnv):
    """策略文件与总开关；TOOL_POLICY_ENABLED=false 可整体回退。"""

    file: str = "config/tool_policy.yaml"
    enabled: bool = True

    model_config = {"env_prefix": "TOOL_POLICY_"}


class PolicyRule(BaseModel):
    """一条策略规则；tool 支持 glob，pattern 是参数 JSON 的正则。"""

    tool: str = "*"
    pattern: str | None = None
    decision: Decision
    reason: str = ""

    @field_validator("pattern")
    @classmethod
    def _valid_regex(cls, value: str | None) -> str | None:
        if value:
            re.compile(value)
        return value


class ToolPolicyConfig(BaseModel):
    """策略文件结构。"""

    enabled: bool = True
    max_prompts_per_run: int = Field(default=3, ge=0)
    defaults: dict[ToolRisk, Decision] = Field(
        default_factory=lambda: {
            "read": "allow",
            "write": "allow",
            "external": "allow",
            "destructive": "prompt",
        }
    )
    rules: list[PolicyRule] = Field(default_factory=list)


@dataclass(frozen=True)
class PolicyDecision:
    """一次策略判定；reason 进入审批审计。"""

    decision: Decision
    reason: str = ""
    rule: str = "default"


_DEFAULT_RULES = [
    PolicyRule(
        tool="execute",
        pattern=r'rm\s+-rf\s+/(?=[\s"]|$)',
        decision="deny",
        reason="禁止递归删除根目录",
    ),
    PolicyRule(
        tool="execute",
        pattern=r"rm\s+-rf",
        decision="prompt",
        reason="递归删除需要人工确认",
    ),
    PolicyRule(
        tool="deploy",
        decision="prompt",
        reason="deploy 会写入产物桶",
    ),
]


class ToolPolicy:
    """策略判定器；不依赖 LangGraph，可单测。"""

    def __init__(self, config: ToolPolicyConfig) -> None:
        self.config = config

    @classmethod
    def load(cls, path: str | Path | None = None) -> "ToolPolicy":
        settings = ToolPolicySettings()
        if not settings.enabled:
            return cls(ToolPolicyConfig(enabled=False))
        target = Path(path or settings.file)
        if target.is_file():
            payload = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
            if not isinstance(payload, dict):
                raise ValueError("tool policy 必须是 YAML 对象")
            return cls(ToolPolicyConfig.model_validate(payload))
        return cls(ToolPolicyConfig(rules=_DEFAULT_RULES))

    def decide(
        self, tool: str, args: dict, risk: ToolRisk, default: Decision = "prompt"
    ) -> PolicyDecision:
        if not self.config.enabled:
            return PolicyDecision("allow", reason="policy disabled")
        args_text = json.dumps(args, ensure_ascii=False, sort_keys=True, default=str)
        for index, rule in enumerate(self.config.rules):
            if not fnmatch.fnmatchcase(tool, rule.tool):
                continue
            if rule.pattern and not re.search(
                rule.pattern, args_text, flags=re.IGNORECASE
            ):
                continue
            return PolicyDecision(
                rule.decision,
                reason=rule.reason or f"rule {index}",
                rule=f"rule:{index}",
            )
        decision = self.config.defaults.get(risk, default)
        return PolicyDecision(
            decision,
            reason=f"默认风险策略: {risk}",
            rule="default",
        )

    @property
    def max_prompts_per_run(self) -> int:
        return self.config.max_prompts_per_run


_policy: ToolPolicy | None = None


def get_tool_policy() -> ToolPolicy:
    global _policy
    if _policy is None:
        _policy = ToolPolicy.load()
    return _policy
