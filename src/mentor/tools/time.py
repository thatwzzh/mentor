"""获取当前时间的工具，返回带时区的 ISO 8601 字符串。"""

from datetime import datetime, timedelta, timezone

from langchain.tools import tool

from mentor.tools.specs import register_tool

# 北京时间固定 UTC+8，1991 年后无夏令时；用固定偏移表达，避免依赖镜像里的 tzdata，
# 也不受容器 TZ 与宿主机时区影响。
BEIJING_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")


@register_tool(risk="read", timeout_s=5)
@tool
def get_current_time():
    """
    获取当前的日期和时间，返回当前时刻（北京时区）
    所有涉及到最新、最近等和时间相关的信息获取都应该优先调用此工具
    """
    return datetime.now(BEIJING_TZ).isoformat()
