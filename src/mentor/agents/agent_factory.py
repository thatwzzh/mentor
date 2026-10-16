from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any

from langchain.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from mentor.config.nacos.nacos_agent_spec import get_agent_spec
from mentor.config.nacos.nacos_schemas import AgentSpec
from langchain_core.runnables import Runnable
from mentor.utils.create_model import create_model
from mentor.tools import get_tools
import asyncio
from mentor.utils.func_tools import func_cache
from importlib import import_module

_AGENT_FACTORIES = {"web_agent": "mentor.agents.web"}


class AgentFactory(ABC):

    @property
    @abstractmethod
    def name(self) -> str: ...

    agent_config: AgentSpec | None = None

    def _get_model(self) -> Any:
        if not self.agent_config:
            return create_model()
        return create_model(self.agent_config.model)

    @func_cache(ttl=86400)
    async def _get_mcp_tools(self, mcp_name: str) -> Sequence[BaseTool]:
        if not self.agent_config:
            return []
        if mcp_name not in self.agent_config.mcps:
            return []
        mcp_config = self.agent_config.mcps[mcp_name]
        client = MultiServerMCPClient(
            connections={
                mcp_name: {
                    "transport": "streamable_http",
                    "url": mcp_config.url,
                }
            },
            tool_name_prefix=True,
        )
        mcp_tools = await client.get_tools()

        disabled_tool_names = [
            f"{mcp_name}_{name}" for name in mcp_config.disabled_tools
        ]
        tools = [tool for tool in mcp_tools if tool.name not in disabled_tool_names]
        return tools

    async def _get_tools(self) -> Sequence[BaseTool]:
        if not self.agent_config:
            return []
        # 获取内置工具
        tools = get_tools(self.agent_config.tools)
        mcp_tool_groups = await asyncio.gather(
            *[
                self._get_mcp_tools(mcp_name)
                for mcp_name in self.agent_config.mcps.keys()
            ]
        )
        for mcp_tools in mcp_tool_groups:
            tools.extend(mcp_tools)
        return tools

    async def _get_subagents(self) -> list[dict]:
        if not self.agent_config:
            return []
        specs = await asyncio.gather(
            *[get_agent_spec(agent_name) for agent_name in self.agent_config.subagents]
        )

        subagents = []
        for spec in specs:
            name = spec.name
            description = spec.description
            if name not in _AGENT_FACTORIES:
                continue
            module_path = _AGENT_FACTORIES[name]  # 模块路径
            module = import_module(module_path)
            factory = getattr(module, "factory")
            runnable = await factory()
            subagents.append(
                {"name": name, "description": description, "runnable": runnable}
            )

        return subagents

    async def factory(self) -> Runnable:
        agent_config = await get_agent_spec(self.name)
        self.agent_config = agent_config
        model = self._get_model()
        tools = await self._get_tools()
        subagents = await self._get_subagents()
        agent = await self.create_agent(
            model=model,
            system_prompt=agent_config.system_prompt,
            tools=tools,
            subagents=subagents,
            skills=agent_config.skills,
        )
        return agent

    @abstractmethod
    async def create_agent(
        self,
        *,
        model: Any,
        system_prompt: str,
        tools: Sequence[BaseTool],
        subagents: list[dict],
        skills: list[str],
    ) -> Runnable: ...
