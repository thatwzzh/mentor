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

    model_config = {"env_prefix": "MINIO_"}


minio_settings = MinioSettings()
