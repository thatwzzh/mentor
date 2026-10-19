from pydantic import field_validator

from .base_settings import BaseSettingsWithEnv


class NacosSettings(BaseSettingsWithEnv):
    server_address: str = ""
    # 沙箱内访问 Nacos 的地址。宿主机地址在沙箱里不可达。
    server_address_in_sandbox: str = ""
    namespace_id: str = ""
    username: str = ""
    password: str = ""
    ttl: int = 0

    model_config = {"env_prefix": "NACOS_"}

    @field_validator("server_address", "server_address_in_sandbox")
    @classmethod
    def remove_trailing_slashes(cls, value: str) -> str:
        return value.rstrip("/")  # 去掉末尾斜杠


nacos_settings = NacosSettings()
