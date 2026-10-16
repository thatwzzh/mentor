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
