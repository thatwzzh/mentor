"""Tavily 搜索配置：API Key。"""

from mentor.config.base_settings import BaseSettingsWithEnv


class TavilySettings(BaseSettingsWithEnv):
    # web_search 与 fetch_url 共用同一个 Tavily Key。
    api_key: str = ""

    model_config = {"env_prefix": "TAVILY_"}


tavily_settings = TavilySettings()
