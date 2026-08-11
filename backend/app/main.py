"""Application entrypoint: FastAPI app serving the API, WebSocket, and SPA."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import logbuffer
from app.api import admin, layouts, routes, tracks, ws
from app.config import get_settings
from app.processing.cars import CarDatabase
from app.race_engineer import VERBOSITY_MODES
from app.service import TelemetryService
from app.storage.db import init_db, make_engine, make_session_factory
from app.storage.repository import Repository

FRONTEND_DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    logbuffer.install()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    log = logging.getLogger("app")

    engine = make_engine(settings.db_path)
    await init_db(engine)
    repo = Repository(make_session_factory(engine))

    # Settings changed via the admin page override env defaults.
    stored = await repo.get_settings()
    if "ps_ip" in stored:
        settings.ps_ip = stored["ps_ip"]
    if stored.get("source") in ("udp", "sim"):
        settings.source = stored["source"]
    if stored.get("packet_format") in ("A", "B", "~", "C"):
        settings.packet_format = stored["packet_format"]
    if "log_level" in stored:
        logging.getLogger().setLevel(stored["log_level"])
    if "webhook_url" in stored:
        settings.webhook_url = stored["webhook_url"]
    if "webhook_events" in stored:
        settings.webhook_events = stored["webhook_events"]
    if "race_engineer" in stored:
        settings.race_engineer = stored["race_engineer"] == "true"
    if stored.get("race_engineer_verbosity") in VERBOSITY_MODES:
        settings.race_engineer_verbosity = stored["race_engineer_verbosity"]
    if "race_engineer_categories" in stored:
        settings.race_engineer_categories = stored["race_engineer_categories"]
    if stored.get("race_engineer_units") in ("metric", "imperial"):
        settings.race_engineer_units = stored["race_engineer_units"]

    cars = CarDatabase()
    cars.load(settings.cars_csv)

    service = TelemetryService(settings, repo, cars)
    app.state.service = service
    await service.start()

    if settings.source == "udp" and not settings.ps_ip:
        log.info(
            "GT7_PS_IP not set: broadcasting heartbeat for auto-discovery. "
            "If no data arrives, check the console IP and that UDP %d is not firewalled.",
            settings.telemetry_port,
        )
    yield
    await service.stop()
    await engine.dispose()


def create_app() -> FastAPI:
    app = FastAPI(title="GT7 Datalogger", lifespan=lifespan)
    # No CORS middleware by default: both serving modes are same-origin (the
    # SPA is mounted below; the dev server proxies /api and /ws). Cross-origin
    # consumers must opt in via GT7_CORS_ORIGINS.
    origins = [o.strip() for o in get_settings().cors_origins.split(",") if o.strip()]
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_methods=["*"],
            allow_headers=["*"],  # includes X-API-Key
        )
    app.include_router(routes.router)
    app.include_router(admin.router)
    app.include_router(layouts.router)
    app.include_router(tracks.router)
    app.include_router(ws.router)
    if FRONTEND_DIST.exists():
        # SPA deep links: /overlay?... and /dash?... must serve the app (the
        # static mount only resolves "/"). Plain paths keep strict URL
        # validators happy (some streaming apps reject /#overlay fragments).
        @app.get("/overlay", include_in_schema=False)
        async def overlay_page() -> FileResponse:
            return FileResponse(FRONTEND_DIST / "index.html")

        @app.get("/dash", include_in_schema=False)
        async def dash_page() -> FileResponse:
            return FileResponse(FRONTEND_DIST / "index.html")

        @app.get("/engineer", include_in_schema=False)
        async def engineer_page() -> FileResponse:
            return FileResponse(FRONTEND_DIST / "index.html")

        app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="spa")
    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run("app.main:app", host=settings.http_host, port=settings.http_port)
