"""获取当前时间的工具，返回带时区的 ISO 8601 字符串。"""

from datetime import datetime, timezone

from langchain.tools import tool


@tool
def get_current_time():
    """
    获取当前的日期和时间，返回当前时刻（北京时区）
    所有涉及到最新、最近等和时间相关的信息获取都应该优先调用此工具
    """
    # astimezone() 无参数时使用进程本地时区；容器内需设置 TZ=Asia/Shanghai，
    # 否则返回的是容器时区而不是北京时间。
    now = datetime.now(timezone.utc).astimezone()
    return now.isoformat()
