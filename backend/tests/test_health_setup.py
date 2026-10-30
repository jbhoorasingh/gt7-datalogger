"""/api/health identity, the setup marker and first-run mode (#184)."""

from __future__ import annotations

import asyncio
import logging

import pytest
from httpx import ASGITransport, AsyncClient

from app import main, setup_state
from app.config import Settings
from app.processing.cars import CarDatabase
from app.service import TelemetryService
from app.storage.db import init_db, make_engine, make_session_factory
from app.storage.repository import Repository
from app.version import version


@pytest.fixture
async def client(tmp_path):
    settings = Settings(_env_file=None, source="sim", db_path=tmp_path / "test.db")
    engine = make_engine(settings.db_path)
    await init_db(engine)
    service = TelemetryService(settings, Repository(make_session_factory(engine)), CarDatabase())
    app = main.create_app()
    app.router.lifespan_context = None  # type: ignore[assignment]
    app.state.service = service
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c, service
    await service.stop()
    await engine.dispose()


async def test_health_says_what_this_server_is(client) -> None:
    c, _ = client
    body = (await c.get("/api/health")).json()
    assert body == {
        "status": "ok",
        "app": "gt7-datalogger",
        "version": version(),
        "install_kind": setup_state.install_kind(Settings(_env_file=None)),
        "schema": {"current": setup_state.head_revision(), "head": setup_state.head_revision()},
    }


def test_the_desktop_flag_is_the_install_kind() -> None:
    assert setup_state.install_kind(Settings(_env_file=None, desktop=True)) == "desktop"


async def test_setup_is_incomplete_until_completed(client) -> None:
    c, service = client
    service.recording = False
    state = (await c.get("/api/admin/setup")).json()
    assert state["completed"] is False
    assert state["source"] == "sim"
    assert state["listener_error"] is None
    done = (await c.post("/api/admin/setup/complete")).json()
    assert done["completed"] is True
    assert service.recording is True


async def _lifespan_settings(tmp_path, monkeypatch, stored: dict[str, str]) -> TelemetryService:
    monkeypatch.setenv("GT7_DB_PATH", str(tmp_path / "gt7.db"))
    monkeypatch.setenv("GT7_FORCE_SIM", "firstrun")
    monkeypatch.setenv("GT7_SHARED_BUNDLES_URL", "")
    monkeypatch.setattr(main, "refresh_cars_if_stale", _noop)
    main.get_settings.cache_clear()
    engine = make_engine(tmp_path / "gt7.db")
    await init_db(engine)
    repo = Repository(make_session_factory(engine))
    for key, value in stored.items():
        await repo.set_setting(key, value)
    await engine.dispose()
    app = main.create_app()
    async with main.lifespan(app):
        service: TelemetryService = app.state.service
        return service


async def _noop(*_args, **_kwargs) -> None:
    return None


async def test_first_run_forces_the_simulator_and_holds_recording(tmp_path, monkeypatch) -> None:
    try:
        service = await _lifespan_settings(
            tmp_path, monkeypatch, {"source": "udp", "mdns": "true"}
        )
        assert service.settings.source == "sim"
        assert service.settings.mdns is False
        assert service.recording is False
    finally:
        main.get_settings.cache_clear()


async def test_first_run_ends_with_the_marker(tmp_path, monkeypatch) -> None:
    try:
        service = await _lifespan_settings(
            tmp_path, monkeypatch, {"source": "sim", setup_state.SETUP_KEY: "2026-10-30"}
        )
        assert service.recording is True
    finally:
        main.get_settings.cache_clear()


async def test_teardown_runs_every_step_when_one_fails(caplog) -> None:
    ran: list[str] = []

    class Discovery:
        async def stop(self) -> None:
            ran.append("mdns")

    class Service:
        discovery = Discovery()

        async def stop(self) -> None:
            ran.append("service")
            raise RuntimeError("boom")

    class Engine:
        async def dispose(self) -> None:
            ran.append("database")

    async def forever() -> None:
        await asyncio.sleep(3600)

    task = asyncio.create_task(forever())
    with caplog.at_level(logging.ERROR):
        await main.shut_down(Service(), Engine(), (task,), logging.getLogger("t"))  # type: ignore[arg-type]
    assert ran == ["service", "database", "mdns"]
    assert task.cancelled()
    assert "telemetry service did not stop cleanly" in caplog.text
