from langgraph.config import get_config


def get_thread_id():
    config = get_config()
    if not config:
        return None
    return config.get("configurable", {}).get("thread_id", None)
