"""跨轮预压缩摘要：回答结束后异步生成，下一轮原子领取并注入。

关键并发语义：
- `try_claim` 在 Postgres 里 INSERT ON CONFLICT，只允许一个 run 启动摘要任务。
- `mark_ready` 只在 generating 状态写回摘要。
- `claim_ready` 用 DELETE ... RETURNING 原子领取，多个并发 run 只有一个注入。
"""

import threading
from datetime import datetime, timedelta, timezone
from typing import Literal, Protocol

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from pydantic import BaseModel, Field

from mentor.config.event_settings import event_settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pending_context_summaries (
    thread_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL DEFAULT '',
    user_id TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    token_before INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'ready',
    created_at TIMESTAMPTZ NOT NULL
);
ALTER TABLE pending_context_summaries
    ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'ready';
"""


class PendingSummary(BaseModel):
    """一条等待下一轮注入的预压缩摘要。"""

    thread_id: str
    run_id: str = ""
    user_id: str = ""
    summary: str = ""
    token_before: int = 0
    status: Literal["generating", "ready"] = "ready"
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class ContextSummaryStoreProtocol(Protocol):
    def upsert(self, summary: PendingSummary) -> None: ...
    def get(self, thread_id: str) -> PendingSummary | None: ...
    def try_claim(
        self,
        thread_id: str,
        run_id: str,
        user_id: str,
        token_before: int,
    ) -> bool: ...
    def mark_ready(
        self, thread_id: str, summary: str, token_before: int
    ) -> bool: ...
    def claim_ready(self, thread_id: str) -> PendingSummary | None: ...
    def delete(self, thread_id: str) -> None: ...


class ContextSummaryStore:
    """Postgres 预压缩摘要表；claim 操作是跨进程原子的。"""

    claim_timeout_seconds = 600

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
                    (thread_id, run_id, user_id, summary, token_before,
                     status, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (thread_id) DO UPDATE SET
                    run_id = EXCLUDED.run_id,
                    user_id = EXCLUDED.user_id,
                    summary = EXCLUDED.summary,
                    token_before = EXCLUDED.token_before,
                    status = EXCLUDED.status,
                    created_at = EXCLUDED.created_at
                """,
                (
                    summary.thread_id,
                    summary.run_id,
                    summary.user_id,
                    summary.summary,
                    summary.token_before,
                    summary.status,
                    summary.created_at,
                ),
            )
            conn.commit()

    def get(self, thread_id: str) -> PendingSummary | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM pending_context_summaries
                WHERE thread_id = %s AND status = 'ready'
                """,
                (thread_id,),
            ).fetchone()
        return PendingSummary.model_validate(dict(row)) if row else None

    def try_claim(
        self,
        thread_id: str,
        run_id: str,
        user_id: str,
        token_before: int,
    ) -> bool:
        """原子占位；已有 ready/generating 行时返回 False。"""
        stale_before = datetime.now(timezone.utc) - timedelta(
            seconds=self.claim_timeout_seconds
        )
        with self._pool.connection() as conn:
            conn.execute(
                """
                DELETE FROM pending_context_summaries
                WHERE thread_id = %s AND status = 'generating'
                  AND created_at < %s
                """,
                (thread_id, stale_before),
            )
            cursor = conn.execute(
                """
                INSERT INTO pending_context_summaries
                    (thread_id, run_id, user_id, summary, token_before,
                     status, created_at)
                VALUES (%s, %s, %s, '', %s, 'generating', %s)
                ON CONFLICT (thread_id) DO NOTHING
                """,
                (
                    thread_id,
                    run_id,
                    user_id,
                    token_before,
                    datetime.now(timezone.utc),
                ),
            )
            conn.commit()
        return cursor.rowcount > 0

    def mark_ready(
        self, thread_id: str, summary: str, token_before: int
    ) -> bool:
        with self._pool.connection() as conn:
            cursor = conn.execute(
                """
                UPDATE pending_context_summaries
                SET summary = %s, token_before = %s, status = 'ready',
                    created_at = %s
                WHERE thread_id = %s AND status = 'generating'
                """,
                (summary, token_before, datetime.now(timezone.utc), thread_id),
            )
            conn.commit()
        return cursor.rowcount > 0

    def claim_ready(self, thread_id: str) -> PendingSummary | None:
        """原子领取 ready 摘要；并发 run 只有一个能拿到。"""
        with self._pool.connection() as conn:
            row = conn.execute(
                """
                DELETE FROM pending_context_summaries
                WHERE thread_id = %s AND status = 'ready'
                RETURNING *
                """,
                (thread_id,),
            ).fetchone()
            conn.commit()
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
    """测试/本地用预压缩摘要表；用锁模拟原子 claim。"""

    def __init__(self) -> None:
        self._items: dict[str, PendingSummary] = {}
        self._lock = threading.Lock()

    def upsert(self, summary: PendingSummary) -> None:
        with self._lock:
            self._items[summary.thread_id] = summary

    def get(self, thread_id: str) -> PendingSummary | None:
        with self._lock:
            item = self._items.get(thread_id)
            return item if item is not None and item.status == "ready" else None

    def try_claim(
        self,
        thread_id: str,
        run_id: str,
        user_id: str,
        token_before: int,
    ) -> bool:
        with self._lock:
            if thread_id in self._items:
                return False
            self._items[thread_id] = PendingSummary(
                thread_id=thread_id,
                run_id=run_id,
                user_id=user_id,
                token_before=token_before,
                status="generating",
            )
            return True

    def mark_ready(
        self, thread_id: str, summary: str, token_before: int
    ) -> bool:
        with self._lock:
            item = self._items.get(thread_id)
            if item is None or item.status != "generating":
                return False
            self._items[thread_id] = item.model_copy(
                update={
                    "summary": summary,
                    "token_before": token_before,
                    "status": "ready",
                    "created_at": datetime.now(timezone.utc),
                }
            )
            return True

    def claim_ready(self, thread_id: str) -> PendingSummary | None:
        with self._lock:
            item = self._items.get(thread_id)
            if item is None or item.status != "ready":
                return None
            return self._items.pop(thread_id)

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
