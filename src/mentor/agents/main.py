from collections.abc import Sequence
from typing import Any

from langchain.tools import BaseTool

from deepagents import create_deep_agent
from deepagents.middleware import CompiledSubAgent
from mentor.utils.trace_wrapper import trace_wrapper
from mentor.agents.main_agent_state import MainAgentState
from mentor.middlewares.turn_summary import turn_summary
from mentor.agents.agent_factory import AgentFactory

from mentor.backends.sandbox_backend import SandboxBackend


class MainAgentFactory(AgentFactory):
    @property
    def name(self) -> str:
        return "main_agent"

    async def create_agent(
        self,
        *,
        model: Any,
        system_prompt: str,
        tools: Sequence[BaseTool],
        subagents: list[dict],
        skills: list[str],
    ):
        agent = create_deep_agent(
            model=model,
            system_prompt=system_prompt,
            tools=tools,
            state_schema=MainAgentState,
            subagents=[CompiledSubAgent(**config) for config in subagents],
            middleware=[turn_summary],
            backend=SandboxBackend(),
        )
        return agent


main_agent_factory = MainAgentFactory()

factory = main_agent_factory.factory

factory_with_trace = trace_wrapper(factory)
