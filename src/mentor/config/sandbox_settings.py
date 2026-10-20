"""OpenSandbox 连接配置：API Key 与访问域名。"""

from .base_settings import BaseSettingsWithEnv


class SandboxSettings(BaseSettingsWithEnv):
    # OpenSandbox Server 的 API Key，对应 OPEN_SANDBOX_API_KEY。
    api_key: str = ""
    # OpenSandbox Server 地址，不含协议；沙箱内访问需另行配置。
    domain: str = ""
    model_config = {"env_prefix": "OPEN_SANDBOX_"}


sandbox_settings = SandboxSettings()
