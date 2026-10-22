"""模型工厂：按 Agent 配置创建 LiteLLM/OpenAI 兼容模型。

x-litellm-session-id 不在这里写死：图会被 AgentFactory 缓存，header 由
ReasoningChatModel 在每次请求时从 session_context 读取并注入。
"""

from langchain.chat_models import init_chat_model

from mentor.config.model_settings import model_settings
from mentor.config.nacos.nacos_schemas import AgentModelConfig


def create_model(model_config: AgentModelConfig | None = None):
    if model_config is None:
        model_config = AgentModelConfig()
    config = {
        "model": model_config.name,
        # openai_reasoning 由 reasoning_chat_model.py 注册，额外提取 reasoning_content。
        "model_provider": "openai_reasoning",
        # enable_thinking 是部分 OpenAI 兼容服务（如百炼）的扩展字段。
        "extra_body": {"enable_thinking": model_config.thinking},
        # LiteLLM 负责 provider 重试/fallback；Agent 侧固定不重试，避免放大。
        "request_timeout": model_settings.request_timeout_seconds,
        "max_retries": 0,
    }
    if model_config.temperature is not None:
        config["temperature"] = model_config.temperature
    if model_config.top_p is not None:
        config["top_p"] = model_config.top_p

    model = init_chat_model(**config)
    return model
