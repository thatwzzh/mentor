"""配置基类：统一 .env 加载与未知字段忽略策略。"""

from pydantic_settings import BaseSettings


class BaseSettingsWithEnv(BaseSettings):
    # env_file 相对于进程工作目录；用 `langgraph dev` 启动时应位于仓库根目录。
    model_config = {"env_file": ".env", "extra": "ignore"}
