"""长记忆 Postgres 集成：supersede、检索、隔离、删除。"""

import os
from uuid import uuid4

import pytest

from mentor.memory.models import MemoryRecord
from mentor.memory.store import MemoryStore

DSN = os.environ.get("RUN_EVENTS_POSTGRES_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="需要 RUN_EVENTS_POSTGRES_DSN")


def test_memory_store_roundtrip_and_supersede():
    store = MemoryStore(DSN)
    user = f"user-{uuid4().hex}"
    try:
        old = store.write(
            MemoryRecord(
                tenant_id="tenant-a",
                user_id=user,
                key="editor",
                content="用户偏好 Vim",
            )
        )
        found = store.search(
            tenant_id="tenant-a", user_id=user, query="Vim"
        )
        assert found and found[0].id == old.id

        new = store.write(
            MemoryRecord(
                tenant_id="tenant-a",
                user_id=user,
                key="editor",
                content="用户偏好 Neovim",
            )
        )
        found = store.search(
            tenant_id="tenant-a", user_id=user, query="Neovim"
        )
        assert found and found[0].id == new.id
        assert all(item.id != old.id for item in found)
        assert store.search(
            tenant_id="tenant-b", user_id=user, query="Neovim"
        ) == []
        assert store.forget(tenant_id="tenant-a", user_id=user, key="editor") == 2
        assert store.search(
            tenant_id="tenant-a", user_id=user, query="Neovim"
        ) == []
    finally:
        store.clear(tenant_id="tenant-a", user_id=user)
        store.close()
