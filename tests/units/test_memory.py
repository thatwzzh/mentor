"""5.10 记忆：写入/检索/supersede/租户隔离/删除/注入预算。"""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, cast

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from mentor.config.memory_settings import memory_settings
from mentor.memory.models import MemoryRecord
from mentor.memory.store import InMemoryMemoryStore
from mentor.middlewares.memory import MemoryMiddleware, _format_block
from mentor.tools.memory import memory_forget, memory_search, memory_write


def _record(user: str, key: str, content: str, tenant: str = "tenant-a", **kwargs):
    return MemoryRecord(
        tenant_id=tenant,
        user_id=user,
        kind=kwargs.pop("kind", "fact"),
        key=key,
        content=content,
        **kwargs,
    )


def test_memory_store_search_supersede_isolation_and_forget():
    store = InMemoryMemoryStore()
    store.write(_record("user-a", "editor", "用户偏好 Vim"))
    store.write(_record("user-b", "editor", "用户偏好 VSCode"))
    assert store.search(
        tenant_id="tenant-a", user_id="user-a", query="Vim"
    )[0].content == "用户偏好 Vim"
    assert store.search(
        tenant_id="tenant-a", user_id="user-b", query="Vim"
    ) == []
    assert store.search(
        tenant_id="tenant-b", user_id="user-a", query="Vim"
    ) == []

    store.write(_record("user-a", "editor", "用户偏好 Neovim", confidence=0.9))
    results = store.search(
        tenant_id="tenant-a", user_id="user-a", query="Neovim"
    )
    assert results[0].content == "用户偏好 Neovim"
    assert all("Vim" != item.content for item in results)

    assert store.forget(tenant_id="tenant-a", user_id="user-a", key="editor") == 2
    assert store.search(
        tenant_id="tenant-a", user_id="user-a", query="Neovim"
    ) == []


def test_memory_store_expiry_and_pinned():
    store = InMemoryMemoryStore()
    store.write(
        _record(
            "user-a",
            "expired",
            "旧事实",
            expires_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
    )
    store.write(_record("user-a", "pinned", "必须保留", pinned=True))
    results = store.search(
        tenant_id="tenant-a", user_id="user-a", query="无关"
    )
    assert [item.key for item in results] == ["pinned"]


def test_memory_block_respects_token_budget():
    records = [
        _record("user-a", f"k{i}", "x" * 400) for i in range(10)
    ]
    block = _format_block(records, 200)
    assert "k0" in block
    assert "k9" not in block


def test_memory_middleware_injects_scoped_memory(monkeypatch):
    store = InMemoryMemoryStore()
    store.write(_record("user-a", "editor", "用户偏好 Vim"))
    middleware = MemoryMiddleware(store=store)
    monkeypatch.setattr(
        "mentor.middlewares.memory.runtime_ids",
        lambda: ("run-1", "thread-1", "user-a"),
    )
    monkeypatch.setattr(memory_settings, "tenant", "tenant-a")

    captured = {}

    async def handler(request):
        captured["system"] = request.system_message
        return "ok"

    request = cast(
        Any,
        SimpleNamespace(
            messages=[HumanMessage(content="Vim")],
            system_message=SystemMessage(content="base"),
            override=lambda **kwargs: SimpleNamespace(**kwargs),
        ),
    )
    asyncio.run(middleware.awrap_model_call(request, handler))
    assert "Long-term memory" in captured["system"].content
    assert "Vim" in captured["system"].content


def test_memory_tools_use_identity(monkeypatch):
    store = InMemoryMemoryStore()
    monkeypatch.setattr("mentor.tools.memory.get_memory_store", lambda: store)
    monkeypatch.setattr(
        "mentor.tools.memory.runtime_ids",
        lambda: ("run-1", "thread-1", "user-a"),
    )
    monkeypatch.setattr(memory_settings, "tenant", "tenant-a")
    asyncio.run(
        memory_write.ainvoke(
            {"kind": "preference", "key": "editor", "content": "Vim"}
        )
    )
    results = asyncio.run(memory_search.ainvoke({"query": "Vim"}))
    assert "Vim" in results
    deleted = asyncio.run(memory_forget.ainvoke({"key": "editor"}))
    assert '"deleted": 1' in deleted


def test_main_agent_gets_memory_tools():
    from mentor.agents.main import main_agent_factory
    from mentor.config.nacos.nacos_schemas import AgentSpec

    bundle = asyncio.run(
        main_agent_factory._get_tools(AgentSpec(name="main_agent"))
    )
    names = {tool.name for tool in bundle.direct_tools}
    assert {"memory_search", "memory_write", "memory_forget"} <= names


def test_tenant_claim_preferred_over_fallback(monkeypatch):
    from mentor.utils import shared

    monkeypatch.setattr(
        shared,
        "get_config",
        lambda: {
            "configurable": {
                "langgraph_auth_user": {"tenant_id": "tenant-claim"}
            }
        },
    )
    assert shared.get_tenant_id() == "tenant-claim"


def test_memory_cleanup_removes_expired_and_old_superseded():
    store = InMemoryMemoryStore()
    store.write(
        _record(
            "user-a",
            "expired",
            "过期",
            expires_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
    )
    old = _record("user-a", "old", "旧值")
    old.created_at = datetime.now(timezone.utc) - timedelta(days=60)
    store.write(old)
    store.write(_record("user-a", "old", "新值"))
    assert store.cleanup_expired(retention_days=30) == 2
    assert store.search(
        tenant_id="tenant-a", user_id="user-a", query="新值"
    )[0].content == "新值"


def test_async_memory_extraction_and_sensitive_skip(monkeypatch):
    from mentor.memory.extraction import MemoryExtractor

    class FakeModel:
        async def ainvoke(self, messages):
            return AIMessage(content=(
                '[{"kind":"preference","key":"editor","content":"用户偏好 Vim","confidence":0.9},'
                '{"kind":"fact","key":"secret","content":"api_key=sk-1234567890","confidence":0.9}]'
            ))

    store = InMemoryMemoryStore()
    extractor = MemoryExtractor(FakeModel(), store=store)
    monkeypatch.setattr(
        "mentor.memory.extraction.runtime_ids",
        lambda: ("run-1", "thread-1", "user-a"),
    )
    monkeypatch.setattr(
        "mentor.memory.extraction.get_tenant_id", lambda: "tenant-a"
    )
    messages = [
        HumanMessage(content="以后都用 Vim，另外 api_key=sk-1234567890"),
        AIMessage(content="好的"),
    ]
    assert asyncio.run(extractor.extract(messages)) == 1
    records = store.search(
        tenant_id="tenant-a", user_id="user-a", query="Vim"
    )
    assert records[0].content == "用户偏好 Vim"
    assert all("sk-" not in item.content for item in store._items)


def test_inmemory_vector_recall_without_keyword_overlap():
    store = InMemoryMemoryStore()
    store.write(
        _record(
            "user-a",
            "editor",
            "用户偏好 Vim",
            embedding=[1.0, 0.0, 0.0],
        )
    )
    results = store.search(
        tenant_id="tenant-a",
        user_id="user-a",
        query="完全不同的词",
        query_embedding=[0.98, 0.1, 0.0],
    )
    assert results and results[0].key == "editor"


def test_memory_embedder_is_disabled_without_model():
    from mentor.memory.embedding import MemoryEmbedder

    embedder = MemoryEmbedder()
    assert embedder.enabled is False
    assert asyncio.run(embedder.embed("anything")) is None
