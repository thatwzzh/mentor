"""模型工厂：按 Agent 配置创建 LiteLLM/OpenAI 兼容模型，并注入会话头。"""

from langchain.chat_models import init_chat_model
from mentor.utils.shared import get_thread_id
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
    }
    if model_config.temperature is not None:
        config["temperature"] = model_config.temperature
    if model_config.top_p is not None:
        config["top_p"] = model_config.top_p

    thread_id = get_thread_id()
    if thread_id:
        # 让 LiteLLM 按会话做路由/计费；同名 header 也会出现在网关日志里。
        config["default_headers"] = {"x-litellm-session-id": thread_id}

    model = init_chat_model(**config)
    return model
