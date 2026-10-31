"""Settings › About › Quit: the desktop app, from this computer, and nobody else."""

from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from app import serve
from app.config import Settings
from app.main import create_app
from app.processing.cars import CarDatabase
from app.service import TelemetryService
from app.storage.db import init_db, make_engine, make_session_factory
from app.storage.repository import Repository

TOKEN = "t0ken-for-the-tablet"


@pytest.fixture
def quits(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """serve.QUIT as run() sets it, recording the calls."""
    calls: list[int] = []
    monkeypatch.setattr(serve, "QUIT", lambda: calls.append(1))
    return calls


@pytest.fixture
async def make_client(tmp_path):
    made = []

    async def make(*, desktop: bool = True, peer: str = "127.0.0.1") -> AsyncClient:
        settings = Settings(
            _env_file=None, source="sim", db_path=tmp_path / "test.db", desktop=desktop,
            lan_access=True, admin_token=TOKEN, mdns=False,
        )
        engine = make_engine(settings.db_path)
        await init_db(engine)
        repo = Repository(make_session_factory(engine))
        service = TelemetryService(settings, repo, CarDatabase())
        app = create_app()
        app.router.lifespan_context = None  # type: ignore[assignment]
        app.state.service = service
        transport = ASGITransport(app=app, client=(peer, 50000))
        client = AsyncClient(transport=transport, base_url="http://127.0.0.1:8000")
        made.append((client, service, engine))
        return client

    yield make
    for client, service, engine in made:
        await client.aclose()
        await service.stop()
        await engine.dispose()


async def test_the_desktop_app_quits_from_this_computer(make_client, quits) -> None:
    c = await make_client()
    assert (await c.get("/api/admin/stats")).json()["can_quit"] is True
    resp = await c.post("/api/admin/quit")
    assert resp.status_code == 202
    await asyncio.sleep(0.4)  # the stop is scheduled after the answer
    assert quits == [1]


async def test_a_phone_cannot_quit_it_even_with_the_token(make_client, quits) -> None:
    c = await make_client(peer="192.168.1.20")
    headers = {"X-API-Key": TOKEN, "Host": "192.168.1.5:8000"}
    stats = await c.get("/api/admin/stats", headers=headers)
    assert stats.status_code == 200 and stats.json()["can_quit"] is False
    resp = await c.post("/api/admin/quit", headers=headers)
    assert resp.status_code == 403
    await asyncio.sleep(0.4)
    assert quits == []


async def test_docker_and_source_installs_never_offer_it(make_client, quits) -> None:
    c = await make_client(desktop=False)
    headers = {"X-API-Key": TOKEN}  # loopback is not admin outside the desktop app
    assert (await c.get("/api/admin/stats", headers=headers)).json()["can_quit"] is False
    assert (await c.post("/api/admin/quit", headers=headers)).status_code == 403
    assert quits == []


async def test_not_offered_without_the_desktop_server(make_client, monkeypatch) -> None:
    # The desktop flag under plain uvicorn (a dev run): nothing to stop it with.
    monkeypatch.setattr(serve, "QUIT", None)
    c = await make_client()
    assert (await c.get("/api/admin/stats")).json()["can_quit"] is False
    assert (await c.post("/api/admin/quit")).status_code == 403
