from fastapi import APIRouter, Request, BackgroundTasks
from fastapi.responses import JSONResponse
from mentor.utils.langfuse_signature import verify_signature
from mentor.config.langfuse_settings import langfuse_settings
from mentor.benchmarks.benchmark import run_benchmark

router = APIRouter()


@router.post("/api/webhooks/langfuse/experiments")
async def start_experiment(request: Request, background_tasks: BackgroundTasks):
    # 1. 验证签名
    isvalid = await verify_signature(
        request, langfuse_settings.experiment_webhook_secret
    )
    if not isvalid:
        return JSONResponse({"error": "Invalid Webhook Signature"}, status_code=401)
    # 2. 启动实验
    body = await request.json()
    dataset_name = body["datasetName"]
    background_tasks.add_task(run_benchmark, dataset_name)
    return JSONResponse({"ok": True}, status_code=202)
