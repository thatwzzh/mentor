"""Nacos 连接配置：地址、命名空间、账号与缓存 TTL。"""

from pydantic import field_validator

from .base_settings import BaseSettingsWithEnv


class NacosSettings(BaseSettingsWithEnv):
    server_address: str = ""
    # 沙箱内访问 Nacos 的地址。宿主机地址在沙箱里不可达。
    server_address_in_sandbox: str = ""
    namespace_id: str = ""
    username: str = ""
    password: str = ""
    # 本地缓存秒数；0 表示每次调用都视为过期（lazy 模式下会持续触发后台刷新）。
    ttl: int = 0

    model_config = {"env_prefix": "NACOS_"}

    @field_validator("server_address", "server_address_in_sandbox")
    @classmethod
    def remove_trailing_slashes(cls, value: str) -> str:
        return value.rstrip("/")  # 去掉末尾斜杠


nacos_settings = NacosSettings()
