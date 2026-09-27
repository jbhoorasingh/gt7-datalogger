"""Opt-in admin token: gated admin/mutating endpoints, open reads, compat."""

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.processing.cars import CarDatabase
from app.service import TelemetryService
from app.storage.db import init_db, make_engine, make_session_factory
from app.storage.repository import Repository
from tests.test_api import drive_laps

TOKEN = "s3cret-token"
AUTH = {"X-API-Key": TOKEN}


async def make_client(tmp_path, admin_token: str):
    settings = Settings(
        source="udp",
        db_path=tmp_path / "test.db",
        ws_rate=1000,
        telemetry_port=43744,
        admin_token=admin_token,
    )
    engine = make_engine(settings.db_path)
    await init_db(engine)
    repo = Repository(make_session_factory(engine))
    service = TelemetryService(settings, repo, CarDatabase())
    service.processor.min_lap_ticks = 1
    app = create_app()
    app.router.lifespan_context = None  # type: ignore[assignment]
    app.state.service = service
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test"), service, engine


@pytest.fixture
async def secured(tmp_path):
    c, service, engine = await make_client(tmp_path, TOKEN)
    async with c:
        yield c, service
    await service.stop()
    await engine.dispose()


@pytest.fixture
async def open_client(tmp_path):
    c, service, engine = await make_client(tmp_path, "")
    async with c:
        yield c, service
    await service.stop()
    await engine.dispose()


async def test_admin_get_requires_token(secured) -> None:
    c, _ = secured
    assert (await c.get("/api/admin/settings")).status_code == 401
    assert (await c.get("/api/admin/settings", headers=AUTH)).status_code == 200


async def test_wrong_token_403(secured) -> None:
    c, _ = secured
    resp = await c.get("/api/admin/settings", headers={"X-API-Key": "nope"})
    assert resp.status_code == 403


async def test_mutating_routes_require_token(secured) -> None:
    c, service = secured
    await drive_laps(service, laps=1)
    lap_id = (await c.get("/api/laps")).json()[0]["id"]

    checks = [
        ("DELETE", f"/api/laps/{lap_id}", None),
        ("DELETE", "/api/sessions/1", None),
        ("POST", "/api/laps/import", {"format": "x", "version": 2, "lap": {}}),
        ("POST", "/api/tracks", {"name": "T", "lap_id": lap_id}),
        ("DELETE", "/api/tracks/1", None),
        ("POST", "/api/control/recording", {"recording": True}),
        ("POST", "/api/control/log-lap-now", None),
        ("POST", "/api/layouts", {"name": "l", "kind": "overlay", "config": {}}),
        ("PUT", "/api/layouts/1", {"config": {}}),
        ("DELETE", "/api/layouts/1", None),
        ("POST", "/api/admin/vacuum", None),
        ("PUT", "/api/admin/settings", {"log_level": "INFO"}),
        ("POST", "/api/admin/clear-data", None),
    ]
    for method, url, body in checks:
        bare = await c.request(method, url, json=body)
        assert bare.status_code == 401, f"{method} {url} not gated"
        authed = await c.request(method, url, json=body, headers=AUTH)
        assert authed.status_code != 401, f"{method} {url} rejected valid token"
        assert authed.status_code != 403, f"{method} {url} rejected valid token"


async def test_reads_stay_open_with_token_set(secured) -> None:
    c, service = secured
    await drive_laps(service, laps=1)
    lap_id = (await c.get("/api/laps")).json()[0]["id"]
    for url in (
        "/api/health",
        "/api/status",
        "/api/sessions",
        "/api/laps",
        f"/api/laps/{lap_id}/export",
        f"/api/laps/{lap_id}/export.csv",
        # Every lap at once is no more privileged than each one alone.
        "/api/export/laps.zip",
        "/api/export/laps-csv.zip",
        "/api/tracks",
        "/api/layouts",
    ):
        assert (await c.get(url)).status_code == 200, url


async def test_vacuum_with_token(secured) -> None:
    c, _ = secured
    resp = await c.post("/api/admin/vacuum", headers=AUTH)
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_no_token_configured_is_fully_open(open_client) -> None:
    c, service = open_client
    await drive_laps(service, laps=1)
    lap_id = (await c.get("/api/laps")).json()[0]["id"]
    assert (await c.get("/api/admin/settings")).status_code == 200
    assert (await c.post("/api/admin/vacuum")).status_code == 200
    assert (await c.delete(f"/api/laps/{lap_id}")).status_code == 200
