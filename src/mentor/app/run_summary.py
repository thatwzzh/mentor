"""按 run 读取投影：session summary / run trace / usage。"""

import asyncio

from fastapi import APIRouter, Header, HTTPException, Query

from mentor.app.auth import require_user
from mentor.events.models import RunEvent
from mentor.events.projection import build_summary
from mentor.events.store import get_run_event_store

router = APIRouter()


async def _events(
    run_id: str, user_id: str, after_seq: int = 0
) -> list[RunEvent]:
    store = get_run_event_store()
    return await asyncio.to_thread(
        store.list, run_id, user_id=user_id, after_seq=after_seq
    )


@router.get("/runs/{run_id}/summary")
async def get_run_summary(
    run_id: str,
    authorization: str | None = Header(default=None),
    after_seq: int = Query(default=0, ge=0),
):
    """session_summary：按 seq 返回当前用户的 run 事件与状态。"""
    user_id = await require_user(authorization)
    events = await _events(run_id, user_id, after_seq)
    if not events:
        raise HTTPException(status_code=404, detail="run summary not found")
    return build_summary(events)


@router.get("/runs/{run_id}/trace")
async def get_run_trace(
    run_id: str,
    authorization: str | None = Header(default=None),
    after_seq: int = Query(default=0, ge=0),
):
    """run_trace：返回状态、最后完成工具、待完成工具与事件。"""
    user_id = await require_user(authorization)
    events = await _events(run_id, user_id, after_seq)
    if not events:
        raise HTTPException(status_code=404, detail="run trace not found")
    summary = build_summary(events)
    return {
        "run_id": summary.run_id,
        "thread_id": summary.thread_id,
        "status": summary.status,
        "last_completed_tool": summary.last_completed_tool,
        "pending_tools": summary.pending_tools,
        "events": summary.events,
    }


@router.get("/runs/{run_id}/usage")
async def get_run_usage(
    run_id: str,
    authorization: str | None = Header(default=None),
    after_seq: int = Query(default=0, ge=0),
):
    """usage：token 汇总与压缩记录。"""
    user_id = await require_user(authorization)
    events = await _events(run_id, user_id, after_seq)
    if not events:
        raise HTTPException(status_code=404, detail="run usage not found")
    summary = build_summary(events)
    return {
        "run_id": summary.run_id,
        "token_usage": summary.token_usage,
        "compression": summary.compression,
        "status": summary.status,
    }
