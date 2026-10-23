"""运行时上下文读取：从 LangGraph config 中取 thread_id 与认证用户 ID。"""

from langgraph.config import get_config


def get_thread_id():
    # get_config() 依赖 LangGraph 运行时上下文；脱离图调用会抛 RuntimeError。
    config = get_config()
    if not config:
        return None
    return config.get("configurable", {}).get("thread_id", None)


def get_user_id() -> str:
    # 认证用户 ID 由 LangGraph 平台注入；缺失时 fail-fast，避免把空用户当成合法租户。
    config = get_config()
    user_id = config.get("configurable", {}).get("langgraph_auth_user_id", None)
    if not user_id:
        raise RuntimeError("无法通过langgraph_auth_user_id获取当前认证用户的ID")
    return user_id


def get_tenant_id() -> str:
    """从 auth 注入的 user 取 tenant；缺失时回落 MEMORY_TENANT。"""
    from mentor.config.memory_settings import memory_settings

    try:
        config = get_config()
    except RuntimeError:
        return memory_settings.tenant
    auth_user = (config.get("configurable", {}) or {}).get("langgraph_auth_user")
    if isinstance(auth_user, dict):
        return str(auth_user.get("tenant_id") or memory_settings.tenant)
    return str(
        getattr(auth_user, "tenant_id", None) or memory_settings.tenant
    )
