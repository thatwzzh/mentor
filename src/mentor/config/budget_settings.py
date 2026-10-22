"""单 run 预算配置；0 表示不限制。"""

from pydantic import Field

from .base_settings import BaseSettingsWithEnv


class BudgetSettings(BaseSettingsWithEnv):
    max_tokens: int = Field(default=0, ge=0)
    max_seconds: float = Field(default=0, ge=0)
    max_output_tokens: int = Field(default=4096, gt=0)

    model_config = {"env_prefix": "RUN_BUDGET_"}


budget_settings = BudgetSettings()
