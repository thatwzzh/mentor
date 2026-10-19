"""Agent 工厂：把 Nacos 规格、模型、工具、MCP、子 Agent 和技能装配成 DeepAgents 图。"""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any

from deepagents import (
    AsyncSubAgent,
    CompiledSubAgent,
    DeepAgentState,
    SubAgent,
    create_deep_agent,
)
from deepagents.backends import BackendProtocol
from langchain.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from mentor.backends.sandbox_backend import SandboxBackend
from mentor.config.nacos.nacos_agent_spec import get_agent_spec
from mentor.config.nacos.nacos_schemas import AgentSpec
from mentor.config.nacos.nacos_skill import ensure_skills, skill_path
from langchain_core.runnables import Runnable
from mentor.utils.create_model import create_model
from mentor.tools import get_tools
import asyncio
from mentor.utils.func_tools import func_cache
from importlib import import_module
from langchain.agents.middleware import AgentMiddleware


# 子 Agent 名称 → 工厂模块路径。新增可委派子 Agent 时在此登记，模块内需导出 factory。
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

        # 打开前缀后工具名形如 "<mcp_name>_<tool_name>"，禁用列表也必须使用同样的前缀。
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
        # 先过滤未注册的名称，避免为一个可选的、未实现的子 Agent 去请求 Nacos。
        configured = [
            name
            for name in self.agent_config.subagents
            if name in _AGENT_FACTORIES
        ]
        specs = await asyncio.gather(
            *[get_agent_spec(agent_name) for agent_name in configured]
        )

        subagents = []
        for spec in specs:
            name = spec.name
            description = spec.description
            module_path = _AGENT_FACTORIES[name]
            module = import_module(module_path)
            factory = getattr(module, "factory")
            runnable = await factory()
            subagents.append(
                {"name": name, "description": description, "runnable": runnable}
            )

        return subagents

    async def factory(self) -> Runnable:
        # 规格在装配期间暂存在实例上，供 _get_model/_get_tools/_get_subagents 复用。
        # 注意：AgentFactory 是单例，factory() 并发执行时 this.agent_config 会互相覆盖。
        agent_config = await get_agent_spec(self.name)
        self.agent_config = agent_config
        # 技能刷新与配置读取解耦：有可用版本时立即返回，过期版本在后台刷新。
        await ensure_skills(self.name, agent_config.skills)
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

    async def create_agent(
        self,
        *,
        model: Any,
        system_prompt: str,
        tools: Sequence[BaseTool],
        subagents: list[dict],
        skills: list[str],
    ) -> Runnable:
        config = {
            "model": model,
            "system_prompt": system_prompt,
            "tools": tools,
            "state_schema": self.get_state_schema(),
            "subagents": self.get_subagents(subagents),
            "middleware": self.get_middlewares(),
            "backend": self.get_backend(),
        }
        if len(skills):
            # 技能已按内容版本落到共享卷；current 是指向可用版本的原子符号链接。
            config["skills"] = [skill_path(self.name)]

        agent = create_deep_agent(**config)
        return agent

    def get_backend(self) -> BackendProtocol | None:
        return SandboxBackend()

    def get_middlewares(self) -> Sequence[AgentMiddleware]:
        return []

    def get_subagents(
        self, subagent_configs: list[dict]
    ) -> Sequence[SubAgent | CompiledSubAgent | AsyncSubAgent] | None:
        return [CompiledSubAgent(**config) for config in subagent_configs]

    def get_state_schema(self) -> type[DeepAgentState] | None:
        return DeepAgentState
