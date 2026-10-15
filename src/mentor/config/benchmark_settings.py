from mentor.config.base_settings import BaseSettingsWithEnv


class BenchMarkSettings(BaseSettingsWithEnv):
    agent_server: str = ""
    user_id: str = ""
    graph_id: str = ""
    model_config = {"env_prefix": "BENCHMARK_"}


benchmark_settings = BenchMarkSettings()
