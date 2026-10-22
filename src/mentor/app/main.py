"""FastAPI 应用入口：注册实验、告警与 run 摘要路由。"""

from fastapi import FastAPI
from mentor.app.experiment_webhook import router as experiment_router
from mentor.app.alert_webhook import router as alert_router
from mentor.app.approvals import router as approvals_router
from mentor.app.run_events import router as run_events_router
from mentor.app.run_summary import router as run_summary_router

app = FastAPI()
# 两个 Webhook 分别处理实验触发与告警通知；签名校验在各自路由内完成。
app.include_router(experiment_router)
app.include_router(alert_router)
app.include_router(approvals_router)
# run 摘要按 Bearer 身份过滤，供前端卡片与审计读取。
app.include_router(run_summary_router)
app.include_router(run_events_router)
