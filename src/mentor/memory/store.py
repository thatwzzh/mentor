"""长记忆存储：Postgres 主实现 + InMemory 测试实现。

当前环境没有 pgvector，检索用关键词 + confidence + recency；embedding 字段
预留，后续接 pgvector 时不改上层协议。
"""

import re
import threading
from datetime import datetime, timezone
from typing import Protocol

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from mentor.config.event_settings import event_settings
from mentor.memory.models import MemoryRecord

_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_memories (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    key TEXT NOT NULL,
    content TEXT NOT NULL,
    confidence DOUBLE PRECISION NOT NULL DEFAULT 0.8,
    source_thread_id TEXT NOT NULL DEFAULT '',
    pinned BOOLEAN NOT NULL DEFAULT FALSE,
    embedding JSONB,
    last_used_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ,
    superseded_by TEXT,
    created_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_memories_scope
    ON agent_memories (tenant_id, user_id, kind, key);
CREATE INDEX IF NOT EXISTS idx_agent_memories_active
    ON agent_memories (tenant_id, user_id, superseded_by, expires_at);
"""


class MemoryStoreProtocol(Protocol):
    def write(self, record: MemoryRecord) -> MemoryRecord: ...
    def search(
        self, *, tenant_id: str, user_id: str, query: str, limit: int = 5
    ) -> list[MemoryRecord]: ...
    def forget(self, *, tenant_id: str, user_id: str, key: str) -> int: ...
    def clear(self, *, tenant_id: str, user_id: str) -> None: ...


def _terms(query: str) -> list[str]:
    return [term.lower() for term in re.findall(r"[\w\u4e00-\u9fff]+", query) if term]


class MemoryStore:
    """Postgres 长记忆表；同 key 新值会 supersede 旧值。"""

    def __init__(self, dsn: str | None = None) -> None:
        self.dsn = dsn or event_settings.dsn
        self._pool = ConnectionPool(
            conninfo=self.dsn,
            min_size=1,
            max_size=5,
            open=False,
            kwargs={"row_factory": dict_row},
        )
        self._pool.open()
        with self._pool.connection() as conn:
            conn.execute(_SCHEMA)
            conn.commit()

    def write(self, record: MemoryRecord) -> MemoryRecord:
        with self._pool.connection() as conn:
            conn.execute(
                """
                UPDATE agent_memories
                SET superseded_by = %s
                WHERE tenant_id = %s AND user_id = %s
                  AND kind = %s AND key = %s AND superseded_by IS NULL
                """,
                (
                    record.id,
                    record.tenant_id,
                    record.user_id,
                    record.kind,
                    record.key,
                ),
            )
            conn.execute(
                """
                INSERT INTO agent_memories
                    (id, tenant_id, user_id, kind, key, content, confidence,
                     source_thread_id, pinned, embedding, last_used_at,
                     expires_at, superseded_by, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NULL, %s)
                """,
                (
                    record.id,
                    record.tenant_id,
                    record.user_id,
                    record.kind,
                    record.key,
                    record.content,
                    record.confidence,
                    record.source_thread_id,
                    record.pinned,
                    record.embedding,
                    record.last_used_at,
                    record.expires_at,
                    record.created_at,
                ),
            )
            conn.commit()
        return record

    def search(
        self, *, tenant_id: str, user_id: str, query: str, limit: int = 5
    ) -> list[MemoryRecord]:
        now = datetime.now(timezone.utc)
        terms = _terms(query)
        patterns = [f"%{term}%" for term in terms]
        with self._pool.connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM agent_memories
                WHERE tenant_id = %s AND user_id = %s
                  AND superseded_by IS NULL
                  AND (expires_at IS NULL OR expires_at > %s)
                  AND (
                    pinned = TRUE
                    OR cardinality(%s::text[]) = 0
                    OR content ILIKE ANY(%s::text[])
                    OR key ILIKE ANY(%s::text[])
                  )
                ORDER BY pinned DESC, confidence DESC, created_at DESC
                LIMIT 50
                """,
                (tenant_id, user_id, now, terms, patterns, patterns),
            ).fetchall()
            records = [MemoryRecord.model_validate(dict(row)) for row in rows]

        def score(record: MemoryRecord) -> float:
            text = f"{record.key} {record.content}".lower()
            overlap = sum(1 for term in terms if term in text)
            recency = 0.0
            if record.created_at:
                age_days = max(
                    (now - record.created_at).total_seconds() / 86400, 0
                )
                recency = max(0.0, 0.1 * (1 - min(age_days, 30) / 30))
            return (
                (1.0 if record.pinned else 0.0)
                + record.confidence
                + overlap * 0.2
                + recency
            )

        records.sort(key=score, reverse=True)
        result = records[:limit]
        if result:
            with self._pool.connection() as conn:
                conn.execute(
                    "UPDATE agent_memories SET last_used_at = %s WHERE id = ANY(%s)",
                    (now, [record.id for record in result]),
                )
                conn.commit()
        return result

    def forget(self, *, tenant_id: str, user_id: str, key: str) -> int:
        with self._pool.connection() as conn:
            cursor = conn.execute(
                """
                DELETE FROM agent_memories
                WHERE tenant_id = %s AND user_id = %s AND key = %s
                """,
                (tenant_id, user_id, key),
            )
            conn.commit()
        return cursor.rowcount

    def clear(self, *, tenant_id: str, user_id: str) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                "DELETE FROM agent_memories WHERE tenant_id = %s AND user_id = %s",
                (tenant_id, user_id),
            )
            conn.commit()

    def close(self) -> None:
        self._pool.close()


