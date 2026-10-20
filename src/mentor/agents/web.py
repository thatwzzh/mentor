"""Web 子 Agent 定义：复用通用工厂，作为主 Agent 的可委派子图。"""

from mentor.utils.trace_wrapper import trace_wrapper
from mentor.agents.agent_factory import AgentFactory


class WebAgentFactory(AgentFactory):
    @property
    def name(self) -> str:
        # 与主 Agent 共用装配逻辑，仅 Agent 名不同；Nacos 中需有同名 agentspec。
        return "web_agent"


web_agent_factory = WebAgentFactory()

# 子 Agent 也可能被主 Agent 递归调用，因此同样在 factory 外包一层 trace。
factory = web_agent_factory.factory

factory_with_trace = trace_wrapper(factory)
