"""OpenSandbox 连接配置：API Key 与访问域名。"""

from .base_settings import BaseSettingsWithEnv


class SandboxSettings(BaseSettingsWithEnv):
    api_key: str = ""
    domain: str = ""
    model_config = {"env_prefix": "OPEN_SANDBOX_"}


sandbox_settings = SandboxSettings()
