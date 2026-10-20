"""Langfuse 配置：密钥、地址、Webhook 签名与 Trace 保留天数。"""

from mentor.config.base_settings import BaseSettingsWithEnv


class LangfuseSettings(BaseSettingsWithEnv):
    # Langfuse 项目密钥，用于 SDK 上报与 API 调用。
    secret_key: str = ""
    public_key: str = ""
    # Langfuse Web/API 地址，例如 http://localhost:3000。
    base_url: str = ""
    # 实验 Webhook 与告警 Webhook 使用独立签名密钥，留空时路由拒绝请求。
    experiment_webhook_secret: str = ""
    alert_webhook_secret: str = ""
    # Trace 保留天数；None 时清理函数按 30 天兜底。
    trace_retention_days: int | None = None

    model_config = {"env_prefix": "LANGFUSE_"}


langfuse_settings = LangfuseSettings()
