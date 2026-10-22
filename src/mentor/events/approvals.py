"""审批审计表：approval_events（Postgres / InMemory）。

一条 prompt 审批会先写 requested，再写最终 allow/deny；单 run 审批次数用
本表计数，避免进程内状态在重启后丢失。
"""

import threading
from datetime import datetime, timezone
from typing import Literal, Protocol

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from pydantic import BaseModel, Field

from mentor.config.event_settings import event_settings

ApprovalDecision = Literal["requested", "allow", "deny"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS approval_events (
    id BIGSERIAL PRIMARY KEY,
    run_id TEXT NOT NULL,
    thread_id TEXT NOT NULL DEFAULT '',
    user_id TEXT NOT NULL DEFAULT '',
    tool TEXT NOT NULL,
    args_hash TEXT NOT NULL,
    decision TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_approval_events_run
    ON approval_events (run_id, created_at);
"""


class ApprovalEvent(BaseModel):
    """一条审批审计记录。"""

    id: int = 0
    run_id: str
    thread_id: str = ""
    user_id: str = ""
    tool: str
    args_hash: str
    decision: ApprovalDecision
    reason: str = ""
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class ApprovalStoreProtocol(Protocol):
    def append(self, event: ApprovalEvent) -> ApprovalEvent: ...
    def count(self, run_id: str) -> int: ...
    def list(
        self, run_id: str, *, user_id: str, limit: int = 200
    ) -> list[ApprovalEvent]: ...
    def clear(self, run_id: str) -> None: ...


class ApprovalStore:
    """Postgres 审批表；与 run_events 共用 DSN，连接池独立。"""

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

    def append(self, event: ApprovalEvent) -> ApprovalEvent:
        with self._pool.connection() as conn:
            row = conn.execute(
                """
                INSERT INTO approval_events
                    (run_id, thread_id, user_id, tool, args_hash, decision,
                     reason, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    event.run_id,
                    event.thread_id,
                    event.user_id,
                    event.tool,
                    event.args_hash,
                    event.decision,
                    event.reason,
                    event.created_at,
                ),
            ).fetchone()
        if row is None:
            raise RuntimeError("approval_events 写入失败")
        return event.model_copy(update={"id": int(row["id"])})

    def count(self, run_id: str) -> int:
        """统计已发起的 prompt 数（requested 行）。"""
        with self._pool.connection() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS count FROM approval_events
                WHERE run_id = %s AND decision = 'requested'
                """,
                (run_id,),
            ).fetchone()
        return int(row["count"]) if row else 0

    def list(
        self, run_id: str, *, user_id: str, limit: int = 200
    ) -> list[ApprovalEvent]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM approval_events
                WHERE run_id = %s AND user_id = %s
                ORDER BY id ASC LIMIT %s
                """,
                (run_id, user_id, limit),
            ).fetchall()
        return [ApprovalEvent.model_validate(dict(row)) for row in rows]

    def clear(self, run_id: str) -> None:
        with self._pool.connection() as conn:
            conn.execute("DELETE FROM approval_events WHERE run_id = %s", (run_id,))
            conn.commit()

    def close(self) -> None:
        self._pool.close()


class InMemoryApprovalStore:
    """测试/本地用审批表。"""

    def __init__(self) -> None:
        self._events: list[ApprovalEvent] = []
        self._lock = threading.Lock()

    def append(self, event: ApprovalEvent) -> ApprovalEvent:
        with self._lock:
            stored = event.model_copy(update={"id": len(self._events) + 1})
            self._events.append(stored)
            return stored

    def count(self, run_id: str) -> int:
        with self._lock:
            return sum(
                1
                for event in self._events
                if event.run_id == run_id and event.decision == "requested"
            )

    def list(
        self, run_id: str, *, user_id: str, limit: int = 200
    ) -> list[ApprovalEvent]:
        with self._lock:
            return [
                event
                for event in self._events
                if event.run_id == run_id and event.user_id == user_id
            ][:limit]

    def clear(self, run_id: str) -> None:
        with self._lock:
            self._events = [
                event for event in self._events if event.run_id != run_id
            ]

    def close(self) -> None:
        return None


_approval_store: ApprovalStore | None = None
_store_lock = threading.Lock()


def get_approval_store() -> ApprovalStore:
    global _approval_store
    with _store_lock:
        if _approval_store is None:
            _approval_store = ApprovalStore()
        return _approval_store
