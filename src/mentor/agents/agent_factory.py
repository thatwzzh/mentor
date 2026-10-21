"""Agent 工厂：按 Nacos 规格装配 DeepAgents 图，并按 spec_hash 缓存编译结果。

一次编译包含四个阶段：读取主/子 Agent 规格 → 计算规格 hash → 命中缓存则直接返回
→ 未命中时装配模型、工具、子 Agent 与技能并写入缓存。规格 hash 覆盖主 Agent 与
所有子 Agent 的完整配置，因此 Prompt、模型、MCP、工具白名单或子 Agent 变化都会
自然切换到新版本；技能内容由 ensure_skills 单独刷新，不需要重建图。
"""

import asyncio
import hashlib
import json
import logging
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
from langchain.agents.middleware import AgentMiddleware
from langchain.tools import BaseTool
from langchain_core.runnables import Runnable
from langchain_mcp_adapters.client import MultiServerMCPClient

from mentor.agents.agent_cache import AgentCache
from mentor.agents.subagent_registry import get_subagent_factory
from mentor.backends.sandbox_backend import SandboxBackend
from mentor.config.nacos.nacos_agent_spec import get_agent_spec
from mentor.config.nacos.nacos_schemas import AgentSpec, MCPServerConfig
from mentor.config.nacos.nacos_skill import ensure_skills, skill_path
from mentor.tools import get_tools
from mentor.utils.create_model import create_model

logger = logging.getLogger(__name__)


class AgentFactory(ABC):
    """所有 Agent 的装配基类；子类只覆盖 name/state/middleware 等差异。"""

    def __init__(self) -> None:
        # 每个 Agent 保留最近 3 个规格版本；旧版本被淘汰时，in-flight run 仍持有引用。
        self._cache = AgentCache(max_versions_per_agent=3)
        # 同一 Agent 的并发首次请求只允许一个协程编译，其余等待后复用缓存。
        self._build_locks: dict[str, asyncio.Lock] = {}

    @property
    @abstractmethod
    def name(self) -> str: ...

    def _get_model(self, agent_config: AgentSpec) -> Any:
        """按 AgentSpec 创建模型；模型对象只依赖静态配置，可安全复用。"""
        return create_model(agent_config.model)

    async def _get_mcp_tools(
        self, mcp_name: str, mcp_config: MCPServerConfig
    ) -> Sequence[BaseTool]:
        """连接单个 MCP server 并过滤禁用工具。

        只在编译新规格时调用；MCP 配置变化会改变 spec_hash，从而触发重新发现。
        """
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
        return [tool for tool in mcp_tools if tool.name not in disabled_tool_names]

    async def _get_tools(self, agent_config: AgentSpec) -> Sequence[BaseTool]:
        """内置工具 + 所有 MCP 工具，统一在编译阶段装配。"""
        tools = get_tools(agent_config.tools)
        mcp_tool_groups = await asyncio.gather(
            *[
                self._get_mcp_tools(mcp_name, mcp_config)
                for mcp_name, mcp_config in agent_config.mcps.items()
            ]
        )
        for mcp_tools in mcp_tool_groups:
            tools.extend(mcp_tools)
        return tools

    async def _load_subagent_specs(self, names: list[str]) -> list[AgentSpec]:
        """加载已注册子 Agent 的规格；未注册名称直接跳过。"""
        configured = [
            name for name in names if get_subagent_factory(name) is not None
        ]
        return list(
            await asyncio.gather(
                *[get_agent_spec(agent_name) for agent_name in configured]
            )
        )

    async def _build_subagents(self, specs: list[AgentSpec]) -> list[dict]:
        """把子 Agent 规格交给注册表里的工厂编译，返回 CompiledSubAgent 配置。"""
        subagents = []
        for spec in specs:
            factory = get_subagent_factory(spec.name)
            if factory is None:
                continue
            runnable = await factory()
            subagents.append(
                {
                    "name": spec.name,
                    "description": spec.description,
                    "runnable": runnable,
                }
            )
        return subagents

    @staticmethod
    def _spec_hash(
        agent_config: AgentSpec, subagent_specs: list[AgentSpec]
    ) -> str:
        """计算规格内容 hash；主/子 Agent 任一字段变化都会得到新 hash。"""
        payload = {
            "agent": agent_config.model_dump(mode="json"),
            "subagents": [
                spec.model_dump(mode="json")
                for spec in sorted(subagent_specs, key=lambda item: item.name)
            ],
        }
        canonical = json.dumps(
            payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    async def factory(self) -> Runnable:
        """返回当前规格对应的编译图；命中缓存时跳过模型/工具/子 Agent 重建。"""
        agent_config = await get_agent_spec(self.name)
        # 技能内容更新不改变图结构：ensure_skills 内部有进程内缓存和后台刷新。
        await ensure_skills(self.name, agent_config.skills)

        subagent_specs = await self._load_subagent_specs(agent_config.subagents)
        spec_hash = self._spec_hash(agent_config, subagent_specs)
        cached = self._cache.get(self.name, spec_hash)
        if cached is not None:
            logger.debug("agent cache hit: %s spec_hash=%s", self.name, spec_hash[:12])
            return cached

        lock = self._build_locks.setdefault(self.name, asyncio.Lock())
        async with lock:
            # 等锁期间可能已有其他协程完成同一规格的编译。
            cached = self._cache.get(self.name, spec_hash)
            if cached is not None:
                return cached

            model = self._get_model(agent_config)
            tools = await self._get_tools(agent_config)
            subagents = await self._build_subagents(subagent_specs)
            agent = await self.create_agent(
                model=model,
                system_prompt=agent_config.system_prompt,
                tools=tools,
                subagents=subagents,
                skills=agent_config.skills,
                spec_hash=spec_hash,
            )
            self._cache.put(self.name, spec_hash, agent)
            logger.info(
                "agent compiled: %s spec_hash=%s", self.name, spec_hash[:12]
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
        spec_hash: str,
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
        # 把规格 hash 写进图 metadata；trace_wrapper 的 with_config 会合并到 Langfuse trace。
        return agent.with_config(metadata={"agent_spec_hash": spec_hash})

    def cache_stats(self) -> dict[str, object]:
        """暴露缓存统计，供健康检查/调试接口使用。"""
        return self._cache.stats()

    def clear_cache(self) -> None:
        """清空当前工厂的编译缓存；主要供测试或运维手动失效使用。"""
        self._cache.clear()

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
