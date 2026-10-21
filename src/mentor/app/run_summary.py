"""按 run 读取投影摘要：前端卡片与审计共用的只读接口。"""

import asyncio

from fastapi import APIRouter, Header, HTTPException, Query

from mentor.events.projection import build_summary
from mentor.events.store import get_run_event_store
from mentor.security.auth import authenticate

router = APIRouter()


async def _require_user(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="invalid authorization")
    try:
        user = await authenticate(authorization)
    except Exception as exc:
        raise HTTPException(status_code=401, detail="invalid token") from exc
    identity = user.get("identity")
    if not identity:
        raise HTTPException(status_code=401, detail="invalid token")
    return str(identity)


@router.get("/runs/{run_id}/summary")
async def get_run_summary(
    run_id: str,
    authorization: str | None = Header(default=None),
    after_seq: int = Query(default=0, ge=0),
):
    """按 seq 返回当前用户的 run 事件；after_seq 支持增量重放。"""
    user_id = await _require_user(authorization)
    store = get_run_event_store()
    events = await asyncio.to_thread(
        store.list, run_id, user_id=user_id, after_seq=after_seq
    )
    if not events:
        raise HTTPException(status_code=404, detail="run summary not found")
    return build_summary(events)
