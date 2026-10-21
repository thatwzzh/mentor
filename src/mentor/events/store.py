"""Run 事件表：Postgres 实现，按 (run_id, seq) 重放，按 user_id 做归属过滤。

事件契约与存储解耦；单元测试使用 InMemoryRunEventStore，生产使用 Postgres。
"""

import threading
from typing import Protocol

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from mentor.config.event_settings import event_settings
from mentor.events.models import RunEvent

_SCHEMA = """
CREATE TABLE IF NOT EXISTS run_events (
    run_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    event_key TEXT NOT NULL,
    thread_id TEXT NOT NULL DEFAULT '',
    user_id TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL,
    role TEXT,
    name TEXT,
    content TEXT,
    data JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (run_id, seq),
    UNIQUE (run_id, event_key)
);
CREATE TABLE IF NOT EXISTS run_event_seq (
    run_id TEXT PRIMARY KEY,
    seq INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_run_events_user
    ON run_events (run_id, user_id, seq);
"""


class RunEventStoreProtocol(Protocol):
    def append_many(self, events: list[RunEvent]) -> int: ...
    def list(
        self, run_id: str, *, user_id: str, after_seq: int = 0
    ) -> list[RunEvent]: ...
    def clear(self, run_id: str | None = None) -> None: ...
    def close(self) -> None: ...


class RunEventStore:
    """Postgres 事件表；连接池线程安全，可在中间件与 FastAPI 线程池共用。"""

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
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with self._pool.connection() as conn:
            conn.execute(_SCHEMA)
            conn.commit()

    def append_many(self, events: list[RunEvent]) -> int:
        """追加事件；重复 event_key 自动忽略。返回新写入条数。"""
        if not events:
            return 0
        written = 0
        with self._pool.connection() as conn:
            with conn.transaction():
                for event in events:
                    row = conn.execute(
                        """
                        INSERT INTO run_event_seq (run_id, seq)
                        VALUES (%s, 1)
                        ON CONFLICT (run_id)
                        DO UPDATE SET seq = run_event_seq.seq + 1
                        RETURNING seq
                        """,
                        (event.run_id,),
                    ).fetchone()
                    if row is None:
                        raise RuntimeError("无法为 run_events 分配 seq")
                    seq = int(row["seq"])
                    cursor = conn.execute(
                        """
                        INSERT INTO run_events
                            (run_id, seq, event_key, thread_id, user_id, kind, role,
                             name, content, data, created_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (run_id, event_key) DO NOTHING
                        """,
                        (
                            event.run_id,
                            seq,
                            event.event_key,
                            event.thread_id,
                            event.user_id,
                            event.kind,
                            event.role,
                            event.name,
                            event.content,
                            Jsonb(event.data),
                            event.created_at,
                        ),
                    )
                    written += cursor.rowcount
        return written

    def list(
        self, run_id: str, *, user_id: str, after_seq: int = 0
    ) -> list[RunEvent]:
        """按 seq 读取当前用户可见的事件。"""
        with self._pool.connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM run_events
                WHERE run_id = %s AND user_id = %s AND seq > %s
                ORDER BY seq ASC
                """,
                (run_id, user_id, after_seq),
            ).fetchall()
        return [
            RunEvent(
                run_id=row["run_id"],
                thread_id=row["thread_id"],
                user_id=row["user_id"],
                seq=row["seq"],
                event_key=row["event_key"],
                kind=row["kind"],
                role=row["role"],
                name=row["name"],
                content=row["content"],
                data=row["data"] or {},
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def clear(self, run_id: str | None = None) -> None:
        with self._pool.connection() as conn:
            with conn.transaction():
                if run_id is None:
                    conn.execute("DELETE FROM run_events")
                    conn.execute("DELETE FROM run_event_seq")
                else:
                    conn.execute(
                        "DELETE FROM run_events WHERE run_id = %s", (run_id,)
                    )
                    conn.execute(
                        "DELETE FROM run_event_seq WHERE run_id = %s", (run_id,)
                    )

    def close(self) -> None:
        self._pool.close()


class InMemoryRunEventStore:
    """测试/本地演示用的事件表；不依赖 Postgres，语义与 Postgres 版一致。"""

    def __init__(self) -> None:
        self._events: dict[str, list[RunEvent]] = {}
        self._keys: set[tuple[str, str]] = set()
        self._lock = threading.Lock()

    def append_many(self, events: list[RunEvent]) -> int:
        written = 0
        with self._lock:
            for event in events:
                key = (event.run_id, event.event_key)
                if key in self._keys:
                    continue
                seq = len(self._events.get(event.run_id, [])) + 1
                stored = event.model_copy(update={"seq": seq})
                self._events.setdefault(event.run_id, []).append(stored)
                self._keys.add(key)
                written += 1
        return written

    def list(
        self, run_id: str, *, user_id: str, after_seq: int = 0
    ) -> list[RunEvent]:
        with self._lock:
            events = list(self._events.get(run_id, []))
        return [
            event
            for event in events
            if event.user_id == user_id and event.seq > after_seq
        ]

    def clear(self, run_id: str | None = None) -> None:
        with self._lock:
            if run_id is None:
                self._events.clear()
                self._keys.clear()
            else:
                self._events.pop(run_id, None)
                self._keys = {key for key in self._keys if key[0] != run_id}

    def close(self) -> None:
        return None


_event_store: RunEventStore | None = None
_store_lock = threading.Lock()


def get_run_event_store() -> RunEventStore:
    global _event_store
    with _store_lock:
        if _event_store is None:
            _event_store = RunEventStore()
        return _event_store
