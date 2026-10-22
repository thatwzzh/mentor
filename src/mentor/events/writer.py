"""Run 事件写入器：业务路径只调用这里，存储失败不阻断 run。"""

import logging
from collections.abc import Callable

from mentor.events.models import RunEvent
from mentor.events.outbox import spill_event
from mentor.events.store import get_run_event_store

logger = logging.getLogger(__name__)

EventWriter = Callable[[RunEvent], None]


def append_event(event: RunEvent) -> None:
    """写单条事件；失败只告警，工具/模型路径继续执行。"""
    try:
        get_run_event_store().append_many([event])
    except Exception as exc:
        try:
            path = spill_event(event)
            logger.warning(
                "run_events 写入失败 run_id=%s kind=%s key=%s: %s; 已落 outbox=%s",
                event.run_id,
                event.kind,
                event.event_key,
                exc,
                path,
            )
        except Exception as spill_exc:
            logger.warning(
                "run_events 写入失败且 outbox 落盘失败 run_id=%s kind=%s: %s / %s",
                event.run_id,
                event.kind,
                exc,
                spill_exc,
            )


def append_events(events: list[RunEvent]) -> None:
    for event in events:
        append_event(event)
