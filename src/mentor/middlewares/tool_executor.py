"""工具执行中间件：把 Agent 的所有工具调用接到统一 ToolExecutor。"""

import logging

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from mentor.tools.executor import ToolExecutor
from mentor.tools.specs import ToolRegistry

logger = logging.getLogger(__name__)


class ToolExecutorMiddleware(AgentMiddleware):
    """覆盖内置、业务、MCP 工具；未登记工具按默认契约处理。"""

    def __init__(self, registry: ToolRegistry) -> None:
        super().__init__()
        self.registry = registry
        self.executor = ToolExecutor()

    async def awrap_tool_call(
        self,
        request,
        handler,
    ) -> ToolMessage | Command:
        tool_call = getattr(request, "tool_call", {}) or {}
        name = tool_call.get("name", "")
        spec = self.registry.get(name) or self.registry.default(name)
        result = await self.executor.execute_handler(spec, request, handler)
        envelope = getattr(result, "envelope", lambda: {})()
        logger.info(
            "tool_exec name=%s ok=%s error_kind=%s truncated=%s duration_ms=%s",
            name,
            envelope.get("ok"),
            envelope.get("error_kind"),
            envelope.get("truncated"),
            envelope.get("duration_ms"),
        )
        if result.message is not None:
            return result.message
        raise RuntimeError(f"工具 {name} 未返回 ToolMessage/Command")
