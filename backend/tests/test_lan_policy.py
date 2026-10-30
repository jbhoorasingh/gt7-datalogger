"""LAN access as a live policy, and the desktop's own browser as admin (#195)."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app import main
from app.config import Settings
from app.processing.cars import CarDatabase
from app.service import TelemetryService
from app.storage.db import init_db, make_engine, make_session_factory
from app.storage.repository import Repository

PHONE = ("192.168.1.50", 50000)
HERE = ("127.0.0.1", 50000)


@pytest.fixture
async def app_service(tmp_path):
    settings = Settings(_env_file=None, source="sim", db_path=tmp_path / "test.db")
    engine = make_engine(settings.db_path)
    await init_db(engine)
    service = TelemetryService(settings, Repository(make_session_factory(engine)), CarDatabase())
    app = main.create_app()
    app.router.lifespan_context = None  # type: ignore[assignment]
    app.state.service = service
    yield app, service
    await service.stop()
    await engine.dispose()


def _client(app, peer):
    return AsyncClient(transport=ASGITransport(app=app, client=peer), base_url="http://test")


async def test_lan_off_refuses_other_devices_and_keeps_this_one(app_service) -> None:
    app, service = app_service
    service.settings.lan_access = False
    async with _client(app, PHONE) as phone, _client(app, HERE) as here:
        r = await phone.get("/api/status")
        assert r.status_code == 403
        assert r.json()["reason"] == "lan_off"
        assert (await here.get("/api/status")).status_code == 200
        # Live: on again without a restart.
        service.settings.lan_access = True
        assert (await phone.get("/api/status")).status_code == 200


async def test_the_desktop_browser_is_admin_and_the_phone_needs_the_token(app_service) -> None:
    app, service = app_service
    service.settings.admin_token = "s3cret"
    service.settings.desktop = True
    async with _client(app, PHONE) as phone, _client(app, HERE) as here:
        assert (await here.get("/api/admin/stats")).status_code == 200
        assert (await phone.get("/api/admin/stats")).status_code == 401
        assert (await phone.get("/api/status")).status_code == 200  # reads stay open
        r = await phone.get("/api/admin/stats", headers={"X-API-Key": "s3cret"})
        assert r.status_code == 200
    service.settings.desktop = False
    async with _client(app, HERE) as here:
        # Docker and source: loopback is no exception.
        assert (await here.get("/api/admin/stats")).status_code == 401


async def test_finishing_setup_with_lan_on_creates_a_token(app_service) -> None:
    app, service = app_service
    service.settings.lan_access = False
    service.settings.desktop = True
    async with _client(app, HERE) as here:
        body = (await here.post("/api/admin/setup/complete", json={"lan_access": True})).json()
    assert body["completed"] is True
    assert body["lan_access"] is True
    assert body["admin_token"] and body["admin_token"] == service.settings.admin_token
    stored = await service.repo.get_settings()
    assert stored["lan_access"] == "true"


async def test_finishing_setup_keeps_an_existing_token(app_service) -> None:
    app, service = app_service
    service.settings.admin_token = "mine"
    service.settings.desktop = True
    async with _client(app, HERE) as here:
        body = (await here.post("/api/admin/setup/complete", json={"lan_access": True})).json()
    assert body["admin_token"] is None
    assert service.settings.admin_token == "mine"
