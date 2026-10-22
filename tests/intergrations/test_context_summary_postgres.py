"""预压缩摘要表 Postgres 集成验收；未提供 RUN_EVENTS_POSTGRES_DSN 时跳过。"""

import os
from uuid import uuid4

import pytest

from mentor.events.context_summary import ContextSummaryStore, PendingSummary

DSN = os.environ.get("RUN_EVENTS_POSTGRES_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="需要 RUN_EVENTS_POSTGRES_DSN")


def test_context_summary_store_roundtrip():
    store = ContextSummaryStore(DSN)
    thread_id = f"thread-{uuid4().hex}"
    try:
        assert store.try_claim(thread_id, "run-1", "user-a", 70000)
        assert not store.try_claim(thread_id, "run-2", "user-a", 70000)
        assert store.mark_ready(thread_id, "summary text", 70000)
        loaded = store.get(thread_id)
        assert loaded is not None
        assert loaded.summary == "summary text"
        assert loaded.token_before == 70000
        claimed = store.claim_ready(thread_id)
        assert claimed is not None and claimed.summary == "summary text"
        assert store.claim_ready(thread_id) is None
        assert store.get(thread_id) is None
    finally:
        store.delete(thread_id)
        store.close()
