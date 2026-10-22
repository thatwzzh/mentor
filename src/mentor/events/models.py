"""Run 事件模型：前端卡片与审计共用的唯一契约。"""

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

RunEventKind = Literal[
    "run_started",
    "turn_started",
    "model_request",
    "model_response",
    "tool_call",
    "tool_result",
    "approval",
    "artifact",
    "message",
    "usage",
    "compression",
    "run_finished",
    "summary",
]
RunEventRole = Literal["user", "assistant", "tool", "system", "event"]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class RunEvent(BaseModel):
    """一条可重放事件；seq 在单个 run 内单调递增。"""

    run_id: str
    thread_id: str = ""
    user_id: str = ""
    schema_version: str = "1"
    seq: int = 0
    event_key: str
    kind: RunEventKind
    role: RunEventRole | None = None
    name: str | None = None
    content: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class RunSummary(BaseModel):
    """GET /runs/{run_id}/summary 的响应；前端只依赖这套字段。"""

    contract_version: str = "1"
    run_id: str
    thread_id: str = ""
    user_id: str = ""
    events: list[RunEvent]
    summary: list[dict[str, Any]]
    artifacts: list[str]
    token_usage: dict[str, int]
    compression: list[dict[str, Any]]
    status: Literal["unknown", "running", "finished", "incomplete"] = "unknown"
    last_completed_tool: dict[str, Any] | None = None
    pending_tools: list[dict[str, Any]] = Field(default_factory=list)
