"""长记忆工具：search / write / forget。"""

import json
from datetime import datetime, timedelta, timezone
from typing import cast

from langchain.tools import tool

from mentor.config.memory_settings import memory_settings
from mentor.events.context import runtime_ids
from mentor.memory.models import MemoryKind, MemoryRecord
from mentor.memory.store import get_memory_store
from mentor.tools.specs import register_tool


def _identity() -> tuple[str, str, str]:
    _, thread_id, user_id = runtime_ids()
    return memory_settings.tenant, user_id, thread_id


@register_tool(risk="read", timeout_s=10)
@tool
async def memory_search(query: str, limit: int = 5) -> str:
    """按当前用户/租户检索长记忆；返回 key、kind、content、confidence。"""
    tenant_id, user_id, _ = _identity()
    records = get_memory_store().search(
        tenant_id=tenant_id,
        user_id=user_id,
        query=query,
        limit=min(max(limit, 1), memory_settings.search_limit),
    )
    return json.dumps(
        [
            {
                "key": record.key,
                "kind": record.kind,
                "content": record.content,
                "confidence": record.confidence,
                "pinned": record.pinned,
            }
            for record in records
        ],
        ensure_ascii=False,
    )


@register_tool(risk="write", timeout_s=10)
@tool
async def memory_write(
    kind: str,
    key: str,
    content: str,
    confidence: float = 0.8,
    pinned: bool = False,
    expires_in_days: int | None = None,
) -> str:
    """显式写入一条长记忆；同 key 新值会 supersede 旧值。"""
    tenant_id, user_id, thread_id = _identity()
    expires_at = (
        datetime.now(timezone.utc) + timedelta(days=expires_in_days)
        if expires_in_days and expires_in_days > 0
        else None
    )
    kind_value: MemoryKind = cast(
        MemoryKind,
        kind if kind in {"preference", "fact", "decision", "todo", "artifact"} else "fact",
    )
    record = MemoryRecord(
        tenant_id=tenant_id,
        user_id=user_id,
        kind=kind_value,
        key=key,
        content=content,
        confidence=max(0.0, min(confidence, 1.0)),
        source_thread_id=thread_id,
        pinned=pinned,
        expires_at=expires_at,
    )
    get_memory_store().write(record)
    return json.dumps({"ok": True, "key": record.key, "id": record.id}, ensure_ascii=False)


@register_tool(risk="destructive", timeout_s=10)
@tool
async def memory_forget(key: str) -> str:
    """删除当前用户/租户下某个 key 的所有长记忆；删除后不可检索。"""
    tenant_id, user_id, _ = _identity()
    count = get_memory_store().forget(
        tenant_id=tenant_id, user_id=user_id, key=key
    )
    return json.dumps({"ok": True, "key": key, "deleted": count}, ensure_ascii=False)


def memory_tool_specs():
    from mentor.tools.specs import app_specs

    specs = app_specs()
    return [
        spec
        for name in ("memory_search", "memory_write", "memory_forget")
        if (spec := specs.get(name)) is not None
    ]
