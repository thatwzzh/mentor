"""Run 事件 outbox：Postgres 写入失败时落本地 JSONL，供 mentor events replay。"""

import logging
import re
import uuid
from pathlib import Path

from mentor.config.event_settings import event_settings
from mentor.events.models import RunEvent
from mentor.events.store import RunEventStoreProtocol, get_run_event_store

logger = logging.getLogger(__name__)


def _root() -> Path:
    return Path(event_settings.outbox_dir)


def spill_event(event: RunEvent) -> Path:
    """把单条事件写入 outbox；文件名带随机后缀，避免并发覆盖。"""
    root = _root()
    if not root.is_dir():
        root.mkdir(parents=True, exist_ok=True)
    safe_run = re.sub(r"[^A-Za-z0-9._-]", "-", event.run_id)[:80] or "run"
    path = root / f"{safe_run}-{uuid.uuid4().hex}.jsonl"
    path.write_text(event.model_dump_json() + "\n", encoding="utf-8")
    return path


def replay_outbox(
    store: RunEventStoreProtocol | None = None, *, delete: bool = True
) -> int:
    """把 outbox 事件重新写回 Postgres；成功文件删除，失败保留。"""
    root = _root()
    if not root.is_dir():
        return 0
    target = store or get_run_event_store()
    replayed = 0
    for path in sorted(root.glob("*.jsonl")):
        try:
            events = [
                RunEvent.model_validate_json(line)
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            if events:
                target.append_many(events)
                replayed += len(events)
            if delete:
                path.unlink(missing_ok=True)
        except Exception as exc:
            logger.warning("outbox 重放失败 file=%s: %s", path, exc)
    return replayed
