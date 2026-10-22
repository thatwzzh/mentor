"""异步预压缩测试：回答后生成 pending，下一轮只注入摘要。"""

import asyncio
from types import SimpleNamespace
from typing import Any, cast

from langchain_core.messages import HumanMessage, RemoveMessage

from mentor.events.context_summary import (
    InMemoryContextSummaryStore,
    PendingSummary,
)
from mentor.middlewares.precompaction import (
    PendingCompactionMiddleware,
    PostTurnCompactionMiddleware,
)


class FakeSummarization:
    model = SimpleNamespace(profile={"max_input_tokens": 100_000})

    def _count_tokens(self, messages, system_message, tools):
        return 70_000

    async def _acreate_summary(self, messages):
        return "SUMMARY: user is debugging a login bug"


def test_pending_summary_is_injected_next_turn(monkeypatch):
    store = InMemoryContextSummaryStore()
    store.upsert(
        PendingSummary(
            thread_id="thread-1",
            user_id="user-1",
            summary="SUMMARY: keep the API key out of logs",
        )
    )
    middleware = PendingCompactionMiddleware(store=store)
    monkeypatch.setattr(
        "mentor.middlewares.precompaction.runtime_ids",
        lambda: ("run-2", "thread-1", "user-1"),
    )
    state = {"messages": [HumanMessage(content="new question", id="h2")]}
    update = asyncio.run(
        middleware.abefore_agent(state, cast(Any, None))
    )
    assert update is not None
    messages = update["messages"]
    assert isinstance(messages[0], RemoveMessage)
    assert messages[1].additional_kwargs["lc_source"] == "summarization"
    assert "API key" in messages[1].content
    assert store.get("thread-1") is None


def test_post_turn_precompaction_writes_pending_and_event(monkeypatch):
    store = InMemoryContextSummaryStore()
    events = []
    middleware = PostTurnCompactionMiddleware(
        FakeSummarization(), store=store, writer=events.append
    )
    monkeypatch.setattr(
        "mentor.middlewares.precompaction.runtime_ids",
        lambda: ("run-1", "thread-1", "user-1"),
    )
    messages = [HumanMessage(content=f"q{i}", id=f"h{i}") for i in range(11)]

    async def scenario():
        await middleware.aafter_agent(
            {"messages": messages}, cast(Any, None)
        )
        # 等后台 task 完成
        await asyncio.sleep(0.01)

    asyncio.run(scenario())
    pending = store.get("thread-1")
    assert pending is not None
    assert "login bug" in pending.summary
    assert events and events[0].kind == "compression"
    assert events[0].data["source"] == "precompact"


def test_pending_claim_and_ready_claim_are_atomic():
    store = InMemoryContextSummaryStore()
    assert store.try_claim("thread-1", "run-1", "user-1", 10) is True
    assert store.try_claim("thread-1", "run-2", "user-1", 10) is False
    assert store.mark_ready("thread-1", "summary", 10) is True
    first = store.claim_ready("thread-1")
    second = store.claim_ready("thread-1")
    assert first is not None and first.summary == "summary"
    assert second is None
