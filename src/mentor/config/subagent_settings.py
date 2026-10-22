"""异步子代理默认策略：深度、超时；工具默认只读。"""

from pydantic import Field

from .base_settings import BaseSettingsWithEnv


class SubAgentSettings(BaseSettingsWithEnv):
    max_depth: int = Field(default=1, ge=1, le=3)
    timeout_seconds: float = Field(default=180, gt=0)

    model_config = {"env_prefix": "SUBAGENT_"}


subagent_settings = SubAgentSettings()
