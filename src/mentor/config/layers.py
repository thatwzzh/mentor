"""Agent 配置的四层合并：defaults < .env < Nacos < request override。

- defaults：Pydantic 字段默认值，保证最小可运行配置。
- .env：BaseSettingsWithEnv 读取的静态连接信息与默认模型。
- Nacos：get_agent_spec 返回的 AgentSpec（Prompt/模型/MCP/工具/Skill）。
- request override：本次 run 通过 configurable.agent_spec_override 传入的临时覆盖，
  只允许覆盖声明过的字段，避免拼写错误静默进入模型。

override 不持久化，也不改变 Nacos；AgentFactory 会用合并后的 AgentSpec 计算
spec_hash，因此一次临时覆盖只会生成一个新缓存版本。
"""

from typing import Any

from pydantic import BaseModel, ConfigDict

from mentor.config.nacos.nacos_schemas import (
    AgentModelConfig,
    AgentSpec,
    MCPServerConfig,
)
from mentor.config.nacos_settings import nacos_settings


class AgentSpecOverride(BaseModel):
    """request override 的白名单字段；extra=forbid 让拼写错误直接暴露。"""

    model_config = ConfigDict(extra="forbid")

    description: str | None = None
    system_prompt: str | None = None
    model: AgentModelConfig | None = None
    mcps: dict[str, MCPServerConfig] | None = None
    tools: list[str] | None = None
    subagents: list[str] | None = None
    skills: list[str] | None = None


def merge_agent_spec(
    base: AgentSpec, override: dict[str, Any] | AgentSpecOverride | None
) -> AgentSpec:
    """把 request override 合并到 Nacos 规格上，返回新的不可变结果。"""
    if override is None:
        return base
    if isinstance(override, dict):
        override = AgentSpecOverride.model_validate(override)

    data = base.model_dump()
    update = override.model_dump(exclude_none=True)
    if override.model is not None:
        # 只覆盖显式传入的模型字段，避免用默认 name 覆盖 Nacos 中的模型名。
        model_update = override.model.model_dump(
            include=override.model.model_fields_set, exclude_none=True
        )
        update["model"] = {**base.model.model_dump(), **model_update}
    data.update(update)
    return AgentSpec.model_validate(data)


def resolve_label(label: str | None = None) -> str:
    """解析 Nacos label；未传时使用 NACOS_LABEL，默认 production。"""
    value = label or nacos_settings.label
    if value not in {"staging", "production"}:
        raise ValueError(f"不支持的 Nacos label: {value!r}")
    return value
