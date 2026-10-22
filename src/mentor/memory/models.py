"""长记忆记录模型。"""

from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field

MemoryKind = Literal["preference", "fact", "decision", "todo", "artifact"]


class MemoryRecord(BaseModel):
    """一条结构化长记忆。"""

    id: str = Field(default_factory=lambda: uuid4().hex)
    tenant_id: str
    user_id: str
    kind: MemoryKind = "fact"
    key: str
    content: str
    confidence: float = 0.8
    source_thread_id: str = ""
    pinned: bool = False
    embedding: list[float] | None = None
    last_used_at: datetime | None = None
    expires_at: datetime | None = None
    superseded_by: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
