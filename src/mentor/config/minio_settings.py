"""MinIO 配置：访问密钥、宿主机/沙箱内地址与 HTTPS 开关。"""

from pydantic import Field

from mentor.config.base_settings import BaseSettingsWithEnv


class MinioSettings(BaseSettingsWithEnv):
    # MinIO 访问密钥。
    access_key: str = ""
    # MinIO 秘密密钥。
    secret_key: str = ""
    # MinIO API 地址，不包含协议前缀。
    endpoint: str = "localhost:9002"
    # 是否使用 HTTPS 连接。
    secure: bool = False
    # 沙箱内上传地址
    endpoint_in_sandbox: str = ""
    # 产物预签名 URL 的有效期，1 秒至 7 天。
    presign_expire_seconds: int = Field(default=3600, ge=1, le=604800)
    model_config = {"env_prefix": "MINIO_"}


minio_settings = MinioSettings()
