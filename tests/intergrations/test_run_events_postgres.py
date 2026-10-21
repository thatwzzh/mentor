"""Postgres 事件表集成验收；未提供 RUN_EVENTS_POSTGRES_DSN 时跳过。"""

import os
from uuid import uuid4

import pytest

from mentor.events.models import RunEvent
from mentor.events.store import RunEventStore

DSN = os.environ.get("RUN_EVENTS_POSTGRES_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="需要 RUN_EVENTS_POSTGRES_DSN")


def test_postgres_store_roundtrip_and_user_filter():
    store = RunEventStore(DSN)
    run_id = f"it-{uuid4().hex}"
    try:
        events = [
            RunEvent(
                run_id=run_id,
                thread_id="thread-1",
                user_id="user-a",
                event_key="e1",
                kind="message",
                role="user",
                content="hello",
            ),
            RunEvent(
                run_id=run_id,
                thread_id="thread-1",
                user_id="user-a",
                event_key="e2",
                kind="message",
                role="assistant",
                content="world",
            ),
        ]
        assert store.append_many(events) == 2
        assert store.append_many(events) == 0
        listed = store.list(run_id, user_id="user-a")
        assert [event.seq for event in listed] == [1, 2]
        assert store.list(run_id, user_id="user-b") == []
        assert store.list(run_id, user_id="user-a", after_seq=1)[0].content == "world"
    finally:
        store.clear(run_id)
        store.close()
