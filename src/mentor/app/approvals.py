"""审批审计查询：按 run 读取 approval_events。"""

import asyncio

from fastapi import APIRouter, Header, HTTPException, Query

from mentor.app.auth import require_user
from mentor.events.approvals import get_approval_store

router = APIRouter()


@router.get("/runs/{run_id}/approvals")
async def get_run_approvals(
    run_id: str,
    authorization: str | None = Header(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
):
    """返回当前用户的审批记录；用于审计和前端审批卡。"""
    user_id = await require_user(authorization)
    store = get_approval_store()
    events = await asyncio.to_thread(
        store.list, run_id, user_id=user_id, limit=limit
    )
    if not events:
        raise HTTPException(status_code=404, detail="approvals not found")
    return {
        "run_id": run_id,
        "approvals": [event.model_dump(mode="json") for event in events],
    }
