"""工具执行配置：大输出落盘目录与默认截断阈值。"""

from pydantic import Field

from .base_settings import BaseSettingsWithEnv


class ToolSettings(BaseSettingsWithEnv):
    # 完整工具输出落盘目录；生产建议挂持久卷并限制权限。
    artifact_dir: str = "/tmp/mentor-tool-artifacts"
    # 单个工具输出默认上限；ToolSpec 可逐工具覆盖。
    max_output_chars: int = Field(default=8_000, gt=0)

    model_config = {"env_prefix": "TOOL_"}


tool_settings = ToolSettings()
