"""FastAPI application factory.

The app serves both the JSON API (``/api/...``) and the single-page UI from
``leadgen/static``.  It binds to 127.0.0.1 by default because it holds email
credentials; ``--host 0.0.0.0`` is available for LAN/preview use.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import get_settings
from .db import create_all, init_engine
from .routers import accounts, campaigns, crm, system, targeting

STATIC_DIR = Path(__file__).resolve().parent / "static"

log = logging.getLogger("leadgen.app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    init_engine(settings)
    create_all()
    settings.state_dir.mkdir(parents=True, exist_ok=True)
    log.info("LeadGen ready — state dir %s, db %s", settings.state_dir, settings.sqlalchemy_url)
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version=settings.version,
        description="Locally hosted B2B lead generation, cold outreach and CRM platform",
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    for module in (accounts, targeting, campaigns, crm, system):
        app.include_router(module.router)

    @app.get("/api/ping")
    def ping() -> dict:
        return {"ok": True, "app": settings.app_name, "version": settings.version}

    @app.exception_handler(Exception)
    async def unhandled(request, exc):  # pragma: no cover - defensive
        log.exception("unhandled error on %s", request.url)
        return JSONResponse(status_code=500, content={"detail": str(exc)})

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

        @app.get("/", include_in_schema=False)
        def index() -> FileResponse:
            return FileResponse(STATIC_DIR / "index.html")

        @app.get("/favicon.ico", include_in_schema=False)
        def favicon() -> FileResponse:
            icon = STATIC_DIR / "favicon.svg"
            if icon.exists():
                return FileResponse(icon, media_type="image/svg+xml")
            return FileResponse(STATIC_DIR / "index.html")

    return app


app = create_app()
