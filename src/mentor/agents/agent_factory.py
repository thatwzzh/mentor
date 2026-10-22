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
from typing import Any, cast

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
from mentor.middlewares.sandbox_lifecycle import sandbox_lifecycle_middleware
from mentor.config.context_settings import context_settings
from mentor.middlewares.budget import BudgetMiddleware
from mentor.middlewares.context import (
    build_compact_tool_middleware,
    build_summarization_middleware,
)
from mentor.middlewares.precompaction import (
    PendingCompactionMiddleware,
    PostTurnCompactionMiddleware,
)
from mentor.middlewares.token_meter import TokenMeterMiddleware
from mentor.middlewares.tool_executor import ToolExecutorMiddleware
from mentor.middlewares.tool_policy import ToolPolicyMiddleware
from mentor.tools import ToolBundle, ToolSpec, get_tool_bundle
from mentor.tools.specs import ToolRegistry
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
    ) -> list[ToolSpec]:
        """连接单个 MCP server，过滤禁用/未授权工具并生成 ToolSpec。

        只在编译新规格时调用；MCP 配置变化会改变 spec_hash，从而触发重新发现。
        """
        connection: dict[str, Any] = {
            "transport": "streamable_http",
            "url": mcp_config.url,
            "timeout": mcp_config.timeout_seconds,
        }
        if mcp_config.auth:
            connection["headers"] = {"Authorization": mcp_config.auth}
        client = MultiServerMCPClient(
            connections=cast(Any, {mcp_name: connection}),
            tool_name_prefix=True,
        )
        mcp_tools = await client.get_tools()

        # 打开前缀后工具名形如 "<mcp_name>_<tool_name>"，白名单/禁用列表使用同样的前缀。
        allowed = (
            {f"{mcp_name}_{name}" for name in mcp_config.allowed_tools}
            if mcp_config.allowed_tools
            else None
        )
        disabled = {f"{mcp_name}_{name}" for name in mcp_config.disabled_tools}
        specs: list[ToolSpec] = []
        timeout_s = (
            mcp_config.timeout_seconds
            if mcp_config.timeout_seconds > 0
            else 30
        )
        for tool in mcp_tools:
            if tool.name in disabled:
                continue
            if allowed is not None and tool.name not in allowed:
                continue
            specs.append(
                ToolSpec(
                    name=tool.name,
                    tool=tool,
                    risk=mcp_config.risk,
                    description=tool.description or "",
                    exposure=mcp_config.exposure,
                    timeout_s=timeout_s,
                    source="mcp",
                    server=mcp_name,
                )
            )
        return specs

    async def _get_tools(self, agent_config: AgentSpec) -> ToolBundle:
        """内置工具 + 所有 MCP 工具，统一生成清单与直接暴露列表。"""
        bundle = get_tool_bundle(agent_config.tools)
        mcp_spec_groups = await asyncio.gather(
            *[
                self._get_mcp_tools(mcp_name, mcp_config)
                for mcp_name, mcp_config in agent_config.mcps.items()
            ]
        )
        for specs in mcp_spec_groups:
            bundle.register_mcp_specs(specs)
        # 有 deferred 工具时自动补 tool_search / call_tool。
        bundle.add_deferred_tools()
        # 声明了子 Agent 的 Agent 自动获得后台 spawn/check/cancel 工具。
        if agent_config.subagents:
            from mentor.tools.async_subagent import async_subagent_tool_specs

            for spec in async_subagent_tool_specs():
                if bundle.registry.has(spec.name):
                    continue
                bundle.registry.register(spec)
                if spec.exposure == "direct" and spec.tool is not None:
                    bundle.direct_tools.append(spec.tool)
        return bundle

    async def _load_subagent_specs(
        self, names: list[str], label: str | None
    ) -> list[AgentSpec]:
        """加载已注册子 Agent 的规格；未注册名称直接跳过。"""
        configured = [
            name for name in names if get_subagent_factory(name) is not None
        ]
        return list(
            await asyncio.gather(
                *[
                    get_agent_spec(agent_name, label=label)
                    for agent_name in configured
                ]
            )
        )

    async def _build_subagents(
        self, specs: list[AgentSpec], label: str | None
    ) -> list[dict]:
        """把子 Agent 规格交给注册表里的工厂编译，返回 CompiledSubAgent 配置。"""
        subagents = []
        for spec in specs:
            factory = get_subagent_factory(spec.name)
            if factory is None:
                continue
            runnable = await factory(label=label)
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

    async def factory(
        self,
        *,
        override: dict | None = None,
        label: str | None = None,
    ) -> Runnable:
        """返回当前规格对应的编译图；override/label 参与规格与缓存键。"""
        agent_config = await get_agent_spec(
            self.name,
            override=override,
            label=label,
        )
        # 技能内容更新不改变图结构：ensure_skills 内部有进程内缓存和后台刷新。
        await ensure_skills(self.name, agent_config.skills)

        subagent_specs = await self._load_subagent_specs(
            agent_config.subagents, label
        )
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
            tool_bundle = await self._get_tools(agent_config)
            subagents = await self._build_subagents(subagent_specs, label)
            agent = await self.create_agent(
                model=model,
                system_prompt=agent_config.system_prompt,
                tools=tool_bundle.direct_tools,
                tool_registry=tool_bundle.registry,
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
        tool_registry: ToolRegistry,
        subagents: list[dict],
        skills: list[str],
        spec_hash: str,
    ) -> Runnable:
        backend = self.get_backend()
        middleware = list(self.get_middlewares())
        # 显式压缩配置；同名中间件会替换 DeepAgents 默认 SummarizationMiddleware。
        # 测试/自定义工厂可能传 model=None，此时跳过模型相关中间件。
        if model is not None:
            middleware.append(BudgetMiddleware())
            middleware.append(TokenMeterMiddleware())
            manual_summarization = build_summarization_middleware(
                model, backend, auto=True
            )
            auto_summarization = (
                manual_summarization
                if context_settings.auto_compact
                else build_summarization_middleware(model, backend, auto=False)
            )
            middleware.append(auto_summarization)
            middleware.append(
                build_compact_tool_middleware(manual_summarization)
            )
            # 预压缩只挂主 Agent，避免子 Agent 共享 thread 时重复覆盖摘要。
            if self.name == "main_agent":
                middleware.append(PendingCompactionMiddleware())
                middleware.append(PostTurnCompactionMiddleware(manual_summarization))
        # 策略在工具执行器外层：deny/prompt 不会触发工具副作用。
        middleware.append(ToolPolicyMiddleware(tool_registry))
        middleware.append(ToolExecutorMiddleware(tool_registry))
        if isinstance(backend, SandboxBackend):
            # run 结束后关闭当前 (user, thread) 的沙箱连接；异常路径由 aexecute 兜底。
            middleware.append(sandbox_lifecycle_middleware(backend))

        config = {
            "model": model,
            "system_prompt": system_prompt,
            "tools": tools,
            "state_schema": self.get_state_schema(),
            "subagents": self.get_subagents(subagents),
            "middleware": middleware,
            "backend": backend,
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

    def cached_versions(self) -> list[str]:
        """返回当前进程内保留的 spec_hash，供灰度/回滚排查。"""
        return self._cache.versions(self.name)

    def rollback(self, spec_hash: str) -> Runnable | None:
        """取回指定历史版本；仅限仍在缓存中的版本，不修改 Nacos。"""
        return self._cache.get(self.name, spec_hash)

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
