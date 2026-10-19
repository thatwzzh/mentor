"""评测配置：Agent 服务地址、评测用户与图 ID。"""

from mentor.config.base_settings import BaseSettingsWithEnv


class BenchMarkSettings(BaseSettingsWithEnv):
    agent_server: str = ""
    user_id: str = ""
    graph_id: str = ""
    model_config = {"env_prefix": "BENCHMARK_"}


benchmark_settings = BenchMarkSettings()
