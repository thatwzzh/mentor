"""Run 事件运行时上下文：统一读取 run_id/thread_id/user_id。"""

import uuid

from langgraph.config import get_config

from mentor.utils.shared import get_thread_id, get_user_id


def runtime_ids() -> tuple[str, str, str]:
    """读取 run_id/thread_id/user_id；脱离图时返回可用的本地兜底。"""
    try:
        config = get_config() or {}
    except RuntimeError:
        config = {}
    configurable = config.get("configurable", {}) or {}
    run_id = str(
        config.get("run_id")
        or configurable.get("run_id")
        or configurable.get("thread_id")
        or uuid.uuid4().hex
    )
    thread_id = ""
    try:
        thread_id = str(get_thread_id() or "")
    except RuntimeError:
        pass
    try:
        user_id = get_user_id()
    except RuntimeError:
        user_id = "anonymous"
    return run_id, thread_id, user_id
