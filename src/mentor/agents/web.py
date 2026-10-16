from collections.abc import Sequence
from typing import Any

from langchain.agents import create_agent
from langchain.tools import BaseTool
from langchain_core.runnables import Runnable
from mentor.utils.trace_wrapper import trace_wrapper
from mentor.agents.agent_factory import AgentFactory
from deepagents.middleware import SubAgentMiddleware, CompiledSubAgent
from deepagents.backends import StateBackend


class WebAgentFactory(AgentFactory):
    @property
    def name(self) -> str:
        return "web_agent"

    async def create_agent(
        self,
        *,
        model: Any,
        system_prompt: str,
        tools: Sequence[BaseTool],
        subagents: list[dict],
        skills: list[str],
    ) -> Runnable:
        middlewares = []
        if len(subagents):
            middlewares.append(
                SubAgentMiddleware(
                    backend=StateBackend(),
                    subagents=[CompiledSubAgent(**config) for config in subagents],
                )
            )
        agent = create_agent(
            model=model,
            tools=tools,
            system_prompt=system_prompt,
            middleware=middlewares,
        )
        return agent


web_agent_factory = WebAgentFactory()

factory = web_agent_factory.factory

factory_with_trace = trace_wrapper(factory)
