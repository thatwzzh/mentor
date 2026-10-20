"""主 Agent 定义：在通用工厂上挂载主 Agent 的 state 与中间件。"""

from collections.abc import Sequence
from typing import Any
from langchain.agents.middleware import AgentMiddleware, AgentState
from deepagents import DeepAgentState

from mentor.utils.trace_wrapper import trace_wrapper
from mentor.agents.main_agent_state import MainAgentState
from mentor.middlewares.turn_summary import turn_summary
from mentor.agents.agent_factory import AgentFactory


class MainAgentFactory(AgentFactory):
    @property
    def name(self) -> str:
        return "main_agent"

    def get_state_schema(self) -> type[DeepAgentState] | None:
        # 主 Agent 在 DeepAgentState 上多一个 turn_summary 字段，供前端按回合渲染。
        return MainAgentState

    def get_middlewares(self) -> Sequence[AgentMiddleware[AgentState[Any], None, Any]]:
        # 只挂回合摘要；压缩、工具等能力由 DeepAgents 默认中间件负责。
        return [turn_summary]


main_agent_factory = MainAgentFactory()

# factory 只负责按 Nacos 规格装配图；factory_with_trace 在外层挂 Langfuse CallbackHandler。
factory = main_agent_factory.factory

factory_with_trace = trace_wrapper(factory)
