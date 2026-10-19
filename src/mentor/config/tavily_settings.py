"""Tavily 搜索配置：API Key。"""

from mentor.config.base_settings import BaseSettingsWithEnv


class TavilySettings(BaseSettingsWithEnv):
    api_key: str = ""

    model_config = {"env_prefix": "TAVILY_"}


tavily_settings = TavilySettings()
