"""工具执行中间件：把 Agent 的所有工具调用接到统一 ToolExecutor。"""

import logging
import time

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from mentor.budget.run_budget import get_run_budget
from mentor.events.context import runtime_ids
from mentor.events.models import RunEvent
from mentor.events.writer import EventWriter, append_event
from mentor.tools.executor import ToolExecutor
from mentor.tools.specs import ToolRegistry

logger = logging.getLogger(__name__)


class ToolExecutorMiddleware(AgentMiddleware):
    """覆盖内置、业务、MCP 工具；未登记工具按默认契约处理。"""

    def __init__(
        self, registry: ToolRegistry, writer: EventWriter = append_event
    ) -> None:
        super().__init__()
        self.registry = registry
        self.executor = ToolExecutor()
        self.writer = writer

    def _write(self, event: RunEvent) -> None:
        try:
            self.writer(event)
        except Exception as exc:
            logger.warning(
                "run_events 写入失败 run_id=%s kind=%s: %s",
                event.run_id,
                event.kind,
                exc,
            )

    async def awrap_tool_call(
        self,
        request,
        handler,
    ) -> ToolMessage | Command:
        budget = get_run_budget()
        if budget is not None:
            budget.check_deadline()
        tool_call = getattr(request, "tool_call", {}) or {}
        name = tool_call.get("name", "")
        run_id, thread_id, user_id = runtime_ids()
        call_id = str(tool_call.get("id") or "")
        call_key = call_id or f"{run_id}:{name}:{time.monotonic_ns()}"
        self._write(
            RunEvent(
                run_id=run_id,
                thread_id=thread_id,
                user_id=user_id,
                event_key=f"tool_call:{call_key}",
                kind="tool_call",
                role="assistant",
                name=name,
                data={
                    "args": tool_call.get("args", {}),
                    "tool_call_id": call_id or None,
                },
            )
        )
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
        if isinstance(result.message, ToolMessage):
            kind = "approval" if name == "ask_user_question" else "tool_result"
            self._write(
                RunEvent(
                    run_id=run_id,
                    thread_id=thread_id,
                    user_id=user_id,
                    event_key=f"{kind}:{call_key}",
                    kind=kind,
                    role="tool",
                    name=name,
                    content=str(result.message.content),
                    data={
                        "tool_call_id": call_id or None,
                        "status": result.message.status,
                        "error_kind": envelope.get("error_kind"),
                        "truncated": envelope.get("truncated"),
                        "artifacts": envelope.get("artifacts", []),
                        "minio_ref": envelope.get("minio_ref"),
                        "duration_ms": envelope.get("duration_ms"),
                    },
                )
            )
        if result.message is not None:
            return result.message
        raise RuntimeError(f"工具 {name} 未返回 ToolMessage/Command")
