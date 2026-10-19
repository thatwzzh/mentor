"""Langfuse 配置：密钥、地址、Webhook 签名与 Trace 保留天数。"""

from mentor.config.base_settings import BaseSettingsWithEnv


class LangfuseSettings(BaseSettingsWithEnv):
    secret_key: str = ""
    public_key: str = ""
    base_url: str = ""
    experiment_webhook_secret: str = ""
    alert_webhook_secret: str = ""
    trace_retention_days: int | None = None

    model_config = {"env_prefix": "LANGFUSE_"}


langfuse_settings = LangfuseSettings()
