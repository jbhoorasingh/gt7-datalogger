"""Application entrypoint: FastAPI app serving the API, WebSocket, and SPA."""

from __future__ import annotations

import asyncio
import contextlib
import datetime
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
from app.config import Settings, get_settings
from app.processing import car_refresh, track_seed
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


# Settings key holding the digest of the seed file the tracks table was last
# built from. Same persisted key-value pattern the rest of lifespan uses.
SEED_DIGEST_KEY = "track_seed_digest"

# Which inventory the denormalised car columns on the session rows were last
# built from ("<schema>:<generated>"). Same pattern, same reason: the work is
# skipped on every start where the answer would not change.
GENERATED_KEY = car_refresh.GENERATED_KEY


async def sync_track_seed(
    settings: Settings,
    repo: Repository,
    stored: dict[str, str],
    log: logging.Logger,
) -> None:
    """Bring the seeded rows in the tracks table up to date with the file.

    Runs on every start but does nothing on almost all of them: the file's
    content hash is compared against what the database was last built from, so
    the write happens on a first run and on the release that changes the seed,
    and never otherwise.

    Nothing here may stop the app. A missing, unreadable or invalid seed leaves
    identification exactly as it was before #58 — answering only from what the
    user has named and surveyed — which is a worse experience, not a broken one.
    """
    path = settings.track_signatures_json
    # Blank setting: seeding off on purpose. Pydantic renders "" as Path("."),
    # so the empty string never survives to be compared against.
    if str(path) in ("", "."):
        return
    try:
        digest = track_seed.digest(path)
    except OSError:
        log.info("no track signature seed at %s; identification starts empty", path)
        return
    if stored.get(SEED_DIGEST_KEY) == digest:
        return
    rows = track_seed.load(path)
    if not rows:
        return  # load() has already said why
    try:
        count = await repo.sync_seeded_tracks(rows)
        await repo.set_setting(SEED_DIGEST_KEY, digest)
    except Exception:  # pragma: no cover - a seed must never fail a start
        log.exception("track signature seed could not be applied; continuing without it")
        return
    surveyed = sum(1 for r in rows if r.provenance == "survey")
    log.info(
        "track signatures seeded: %d configurations (%d from survey, %d from capture)",
        count, surveyed, count - surveyed,
    )


async def sync_car_inventory(
    settings: Settings,
    repo: Repository,
    cars: CarDatabase,
    stored: dict[str, str],
    log: logging.Logger,
) -> None:
    """Load the shipped inventory and fill it in on the session rows.

    Layer 1 of #57: this is the floor, and it involves no network at all. The
    backfill re-runs only when the loaded inventory is not the one the rows
    were last built from, so a normal start does nothing but the load.
    """
    cars.load(settings.car_inventory())
    if not cars.count:
        return
    marker = car_refresh.stamp(cars)
    if stored.get(GENERATED_KEY) == marker:
        return
    try:
        filled = await repo.backfill_session_cars(cars.all())
        await repo.set_setting(GENERATED_KEY, marker)
    except Exception:  # pragma: no cover - car details must never fail a start
        log.exception("car details could not be backfilled; continuing without them")
        return
    if filled:
        log.info("car details filled in on %d existing session(s)", filled)


async def refresh_cars_if_stale(
    settings: Settings,
    repo: Repository,
    cars: CarDatabase,
    stored: dict[str, str],
    log: logging.Logger,
) -> None:
    """Layer 2 of #57: bring in cars added since the release was cut.

    Runs in a background task, so a slow or hanging site delays nothing, and
    swallows everything it can raise: an install with no internet, or one
    running the day gran-turismo.com changes its page layout, is left with the
    inventory from layer 1 — which is every car we shipped knowing about.
    """
    if not car_refresh.is_stale(stored, datetime.date.today()):
        return
    try:
        await car_refresh.fetch_and_store(cars, settings.refreshed_car_inventory())
    except Exception as exc:
        # Info, not error: being offline is a normal state for a datalogger on
        # a LAN with a games console, and nothing is broken when it happens.
        log.info("car inventory not refreshed (%s); using the bundled one", exc)
        return
    try:
        filled = await car_refresh.record(repo, cars, datetime.date.today())
    except Exception:
        log.exception("refreshed car inventory could not be recorded")
        return
    if filled:
        log.info("car details updated on %d existing session(s)", filled)


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

    await sync_track_seed(settings, repo, stored, log)

    cars = CarDatabase()
    await sync_car_inventory(settings, repo, cars, stored, log)

    service = TelemetryService(settings, repo, cars)
    app.state.service = service
    await service.start()

    # Deliberately not awaited: startup must not wait on gran-turismo.com, and
    # must not fail if it never answers. The task is kept so it can be
    # cancelled at shutdown rather than outliving the app it belongs to.
    refresh = asyncio.create_task(refresh_cars_if_stale(settings, repo, cars, stored, log))

    if settings.source == "udp" and not settings.ps_ip:
        log.info(
            "GT7_PS_IP not set: broadcasting heartbeat for auto-discovery. "
            "If no data arrives, check the console IP and that UDP %d is not firewalled.",
            settings.telemetry_port,
        )
    yield
    # Cancelled AND awaited: a task dropped while still pending logs a
    # "Task was destroyed but it is pending" warning on the way out, which
    # looks like a fault in a shutdown that is working correctly.
    refresh.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await refresh
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
