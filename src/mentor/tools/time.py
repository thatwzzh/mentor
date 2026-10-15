from datetime import datetime, timezone

from langchain.tools import tool


@tool
def get_current_time():
    """
    获取当前的日期和时间，返回当前时刻（北京时区）
    所有涉及到最新、最近等和时间相关的信息获取都应该优先调用此工具
    """
    now = datetime.now(timezone.utc).astimezone()
    return now.isoformat()
