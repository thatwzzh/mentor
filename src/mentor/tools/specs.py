"""工具清单：ToolSpec 元数据、内置工具注册表与风险/暴露级别。

新增业务工具的标准写法：

    @register_tool(risk="read", timeout_s=10)
    @tool
    def my_tool(...): ...

注册后 AgentFactory 会把它放进统一 ToolExecutor，模型可见的工具名仍由
Nacos AgentSpec 的 tools 白名单决定。
"""

from dataclasses import dataclass, replace
from typing import Literal

from langchain_core.tools import BaseTool

ToolRisk = Literal["read", "write", "external", "destructive"]
ToolExposure = Literal["direct", "deferred", "hidden"]
ToolSource = Literal["builtin", "business", "mcp"]


@dataclass(frozen=True)
class ToolSpec:
    """一个工具的执行契约；执行器只依赖这些元数据。"""

    name: str
    tool: BaseTool | None
    risk: ToolRisk
    description: str = ""
    exposure: ToolExposure = "direct"
    timeout_s: float | None = 30
    idempotent: bool = False
    max_output_chars: int = 8_000
    source: ToolSource = "builtin"
    server: str | None = None


# DeepAgents 自带工具不在 get_tools 里；这里登记默认契约，供中间件统一限时/截断。
_DEEPAGENT_DEFAULTS: dict[str, tuple[ToolRisk, float]] = {
    "ls": ("read", 30),
    "read_file": ("read", 30),
    "glob": ("read", 30),
    "grep": ("read", 30),
    "write_file": ("write", 30),
    "edit_file": ("write", 30),
    "execute": ("destructive", 90),
    "task": ("external", 120),
    "compact_conversation": ("read", 120),
}

_APP_SPECS: dict[str, ToolSpec] = {}


def register_tool(
    *,
    name: str | None = None,
    risk: ToolRisk,
    exposure: ToolExposure = "direct",
    timeout_s: float | None = 30,
    idempotent: bool = False,
    max_output_chars: int = 8_000,
):
    """把 @tool 装饰后的工具登记为业务工具；只需一行装饰器。"""

    def decorator(tool: BaseTool) -> BaseTool:
        spec = ToolSpec(
            name=name or tool.name,
            tool=tool,
            risk=risk,
            description=tool.description or "",
            exposure=exposure,
            timeout_s=timeout_s,
            idempotent=idempotent,
            max_output_chars=max_output_chars,
            source="business",
        )
        if spec.name in _APP_SPECS:
            raise ValueError(f"工具已注册: {spec.name}")
        _APP_SPECS[spec.name] = spec
        return tool

    return decorator


def app_specs() -> dict[str, ToolSpec]:
    """返回已登记的业务工具清单。"""
    return dict(_APP_SPECS)


class ToolRegistry:
    """单个 Agent 的工具清单；同名工具直接报错，避免 MCP/内置冲突。"""

    def __init__(self) -> None:
        self._specs: dict[str, ToolSpec] = {}
        for name, (risk, timeout_s) in _DEEPAGENT_DEFAULTS.items():
            self._specs[name] = ToolSpec(
                name=name,
                tool=None,
                risk=risk,
                description=f"DeepAgents 内置工具 {name}",
                timeout_s=timeout_s,
            )

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._specs and self._specs[spec.name].tool is not None:
            raise ValueError(f"工具已注册: {spec.name}")
        self._specs[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)

    def has(self, name: str) -> bool:
        return name in self._specs

    def default(self, name: str) -> ToolSpec:
        """未知工具按 write/30s 处理，保证中间件仍能限时与分类错误。"""
        return ToolSpec(
            name=name,
            tool=None,
            risk="write",
            description="未登记工具",
            timeout_s=30,
        )

    def direct_tools(self) -> list[BaseTool]:
        return [
            spec.tool
            for spec in self._specs.values()
            if spec.exposure == "direct" and spec.tool is not None
        ]

    def deferred_specs(self) -> list[ToolSpec]:
        return [
            spec for spec in self._specs.values() if spec.exposure == "deferred"
        ]

    def search(self, query: str) -> list[ToolSpec]:
        needle = query.strip().lower()
        if not needle:
            return self.deferred_specs()
        return [
            spec
            for spec in self._specs.values()
            if spec.exposure == "deferred"
            and (
                needle in spec.name.lower()
                or needle in spec.description.lower()
                or (spec.server and needle in spec.server.lower())
            )
        ]

    def with_spec(self, spec: ToolSpec) -> "ToolRegistry":
        """返回带一个新 spec 的副本，便于测试/组合。"""
        clone = ToolRegistry()
        clone._specs = dict(self._specs)
        clone.register(replace(spec))
        return clone
