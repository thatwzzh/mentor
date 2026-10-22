"""按 sandbox profile 限制步数/工具调用/并发的运行时护栏。"""

import asyncio
from contextvars import ContextVar
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage

from mentor.config.nacos.nacos_schemas import SandboxProfile

_counters: ContextVar[dict[str, int] | None] = ContextVar(
    "sandbox_profile_counters", default=None
)


class SandboxProfileLimitError(RuntimeError):
    """sandbox profile 的步数/工具调用上限超限。"""


class SandboxProfileGuardMiddleware(AgentMiddleware):
    """max_steps / max_tool_calls / max_parallel_tools 的硬护栏。"""

    def __init__(self, profile: SandboxProfile) -> None:
        super().__init__()
        self.profile = profile
        self._semaphore = asyncio.Semaphore(profile.max_parallel_tools)

    async def abefore_agent(self, state: Any, runtime: Any) -> None:
        _counters.set({"steps": 0, "tools": 0})

    def _counter(self) -> dict[str, int]:
        counters = _counters.get()
        if counters is None:
            counters = {"steps": 0, "tools": 0}
            _counters.set(counters)
        return counters

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        counters = self._counter()
        counters["steps"] += 1
        if counters["steps"] > self.profile.max_steps:
            raise SandboxProfileLimitError(
                f"超过 profile {self.profile.max_steps} 步限制"
            )
        return await handler(request)

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        counters = self._counter()
        counters["tools"] += 1
        if counters["tools"] > self.profile.max_tool_calls:
            tool_call = getattr(request, "tool_call", {}) or {}
            return ToolMessage(
                content="[sandbox_profile] 工具调用次数超过档位上限",
                tool_call_id=str(tool_call.get("id") or ""),
                name=str(tool_call.get("name") or ""),
                status="error",
                artifact={
                    "ok": False,
                    "error_kind": "policy",
                    "truncated": False,
                    "artifacts": [],
                    "duration_ms": 0,
                },
            )
        async with self._semaphore:
            return await handler(request)
