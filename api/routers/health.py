from __future__ import annotations

from fastapi import APIRouter

from api.schemas.common import HealthResponse
from core import database as db
from core.llm import check_gemini, check_grok, check_ollama


router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live", response_model=HealthResponse)
def live() -> HealthResponse:
    return HealthResponse(status="ok", details={"service": "alive"})


@router.get("/ready", response_model=HealthResponse)
def ready() -> HealthResponse:
    mongo = db.check_mongodb_health()
    details = {
        "mongodb": mongo,
        "ollama": check_ollama(),
        "gemini": check_gemini(),
        "grok": check_grok(),
    }
    overall = "ok" if mongo.get("ok") else "degraded"
    return HealthResponse(status=overall, details=details)
