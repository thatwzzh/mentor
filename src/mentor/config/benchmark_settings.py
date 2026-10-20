"""评测配置：Agent 服务地址、评测用户与图 ID。"""

from mentor.config.base_settings import BaseSettingsWithEnv


class BenchMarkSettings(BaseSettingsWithEnv):
    # LangGraph Agent 服务地址，评测任务通过它发起 runs.wait。
    agent_server: str = ""
    # 评测专用 Bearer token；MOCK_USER 模式下通常填某个测试用户 ID。
    user_id: str = ""
    # 被评测的图 ID，对应 langgraph.json 中的 graph 名称。
    graph_id: str = ""
    model_config = {"env_prefix": "BENCHMARK_"}


benchmark_settings = BenchMarkSettings()
