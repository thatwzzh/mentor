"""模型请求侧配置：只保留客户端超时保护。

重试、fallback、熔断由 LiteLLM 负责；Agent 侧在 create_model 中固定
max_retries=0，避免和网关重复重试放大请求。
"""

from pydantic import Field

from .base_settings import BaseSettingsWithEnv


class ModelSettings(BaseSettingsWithEnv):
    request_timeout_seconds: int = Field(default=120, gt=0)

    model_config = {"env_prefix": "MODEL_"}


model_settings = ModelSettings()
