"""OpenAI 兼容模型配置：API Key、Base URL 与默认模型。"""

from mentor.config.base_settings import BaseSettingsWithEnv


class OpenAISettings(BaseSettingsWithEnv):
    # 上游模型密钥；实际请求由 LiteLLM 网关转发。
    api_key: str = ""
    # OpenAI 兼容接口地址，例如 LiteLLM 的 http://localhost:4000/v1。
    base_url: str = ""
    # 未在 AgentSpec 中指定模型时使用的默认模型名。
    default_model: str = ""

    model_config = {"env_prefix": "OPENAI_"}


openai_settings = OpenAISettings()
