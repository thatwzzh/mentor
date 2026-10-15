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
