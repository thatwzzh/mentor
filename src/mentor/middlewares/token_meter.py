"""TokenMeter：调用前估算、调用后回填 usage，并写 usage 事件。"""

import logging
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, BaseMessage

from mentor.events.context import runtime_ids
from mentor.events.models import RunEvent
from mentor.events.projection import estimate_tokens
from mentor.events.writer import EventWriter, append_event

logger = logging.getLogger(__name__)


def _response_messages(response: Any) -> list[BaseMessage]:
    response = getattr(response, "model_response", response)
    result = getattr(response, "result", None)
    if isinstance(result, list):
        return result
    if isinstance(response, BaseMessage):
        return [response]
    return []


class TokenMeterMiddleware(AgentMiddleware):
    """每次模型调用写一条 usage 事件；不自定义 tokenizer，优先用模型回填值。"""

    def __init__(self, writer: EventWriter = append_event) -> None:
        super().__init__()
        self.writer = writer

    def _write(self, event: RunEvent) -> None:
        try:
            self.writer(event)
        except Exception as exc:
            logger.warning("usage 事件写入失败 run_id=%s: %s", event.run_id, exc)

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        run_id, thread_id, user_id = runtime_ids()
        messages = list(getattr(request, "messages", []) or [])
        estimated_input = estimate_tokens(messages)
        model_name = (
            getattr(getattr(request, "model", None), "model_name", None)
            or getattr(getattr(request, "model", None), "model", None)
        )
        try:
            response = await handler(request)
        except BaseException as exc:
            self._write(
                RunEvent(
                    run_id=run_id,
                    thread_id=thread_id,
                    user_id=user_id,
                    event_key=f"usage:{run_id}:error:{type(exc).__name__}",
                    kind="usage",
                    role="event",
                    name=str(model_name) if model_name else None,
                    data={
                        "estimated_input_tokens": estimated_input,
                        "error": str(exc),
                        "message_count": len(messages),
                    },
                )
            )
            raise

        raw_response = response
        responses = _response_messages(response)
        last = next(
            (msg for msg in reversed(responses) if isinstance(msg, AIMessage)),
            None,
        )
        usage = getattr(last, "usage_metadata", None) or {}
        if not isinstance(usage, dict):
            usage = {}
        response_metadata = (
            getattr(last, "response_metadata", {}) if last is not None else {}
        )
        served_model = None
        if isinstance(response_metadata, dict):
            served_model = (
                response_metadata.get("model_name")
                or response_metadata.get("model")
            )
        output_estimate = (
            estimate_tokens([last]) if last is not None else 0
        )
        input_tokens = int(usage.get("input_tokens", 0) or 0)
        output_tokens = int(usage.get("output_tokens", 0) or output_estimate)
        total_tokens = int(
            usage.get("total_tokens", 0) or input_tokens + output_tokens
        )
        self._write(
            RunEvent(
                run_id=run_id,
                thread_id=thread_id,
                user_id=user_id,
                event_key=f"usage:{run_id}:{getattr(last, 'id', None) or len(messages)}",
                kind="usage",
                role="event",
                name=str(model_name) if model_name else None,
                data={
                    "estimated_input_tokens": estimated_input,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "total_tokens": total_tokens,
                    "served_model": served_model or model_name,
                    "message_count": len(messages),
                    "token_usage": {
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                        "total_tokens": total_tokens,
                    },
                },
            )
        )
        return raw_response
