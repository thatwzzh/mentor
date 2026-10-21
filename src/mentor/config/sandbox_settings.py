"""OpenSandbox 连接与生命周期配置：API Key、域名、PVC 租户与空闲回收。"""

import re

from pydantic import Field, field_validator

from .base_settings import BaseSettingsWithEnv


class SandboxSettings(BaseSettingsWithEnv):
    # OpenSandbox Server 的 API Key，对应 OPEN_SANDBOX_API_KEY。
    api_key: str = ""
    # OpenSandbox Server 地址，不含协议；沙箱内访问需另行配置。
    domain: str = ""
    # PVC 名称前缀；最终名称是 "<tenant>-<sha256(user_id)[:12]>"。
    tenant: str = "default"
    # 仅本地脚本/测试可显式开启；生产默认禁止 InMemoryStore 回落。
    allow_memory_store: bool = False
    # 当前进程缓存的空闲沙箱回收时间；0 表示不主动回收，交给 OpenSandbox TTL。
    idle_timeout_seconds: int = Field(default=1800, ge=0)

    model_config = {"env_prefix": "OPEN_SANDBOX_"}

    @field_validator("tenant")
    @classmethod
    def normalize_tenant(cls, value: str) -> str:
        # PVC 名必须符合 DNS-1123；统一小写并把非法字符替换为 "-"。
        cleaned = re.sub(r"[^a-z0-9-]", "-", value.lower()).strip("-")
        return cleaned or "default"


sandbox_settings = SandboxSettings()
