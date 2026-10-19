"""Web 子 Agent 定义：复用通用工厂，作为主 Agent 的可委派子图。"""

from mentor.utils.trace_wrapper import trace_wrapper
from mentor.agents.agent_factory import AgentFactory


class WebAgentFactory(AgentFactory):
    @property
    def name(self) -> str:
        return "web_agent"


web_agent_factory = WebAgentFactory()

factory = web_agent_factory.factory

factory_with_trace = trace_wrapper(factory)
