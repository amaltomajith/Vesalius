from __future__ import annotations

from fastapi import FastAPI

from api import auth
from api.routers import ai, catalog, demo, health, ingest, ui
from pipeline.common.logging import configure_logging


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(title="Vesalius API")
    app.include_router(health.router)
    app.include_router(ingest.router)
    app.include_router(catalog.router)
    app.include_router(demo.router)
    app.include_router(ai.router)
    app.include_router(auth.router)
    app.include_router(ui.router)
    app.mount("/static", ui.RevalidatingStaticFiles(directory=ui.STATIC_DIR), name="static")
    return app


app = create_app()
