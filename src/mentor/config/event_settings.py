"""会话投影配置：事件库、保留轮数、工具结果修剪阈值与历史摘要上限。"""

from pydantic import Field

from .base_settings import BaseSettingsWithEnv


class EventSettings(BaseSettingsWithEnv):
    # Postgres 连接串；默认对应 docker-compose 的 mentor-postgres。
    dsn: str = "postgresql://mentor:mentor@127.0.0.1:5433/mentor"
    # Postgres 写入失败时的事件落盘目录，供 mentor events replay 重放。
    outbox_dir: str = "/tmp/mentor-run-events-outbox"
    # checkpoint 中保留最近多少轮完整对话。
    keep_last_turns: int = Field(default=10, gt=0)
    # 保留轮次中的工具输出最多保留多少字符；完整内容去事件表。
    max_tool_chars: int = Field(default=500, gt=0)
    # history_summary 最大字符数，避免摘要本身无限增长。
    summary_max_chars: int = Field(default=4_000, gt=0)

    model_config = {"env_prefix": "RUN_EVENTS_"}


event_settings = EventSettings()
