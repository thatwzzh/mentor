from pydantic import BaseModel, Field
from mentor.config.openai_settings import openai_settings


class AgentModelConfig(BaseModel):
    name: str = Field(default=openai_settings.default_model)
    thinking: bool = Field(default=True)
    temperature: float | None = Field(default=None)
    top_p: float | None = Field(default=None)


class MCPServerConfig(BaseModel):
    name: str
    url: str
    disabled_tools: list[str] = Field(default_factory=list)


class AgentSpec(BaseModel):
    name: str
    description: str = Field(default="")
    system_prompt: str = Field(default="")
    model: AgentModelConfig = Field(default_factory=AgentModelConfig)
    skills: list[str] = Field(default_factory=list)
    mcps: dict[str, MCPServerConfig] = Field(default_factory=dict)
    tools: list[str] = Field(default_factory=list)
    subagents: list[str] = Field(default_factory=list)
    link_prompts: list[str] = Field(default_factory=list)


class OpenSandboxConfig(BaseModel):
    image: str = Field(default="python:3.12-slim", min_length=1)
    timeout_seconds: int = Field(default=600, gt=0)
    ready_timeout_seconds: int = Field(default=180, gt=0)
    command_timeout_seconds: int = Field(default=90, gt=0)
