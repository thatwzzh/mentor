"""记忆配置：短记忆注入预算、长记忆检索参数。"""

from pydantic import Field

from .base_settings import BaseSettingsWithEnv


class MemorySettings(BaseSettingsWithEnv):
    enabled: bool = True
    # 当前身份体系没有 tenant claim，先用配置值；多租户时替换为 auth claim。
    tenant: str = "default"
    max_inject_tokens: int = Field(default=800, gt=0)
    search_limit: int = Field(default=5, ge=1, le=20)
    min_confidence: float = Field(default=0.3, ge=0, le=1)

    model_config = {"env_prefix": "MEMORY_"}


memory_settings = MemorySettings()
