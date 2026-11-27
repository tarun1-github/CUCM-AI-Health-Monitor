"""FastAPI application entrypoint for CUCM AI Health Monitor."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.api.routes import assistant, devices, health, reports
from backend.utils.config_loader import PROJECT_ROOT, get_settings
from backend.utils.logger import get_logger

logger = get_logger("main", category="app")

app = FastAPI(
    title="CUCM AI Health Monitor",
    version="1.0.0",
    description="AI-powered Cisco CUCM remote health monitoring and diagnostic reporting platform",
)

app.include_router(devices.router)
app.include_router(health.router)
app.include_router(reports.router)
app.include_router(assistant.router)

FRONTEND_DIR = PROJECT_ROOT / "frontend"
ASSETS_DIR = FRONTEND_DIR / "assets"
if ASSETS_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(ASSETS_DIR)), name="assets")


@app.get("/api/ping")
def ping():
    settings = get_settings()
    return {"status": "ok", "app": "CUCM AI Health Monitor", "env": settings.app_env}


@app.get("/")
def serve_dashboard():
    index_file = FRONTEND_DIR / "index.html"
    if index_file.exists():
        return FileResponse(str(index_file))
    return {"message": "CUCM AI Health Monitor API is running."}
