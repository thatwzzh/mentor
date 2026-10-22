"""5.10 记忆：写入/检索/supersede/租户隔离/删除/注入预算。"""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, cast

from langchain_core.messages import HumanMessage, SystemMessage

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
