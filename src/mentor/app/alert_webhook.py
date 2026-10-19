"""Langfuse 告警 Webhook：校验签名后接收告警事件（通知渠道待接入）。"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from mentor.config.langfuse_settings import langfuse_settings
from mentor.utils.langfuse_signature import verify_signature

router = APIRouter()


@router.post("/api/webhooks/langfuse/alerts")
async def receive_alert(request: Request):
    if not await verify_signature(request, langfuse_settings.alert_webhook_secret):
        return JSONResponse({"error": "Invalid Webhook Signature"}, status_code=401)

    # 先解析一次，确保请求体是合法 JSON；具体通知渠道（邮件/短信/IM）在此接入。
    await request.json()
    return JSONResponse({"ok": True}, status_code=200)
