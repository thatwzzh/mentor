"""审批表 Postgres 集成验收；未提供 RUN_EVENTS_POSTGRES_DSN 时跳过。"""

import os
from uuid import uuid4

import pytest

from mentor.events.approvals import ApprovalEvent, ApprovalStore

DSN = os.environ.get("RUN_EVENTS_POSTGRES_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="需要 RUN_EVENTS_POSTGRES_DSN")


def test_approval_store_roundtrip_and_count():
    store = ApprovalStore(DSN)
    run_id = f"approval-{uuid4().hex}"
    try:
        first = store.append(
            ApprovalEvent(
                run_id=run_id,
                user_id="user-a",
                tool="deploy",
                args_hash="h1",
                decision="requested",
                reason="deploy",
            )
        )
        assert first.id > 0
        store.append(
            ApprovalEvent(
                run_id=run_id,
                user_id="user-a",
                tool="deploy",
                args_hash="h1",
                decision="allow",
                reason="deploy",
            )
        )
        assert store.count(run_id) == 1
        listed = store.list(run_id, user_id="user-a")
        assert [event.decision for event in listed] == ["requested", "allow"]
        assert store.list(run_id, user_id="user-b") == []
    finally:
        store.clear(run_id)
        store.close()
