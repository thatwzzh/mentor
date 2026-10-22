"""5.3 TokenMeter 与压缩配置测试。"""

import asyncio
from types import SimpleNamespace
from typing import Any, cast

from langchain.agents.middleware.types import ModelResponse
from langchain_core.messages import AIMessage, HumanMessage

from mentor.config.context_settings import context_settings
from mentor.middlewares.context import context_window_for
from mentor.events.projection import build_summary
from mentor.middlewares.token_meter import TokenMeterMiddleware


def test_context_window_prefers_model_profile(monkeypatch):
    class Model:
        profile = {"max_input_tokens": 64_000}

    assert context_window_for(Model()) == 64_000
    monkeypatch.setattr(context_settings, "window_tokens", 32_000)
    assert context_window_for(object()) == 32_000


def test_token_meter_writes_estimated_and_actual_usage(monkeypatch):
    events = []
    middleware = TokenMeterMiddleware(writer=events.append)
    monkeypatch.setattr(
        "mentor.middlewares.token_meter.runtime_ids",
        lambda: ("run-usage", "thread-1", "user-1"),
    )
    messages = [HumanMessage(content="a" * 400, id="h1")]

    async def handler(request):
        return ModelResponse(
            result=[
                AIMessage(
                    content="answer",
                    id="a1",
                    usage_metadata={
                        "input_tokens": 120,
                        "output_tokens": 30,
                        "total_tokens": 150,
                    },
                )
            ]
        )

    request = cast(
        Any,
        SimpleNamespace(
            messages=messages,
            model=SimpleNamespace(model_name="chat-standard"),
        ),
    )
    response = asyncio.run(middleware.awrap_model_call(request, handler))
    assert isinstance(response, ModelResponse)
    assert len(events) == 1
    event = events[0]
    assert event.kind == "usage"
    assert event.data["estimated_input_tokens"] >= 100
    assert event.data["token_usage"]["total_tokens"] == 150
    assert event.data["message_count"] == 1
    summary = build_summary([event])
    assert summary.token_usage["total_tokens"] == 150
