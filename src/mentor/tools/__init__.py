"""工具装配：内置工具清单 + MCP 工具 + deferred 工具的搜索/调用入口。

业务工具实现后用 @register_tool 登记；AgentFactory 只按 Nacos 白名单选择，
再统一交给 ToolExecutorMiddleware 执行。
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass, field

from langchain_core.tools import BaseTool, StructuredTool

from mentor.tools import artifact as _artifact  # noqa: F401  触发 read_artifact 注册
from mentor.tools.ask_user_question import ask_user_question
from mentor.tools.deploy import deploy
from mentor.tools.executor import ToolExecutor
from mentor.tools.specs import ToolRegistry, ToolSpec, app_specs
from mentor.tools.time import get_current_time
from mentor.tools.web import fetch_url, web_search


@dataclass
class ToolBundle:
    """一个 Agent 的工具清单与执行器；MCP spec 在装配阶段追加。"""

    registry: ToolRegistry
    direct_tools: list[BaseTool]
    executor: ToolExecutor = field(default_factory=ToolExecutor)
    _deferred_registered: bool = False

    def register_mcp_specs(self, specs: Sequence[ToolSpec]) -> None:
        for spec in specs:
            self.registry.register(spec)
            if spec.exposure == "direct" and spec.tool is not None:
                self.direct_tools.append(spec.tool)

    def add_deferred_tools(self) -> None:
        """有 deferred 工具时，追加 tool_search / call_tool 两个直接工具。"""
        if self._deferred_registered or not self.registry.deferred_specs():
            return

        async def search(query: str) -> str:
            specs = self.registry.search(query)
            return json.dumps(
                [
                    {
                        "name": spec.name,
                        "description": spec.description,
                        "risk": spec.risk,
                        "server": spec.server,
                    }
                    for spec in specs
                ],
                ensure_ascii=False,
            )

        async def call(name: str, arguments: dict) -> str:
            spec = self.registry.get(name)
            if spec is None or spec.exposure != "deferred":
                raise ValueError(f"工具不存在或未启用 deferred: {name}")
            result = await self.executor.execute_tool(spec, arguments)
            return json.dumps(result.envelope(), ensure_ascii=False)

        search_tool = StructuredTool.from_function(
            coroutine=search,
            name="tool_search",
            description="按关键字搜索 deferred MCP 工具，返回名称、描述、风险和 server。",
        )
        call_tool = StructuredTool.from_function(
            coroutine=call,
            name="call_tool",
            description="调用 tool_search 找到的 deferred 工具；参数为 name 与 arguments 对象。",
        )
        self.registry.register(
            ToolSpec(
                name="tool_search",
                tool=search_tool,
                risk="read",
                description=search_tool.description or "",
                timeout_s=10,
                source="business",
            )
        )
        self.registry.register(
            ToolSpec(
                name="call_tool",
                tool=call_tool,
                risk="external",
                description=call_tool.description or "",
                timeout_s=120,
                source="business",
            )
        )
        self.direct_tools.extend([search_tool, call_tool])
        self._deferred_registered = True


def get_tool_bundle(names: list[str]) -> ToolBundle:
    """按 Nacos 工具白名单构建清单；未知名称忽略，保持旧行为。"""
    available = app_specs()
    registry = ToolRegistry()
    direct_tools: list[BaseTool] = []
    for name in names:
        spec = available.get(name)
        if spec is None or registry.has(name):
            continue
        registry.register(spec)
        if spec.exposure == "direct" and spec.tool is not None:
            direct_tools.append(spec.tool)
    # 截断输出会返回 artifact:// 引用，读回工具始终可用，避免引用无法消费。
    if not registry.has("read_artifact"):
        artifact_spec = available.get("read_artifact")
        if artifact_spec is not None:
            registry.register(artifact_spec)
            if artifact_spec.tool is not None:
                direct_tools.append(artifact_spec.tool)
    return ToolBundle(registry=registry, direct_tools=direct_tools)


def get_tools(names: list[str]) -> list[BaseTool]:
    """兼容旧调用：只返回直接暴露的工具。"""
    return get_tool_bundle(names).direct_tools


__all__ = [
    "ToolBundle",
    "ToolExecutor",
    "ToolSpec",
    "ask_user_question",
    "deploy",
    "fetch_url",
    "get_current_time",
    "get_tool_bundle",
    "get_tools",
    "web_search",
]
