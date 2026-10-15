from langchain.chat_models import init_chat_model
from mentor.config.openai_settings import openai_settings


def create_model(**kwargs):
    kwargs.pop("model_provider", None)  # 删除 model_provider 字段
    thinking = kwargs.get("thinking", True)
    kwargs.pop("thinking", None)
    config = {
        "model": openai_settings.default_model,
        "model_provider": "openai_reasoning",
        "extra_body": {"thinking": {"type": "enabled" if thinking else "disabled"}},
    }
    config.update(kwargs)

    model = init_chat_model(**config)
    return model
