"""Run 事件与投影：事件表、前端契约和 checkpoint 修剪。"""

from mentor.events.context import runtime_ids
from mentor.events.models import RunEvent, RunSummary
from mentor.events.projection import (
    ProjectedState,
    build_summary,
    project_messages_to_events,
    project_state,
)
from mentor.events.store import (
    InMemoryRunEventStore,
    RunEventStore,
    get_run_event_store,
)
from mentor.events.writer import append_event, append_events

__all__ = [
    "ProjectedState",
    "RunEvent",
    "InMemoryRunEventStore",
    "RunEventStore",
    "RunSummary",
    "append_event",
    "append_events",
    "build_summary",
    "get_run_event_store",
    "project_messages_to_events",
    "project_state",
    "runtime_ids",
]
