from langchain.chat_models import init_chat_model
from mentor.config.openai_settings import openai_settings
from .shared import get_thread_id


def create_model(**kwargs):
    kwargs.pop("model_provider", None)  # 删除 model_provider 字段
    thinking = kwargs.get("thinking", True)
    kwargs.pop("thinking", None)
    kwargs.pop("extra_body", None)
    config = {
        "model": openai_settings.default_model,
        "model_provider": "openai_reasoning",
        "extra_body": {"enable_thinking": thinking},
    }

    thread_id = get_thread_id()
    if thread_id:
        config["default_headers"] = {"x-litellm-session-id": thread_id}

    config.update(kwargs)

    model = init_chat_model(**config)
    return model
