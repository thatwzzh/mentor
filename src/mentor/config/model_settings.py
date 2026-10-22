"""模型请求侧配置：显式超时与客户端重试边界。

LiteLLM 已负责 provider 重试/fallback/熔断；Agent 侧默认 max_retries=0，
避免和网关重复重试放大请求。
"""

from pydantic import Field

from .base_settings import BaseSettingsWithEnv


class ModelSettings(BaseSettingsWithEnv):
    request_timeout_seconds: int = Field(default=120, gt=0)
    # 0 表示不在 Agent 侧重试，交给 LiteLLM。
    max_retries: int = Field(default=0, ge=0, le=5)

    model_config = {"env_prefix": "MODEL_"}


model_settings = ModelSettings()
