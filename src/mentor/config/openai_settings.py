"""OpenAI 兼容模型配置：API Key、Base URL 与默认模型。"""

from mentor.config.base_settings import BaseSettingsWithEnv


class OpenAISettings(BaseSettingsWithEnv):
    api_key: str = ""
    base_url: str = ""
    default_model: str = ""

    model_config = {"env_prefix": "OPENAI_"}


openai_settings = OpenAISettings()
