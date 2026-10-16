from langgraph.config import get_config


def get_thread_id():
    config = get_config()
    if not config:
        return None
    return config.get("configurable", {}).get("thread_id", None)


def get_user_id() -> str:
    config = get_config()
    user_id = config.get("configurable", {}).get("langgraph_auth_user_id", None)
    if not user_id:
        raise RuntimeError("无法通过langgraph_auth_user_id获取当前认证用户的ID")
    return user_id