class InMemoryMemoryStore:
    """测试/本地用长记忆。"""

    def __init__(self) -> None:
        self._items: list[MemoryRecord] = []
        self._lock = threading.Lock()

    def write(self, record: MemoryRecord) -> MemoryRecord:
        with self._lock:
            for item in self._items:
                if (
                    item.tenant_id == record.tenant_id
                    and item.user_id == record.user_id
                    and item.kind == record.kind
                    and item.key == record.key
                    and item.superseded_by is None
                ):
                    item.superseded_by = record.id
            self._items.append(record)
            return record

    def search(
        self, *, tenant_id: str, user_id: str, query: str, limit: int = 5
    ) -> list[MemoryRecord]:
        now = datetime.now(timezone.utc)
        terms = _terms(query)
        with self._lock:
            active = [
                item
                for item in self._items
                if item.tenant_id == tenant_id
                and item.user_id == user_id
                and item.superseded_by is None
                and (item.expires_at is None or item.expires_at > now)
            ]
            matched = [
                item
                for item in active
                if item.pinned
                or not terms
                or any(
                    term in f"{item.key} {item.content}".lower() for term in terms
                )
            ]
            matched.sort(
                key=lambda item: (
                    item.pinned,
                    item.confidence,
                    item.created_at,
                ),
                reverse=True,
            )
            result = matched[:limit]
            for item in result:
                item.last_used_at = now
            return result

    def forget(self, *, tenant_id: str, user_id: str, key: str) -> int:
        with self._lock:
            before = len(self._items)
            self._items = [
                item
                for item in self._items
                if not (
                    item.tenant_id == tenant_id
                    and item.user_id == user_id
                    and item.key == key
                )
            ]
            return before - len(self._items)

    def clear(self, *, tenant_id: str, user_id: str) -> None:
        with self._lock:
            self._items = [
                item
                for item in self._items
                if not (item.tenant_id == tenant_id and item.user_id == user_id)
            ]


_store: MemoryStore | None = None
_lock = threading.Lock()


def get_memory_store() -> MemoryStore:
    global _store
    with _lock:
        if _store is None:
            _store = MemoryStore()
        return _store
