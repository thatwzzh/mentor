"""Run 生命周期与模型事件：run_started / turn_started / model_* / run_finished。

工具事件由 ToolExecutorMiddleware 在副作用前后写入；这里只负责 Agent/模型层，
保证崩溃时事件表里至少能定位到已开始的 run、已开始的模型调用和工具调用。
"""

import hashlib
import logging
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from mentor.events.context import runtime_ids
from mentor.events.models import RunEvent
from mentor.events.writer import EventWriter, append_event

logger = logging.getLogger(__name__)


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        import json

        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        return str(value)


class RunEventsMiddleware(AgentMiddleware):
    """写 run/模型生命周期事件；写失败只告警，不阻断业务。"""

    def __init__(self, agent_name: str, writer: EventWriter = append_event) -> None:
        super().__init__()
        self.agent_name = agent_name
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

    async def abefore_agent(self, state: Any, runtime: Any) -> None:
        messages = list(state.get("messages", []) or [])
        run_id, thread_id, user_id = runtime_ids()
        self._write(
            RunEvent(
                run_id=run_id,
                thread_id=thread_id,
                user_id=user_id,
                event_key=f"run_started:{run_id}",
                kind="run_started",
                role="event",
                data={"agent": self.agent_name},
            )
        )
        human = next(
            (msg for msg in reversed(messages) if isinstance(msg, HumanMessage)),
            None,
        )
        if human is not None:
            self._write(
                RunEvent(
                    run_id=run_id,
                    thread_id=thread_id,
                    user_id=user_id,
                    event_key=f"turn_started:{run_id}:{human.id or 'last'}",
                    kind="turn_started",
                    role="user",
                    content=_text(human.content)[:1000],
                    data={"message_id": human.id},
                )
            )

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        run_id, thread_id, user_id = runtime_ids()
        messages = list(getattr(request, "messages", []) or [])
        last_id = getattr(messages[-1], "id", None) if messages else None
        request_key = f"model_request:{run_id}:{len(messages)}:{last_id or 'none'}"
        model_name = (
            getattr(getattr(request, "model", None), "model_name", None)
            or getattr(getattr(request, "model", None), "model", None)
        )
        self._write(
            RunEvent(
                run_id=run_id,
                thread_id=thread_id,
                user_id=user_id,
                event_key=request_key,
                kind="model_request",
                role="event",
                name=str(model_name) if model_name else None,
                data={"message_count": len(messages)},
            )
        )
        try:
            response = await handler(request)
        except BaseException as exc:
            self._write(
                RunEvent(
                    run_id=run_id,
                    thread_id=thread_id,
                    user_id=user_id,
                    event_key=f"model_response:{run_id}:{request_key}",
                    kind="model_response",
                    role="event",
                    data={"error": str(exc), "error_kind": "system"},
                )
            )
            raise

        # wrap_model_call 可能返回 ExtendedModelResponse（带 Command），先取底层响应。
        raw_response = response
        response = getattr(response, "model_response", response)
        result = getattr(response, "result", None)
        if isinstance(result, list):
            responses = result
        elif isinstance(response, BaseMessage):
            responses = [response]
        else:
            responses = []
        last = next(
            (msg for msg in reversed(responses) if isinstance(msg, AIMessage)),
            None,
        )
        usage = getattr(last, "usage_metadata", None) if last else None
        self._write(
            RunEvent(
                run_id=run_id,
                thread_id=thread_id,
                user_id=user_id,
                event_key=f"model_response:{run_id}:{getattr(last, 'id', None) or request_key}",
                kind="model_response",
                role="assistant",
                content=_text(last.content)[:1000] if last else None,
                data={
                    "token_usage": usage or {},
                    "tool_calls": [
                        call.get("name")
                        for call in (getattr(last, "tool_calls", None) or [])
                        if isinstance(call, dict)
                    ],
                },
            )
        )
        return raw_response

    async def aafter_agent(self, state: Any, runtime: Any) -> None:
        run_id, thread_id, user_id = runtime_ids()
        messages = list(state.get("messages", []) or [])
        summarization = state.get("_summarization_event")
        if isinstance(summarization, dict):
            summary_message = summarization.get("summary_message")
            summary = _text(getattr(summary_message, "content", "") or "")
            summary_hash = hashlib.sha256(summary.encode("utf-8")).hexdigest()[:16]
            self._write(
                RunEvent(
                    run_id=run_id,
                    thread_id=thread_id,
                    user_id=user_id,
                    event_key=f"compression:summarization:{summary_hash}",
                    kind="compression",
                    role="event",
                    data={
                        "source": "summarization",
                        "cutoff_index": summarization.get("cutoff_index"),
                        "summary_hash": summary_hash,
                        "summary": summary[:2000],
                    },
                )
            )
        self._write(
            RunEvent(
                run_id=run_id,
                thread_id=thread_id,
                user_id=user_id,
                event_key=f"run_finished:{run_id}",
                kind="run_finished",
                role="event",
                data={"status": "ok", "messages": len(messages)},
            )
        )
