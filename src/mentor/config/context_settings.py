"""上下文与压缩配置：按模型上下文显式设置 trigger/keep，不使用默认值。"""

from pydantic import Field

from .base_settings import BaseSettingsWithEnv


class ContextSettings(BaseSettingsWithEnv):
    # 模型未知 profile 时的上下文窗口回退值。
    window_tokens: int = Field(default=128_000, gt=0)
    # 触发压缩的窗口比例；默认 80%。
    trigger_ratio: float = Field(default=0.8, gt=0.0, le=0.95)
    # 回答结束后异步预压缩的水位；默认 60%，为 80% 自动压缩留余量。
    precompact_ratio: float = Field(default=0.60, gt=0.0, lt=1.0)
    # 压缩后保留的最近消息数。
    keep_messages: int = Field(default=20, gt=0)
    # 送给摘要模型的历史 token 上限。
    trim_tokens_to_summarize: int = Field(default=4_000, gt=0)
    # 是否自动压缩；关闭后只保留 /compact 手动触发。
    auto_compact: bool = True

    model_config = {"env_prefix": "CONTEXT_"}


context_settings = ContextSettings()
