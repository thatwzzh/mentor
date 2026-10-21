"""主 Agent 定义：在通用工厂上挂载主 Agent 的 state 与中间件。"""

from collections.abc import Sequence
from typing import Any, cast
from langchain.agents.middleware import AgentMiddleware
from deepagents import DeepAgentState

from mentor.utils.trace_wrapper import trace_wrapper
from mentor.agents.main_agent_state import MainAgentState
from mentor.middlewares.run_projection import run_projection
from mentor.agents.agent_factory import AgentFactory


class MainAgentFactory(AgentFactory):
    @property
    def name(self) -> str:
        return "main_agent"

    def get_state_schema(self) -> type[DeepAgentState] | None:
        # 主 Agent 在 DeepAgentState 上增加历史摘要与产物列表；UI 摘要走 run_events。
        return MainAgentState

    def get_middlewares(self) -> Sequence[AgentMiddleware]:
        # run_projection 写事件表并修剪 checkpoint；工具/沙箱中间件由基类追加。
        return [cast(AgentMiddleware, run_projection)]


main_agent_factory = MainAgentFactory()

# factory 只负责按 Nacos 规格装配图；factory_with_trace 在外层挂 Langfuse CallbackHandler。
factory = main_agent_factory.factory

factory_with_trace = trace_wrapper(factory)
