from pydantic import field_validator

from .base_settings import BaseSettingsWithEnv


class NacosSettings(BaseSettingsWithEnv):
    server_address: str = ""
    namespace_id: str = ""
    username: str = ""
    password: str = ""
    ttl: int = 0

    model_config = {"env_prefix": "NACOS_"}

    @field_validator("server_address")
    @classmethod
    def remove_trailing_slashes(cls, value: str) -> str:
        return value.rstrip("/")  # 去掉末尾斜杠


nacos_settings = NacosSettings()
