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
    # 回合结束异步抽取候选；可关闭以省模型调用。
    auto_extract: bool = True
    extract_min_chars: int = Field(default=40, ge=0)
    max_candidates: int = Field(default=5, ge=1, le=20)
    # 空字符串表示不启用 embedding，走 lexical fallback。
    embedding_model: str = ""
    embedding_dim: int = Field(default=1536, gt=0)

    model_config = {"env_prefix": "MEMORY_"}


memory_settings = MemorySettings()
