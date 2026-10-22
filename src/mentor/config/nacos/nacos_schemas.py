"""Nacos 侧数据结构：Agent 模型、MCP、AgentSpec 与沙箱配置。"""

from typing import Literal

from pydantic import BaseModel, Field
from mentor.config.openai_settings import openai_settings


class AgentModelConfig(BaseModel):
    name: str = Field(default=openai_settings.default_model)
    thinking: bool = Field(default=True)
    temperature: float | None = Field(default=None)
    top_p: float | None = Field(default=None)
    # 能力档位，对应 AgentSpec.sandbox_profiles 的 key；不要按模型名 fork Backend。
    profile: str = Field(default="balanced")


class MCPServerConfig(BaseModel):
    name: str
    url: str
    disabled_tools: list[str] = Field(default_factory=list)
    # 认证头原值，例如 "Bearer xxx"；生产应从密钥管理注入，不要提交到仓库。
    auth: str | None = None
    # 单次 MCP 工具调用超时；0 表示交给执行器默认值。
    timeout_seconds: float = 30
    # 非空时只暴露这些工具（未加 server 前缀的原始名）。
    allowed_tools: list[str] = Field(default_factory=list)
    # MCP 默认 deferred，避免一次把大量工具塞进 prompt。
    exposure: Literal["direct", "deferred", "hidden"] = "deferred"
    risk: Literal["read", "write", "external", "destructive"] = "external"


class SandboxProfile(BaseModel):
    """按模型能力档位配置沙箱；字段缺省时回落到 sandbox_config。"""

    image: str | None = None
    cpu: str | None = None
    memory_mb: int | None = Field(default=None, gt=0)
    timeout_seconds: int = Field(default=600, gt=0)
    ready_timeout_seconds: int = Field(default=180, gt=0)
    command_timeout_seconds: int = Field(default=90, gt=0)
    network_default_action: Literal["allow", "deny"] | None = None
    network_allow: list[str] = Field(default_factory=list)
    max_steps: int = Field(default=30, gt=0)
    max_tool_calls: int = Field(default=20, gt=0)
    max_parallel_tools: int = Field(default=1, gt=0)
    max_output_chars: int = Field(default=8_000, gt=0)


class AgentSpec(BaseModel):
    name: str
    description: str = Field(default="")
    system_prompt: str = Field(default="")
    model: AgentModelConfig = Field(default_factory=AgentModelConfig)
    # key 是 model.profile；未声明时回落到 sandbox_config。
    sandbox_profiles: dict[str, SandboxProfile] = Field(default_factory=dict)
    mcps: dict[str, MCPServerConfig] = Field(default_factory=dict)
    tools: list[str] = Field(default_factory=list)
    subagents: list[str] = Field(default_factory=list)
    link_prompts: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)


class OpenSandboxConfig(BaseModel):
    image: str = Field(default="python:3.12-slim", min_length=1)
    timeout_seconds: int = Field(default=600, gt=0)
    ready_timeout_seconds: int = Field(default=180, gt=0)
    command_timeout_seconds: int = Field(default=90, gt=0)
    # 容器资源配额；留空表示使用 OpenSandbox/宿主默认值。
    cpu: str | None = None
    memory: str | None = None
    # 出口网络策略；未配置时不传 NetworkPolicy，保持 OpenSandbox 默认行为。
    network_default_action: Literal["allow", "deny"] | None = None
    network_allow: list[str] = Field(default_factory=list)
