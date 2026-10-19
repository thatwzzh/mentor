from mentor.utils.trace_wrapper import trace_wrapper
from mentor.agents.agent_factory import AgentFactory


class WebAgentFactory(AgentFactory):
    @property
    def name(self) -> str:
        return "web_agent"


web_agent_factory = WebAgentFactory()

factory = web_agent_factory.factory

factory_with_trace = trace_wrapper(factory)
