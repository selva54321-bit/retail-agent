from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.config import settings
from api.errors import register_exception_handlers
from api.routers.dashboard import router as dashboard_router
from api.routers.cycles import router as cycles_router
from api.routers.health import router as health_router
from api.routers.intake import router as intake_router
from api.routers.intelligence import router as intelligence_router
from api.routers.recommendations import router as recommendations_router
from api.routers.retailers import router as retailers_router
from core import database as db


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_exception_handlers(app)

    app.include_router(health_router)
    app.include_router(dashboard_router, prefix=settings.api_prefix)
    app.include_router(intake_router, prefix=settings.api_prefix)
    app.include_router(retailers_router, prefix=settings.api_prefix)
    app.include_router(cycles_router, prefix=settings.api_prefix)
    app.include_router(recommendations_router, prefix=settings.api_prefix)
    app.include_router(intelligence_router, prefix=settings.api_prefix)

    @app.on_event("startup")
    def on_startup() -> None:
        db.init_db()

    return app


app = create_app()
