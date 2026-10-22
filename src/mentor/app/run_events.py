"""Run 事件 SSE：前端卡片实时订阅 run_events 的同一事件流。"""

import asyncio
import json

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import StreamingResponse

from mentor.app.auth import require_user
from mentor.events.store import get_run_event_store

router = APIRouter()


def _int_header(value: str | None) -> int:
    try:
        return max(int(value or 0), 0)
    except ValueError:
        return 0


@router.get("/runs/{run_id}/events")
async def stream_run_events(
    run_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
    after_seq: int = Query(default=0, ge=0),
    last_event_id: str | None = Header(default=None),
):
    """SSE 流；支持 Last-Event-ID 断线重连，遇到 run_finished 自动结束。"""
    user_id = await require_user(authorization)
    store = get_run_event_store()
    cursor = max(after_seq, _int_header(last_event_id))

    async def generator():
        nonlocal cursor
        idle = 0
        while True:
            if await request.is_disconnected():
                return
            events = await asyncio.to_thread(
                store.list, run_id, user_id=user_id, after_seq=cursor
            )
            for event in events:
                cursor = event.seq
                payload = json.dumps(
                    event.model_dump(mode="json"), ensure_ascii=False
                )
                yield (
                    f"id: {event.seq}\n"
                    f"event: {event.kind}\n"
                    f"data: {payload}\n\n"
                )
                if event.kind == "run_finished":
                    return
            if events:
                idle = 0
            else:
                idle += 1
                if idle % 30 == 0:
                    yield ": ping\n\n"
            await asyncio.sleep(0.5)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
