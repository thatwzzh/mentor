from .base_settings import BaseSettingsWithEnv


class SandboxSettings(BaseSettingsWithEnv):
    api_key: str = ""
    domain: str = ""
    model_config = {"env_prefix": "OPEN_SANDBOX_"}


sandbox_settings = SandboxSettings()
