from __future__ import annotations

from fastapi import FastAPI

from api.routers import catalog, health, ingest
from pipeline.common.logging import configure_logging


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(title="Medical Imaging Pipeline API")
    app.include_router(health.router)
    app.include_router(ingest.router)
    app.include_router(catalog.router)
    return app


app = create_app()
