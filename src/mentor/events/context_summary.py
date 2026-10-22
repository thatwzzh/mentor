"""跨轮预压缩摘要：回答结束后异步生成，下一轮由 middleware 注入。

不依赖 checkpoint 私有字段；摘要按 thread_id 覆盖，应用后删除。
"""

import threading
from datetime import datetime, timezone
from typing import Protocol

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from pydantic import BaseModel, Field

from mentor.config.event_settings import event_settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pending_context_summaries (
    thread_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL DEFAULT '',
    user_id TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL,
    token_before INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL
);
"""


class PendingSummary(BaseModel):
    """一条等待下一轮注入的预压缩摘要。"""

    thread_id: str
    run_id: str = ""
    user_id: str = ""
    summary: str
    token_before: int = 0
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class ContextSummaryStoreProtocol(Protocol):
    def upsert(self, summary: PendingSummary) -> None: ...
    def get(self, thread_id: str) -> PendingSummary | None: ...
    def delete(self, thread_id: str) -> None: ...


class ContextSummaryStore:
    """Postgres 预压缩摘要表。"""

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

    def upsert(self, summary: PendingSummary) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                """
                INSERT INTO pending_context_summaries
                    (thread_id, run_id, user_id, summary, token_before, created_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (thread_id) DO UPDATE SET
                    run_id = EXCLUDED.run_id,
                    user_id = EXCLUDED.user_id,
                    summary = EXCLUDED.summary,
                    token_before = EXCLUDED.token_before,
                    created_at = EXCLUDED.created_at
                """,
                (
                    summary.thread_id,
                    summary.run_id,
                    summary.user_id,
                    summary.summary,
                    summary.token_before,
                    summary.created_at,
                ),
            )
            conn.commit()

    def get(self, thread_id: str) -> PendingSummary | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT * FROM pending_context_summaries WHERE thread_id = %s",
                (thread_id,),
            ).fetchone()
        return PendingSummary.model_validate(dict(row)) if row else None

    def delete(self, thread_id: str) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                "DELETE FROM pending_context_summaries WHERE thread_id = %s",
                (thread_id,),
            )
            conn.commit()

    def close(self) -> None:
        self._pool.close()


class InMemoryContextSummaryStore:
    """测试/本地用预压缩摘要表。"""

    def __init__(self) -> None:
        self._items: dict[str, PendingSummary] = {}
        self._lock = threading.Lock()

    def upsert(self, summary: PendingSummary) -> None:
        with self._lock:
            self._items[summary.thread_id] = summary

    def get(self, thread_id: str) -> PendingSummary | None:
        with self._lock:
            return self._items.get(thread_id)

    def delete(self, thread_id: str) -> None:
        with self._lock:
            self._items.pop(thread_id, None)


_store: ContextSummaryStore | None = None
_store_lock = threading.Lock()


def get_context_summary_store() -> ContextSummaryStore:
    global _store
    with _store_lock:
        if _store is None:
            _store = ContextSummaryStore()
        return _store
