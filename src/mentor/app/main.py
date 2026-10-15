from fastapi import FastAPI
from mentor.app.experiment_webhook import router as experiment_router
from mentor.app.alert_webhook import router as alert_router

app = FastAPI()
app.include_router(experiment_router)
app.include_router(alert_router)
