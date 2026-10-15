import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from mentor.config.langfuse_settings import langfuse_settings
from mentor.utils.langfuse_signature import verify_signature

router = APIRouter()


@router.post("/api/webhooks/langfuse/alerts")
async def receive_alert(request: Request):
    if not await verify_signature(request, langfuse_settings.alert_webhook_secret):
        return JSONResponse({"error": "Invalid Webhook Signature"}, status_code=401)

    event = await request.json()
    # 随便你怎么通知：邮件、短信 （SMS接口）
    return JSONResponse({"ok": True}, status_code=200)
