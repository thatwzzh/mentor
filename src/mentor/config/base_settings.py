from pydantic_settings import BaseSettings


class BaseSettingsWithEnv(BaseSettings):
    model_config = {"env_file": ".env", "extra": "ignore"}
