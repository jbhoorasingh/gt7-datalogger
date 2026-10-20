"""Phone discovery and pull (#142): mDNS advertisement, sessions changed since."""

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from app import discovery
from app.config import Settings
from app.main import create_app
from app.processing.cars import CarDatabase
from app.processing.laps import SessionInfo
from app.service import TelemetryService
from app.storage.db import init_db, make_engine, make_session_factory
from app.storage.repository import Repository
from tests.test_api import drive_laps


@pytest.fixture
async def client(tmp_path):
    settings = Settings(
        source="udp", db_path=tmp_path / "test.db", ws_rate=1000, telemetry_port=43748
    )
    engine = make_engine(settings.db_path)
    await init_db(engine)
    repo = Repository(make_session_factory(engine))
    service = TelemetryService(settings, repo, CarDatabase())
    service.processor.min_lap_ticks = 1
    app = create_app()
    app.router.lifespan_context = None  # type: ignore[assignment]
    app.state.service = service
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c, service
    await service.stop()
    await engine.dispose()


# --- sessions changed since -------------------------------------------------


async def changes(c: AsyncClient, since: int) -> dict:
    resp = await c.get("/api/sessions/changes", params={"since": since})
    assert resp.status_code == 200
    return resp.json()


async def test_changes_from_zero_is_everything(client) -> None:
    c, service = client
    assert await changes(c, 0) == {"revision": 0, "sessions": [], "ids": []}
    await drive_laps(service, laps=2)
    body = await changes(c, 0)
    assert [s["id"] for s in body["sessions"]] == body["ids"] == [service.session_id]
    assert body["sessions"][0]["lap_count"] == 2
    assert body["revision"] == body["sessions"][0]["revision"] > 0
    # Nothing changed: nothing to send, the same revision back.
    again = await changes(c, body["revision"])
    assert again["sessions"] == [] and again["revision"] == body["revision"]


@pytest.mark.parametrize("write", [
    "note", "track", "race_result", "override", "survey", "partial", "delete_lap",
])
async def test_every_write_marks_the_session_changed(client, write) -> None:
    c, service = client
    await drive_laps(service, laps=2)
    sid = service.session_id
    repo = service.repo
    laps = await repo.list_laps(session_id=sid)
    lap_id = laps[0]["id"]
    seen = (await changes(c, 0))["revision"]

    if write == "note":
        await repo.update_session(sid, note="wet")
    elif write == "track":
        await repo.set_session_track(sid, "Mount Panorama")
    elif write == "race_result":
        await repo.record_race_result(sid, 1, 8, 2)
    elif write == "override":
        await repo.set_lap_best_override(lap_id, False, "contact")
    elif write == "survey":
        await repo.set_lap_survey_verdict(lap_id, 2, False)
    elif write == "partial":
        await repo.mark_session_laps_partial(sid, [1])
    elif write == "delete_lap":
        await repo.delete_lap(lap_id)

    body = await changes(c, seen)
    assert [s["id"] for s in body["sessions"]] == [sid], write
    assert body["revision"] > seen


def started(hour: str) -> SessionInfo:
    return SessionInfo(car_id=7, started_at=f"2026-10-13T{hour}:00:00")


async def test_only_the_changed_session_comes_back(client) -> None:
    c, service = client
    repo = service.repo
    first = await repo.create_session(started("10"), None)
    second = await repo.create_session(started("11"), None)
    seen = (await changes(c, 0))["revision"]
    await repo.update_session(first, note="dry")
    body = await changes(c, seen)
    assert [s["id"] for s in body["sessions"]] == [first]
    assert body["ids"] == [first, second]
    # A new session is a change too.
    third = await repo.create_session(started("12"), None)
    assert [s["id"] for s in (await changes(c, body["revision"]))["sessions"]] == [third]


async def test_deleted_sessions_drop_out_of_ids(client) -> None:
    c, service = client
    await drive_laps(service, laps=1)
    sid = service.session_id
    service.recording = False
    await service.repo.delete_session(sid)
    body = await changes(c, 0)
    assert sid not in body["ids"]


async def test_since_must_not_be_negative(client) -> None:
    c, _ = client
    assert (await c.get("/api/sessions/changes", params={"since": -1})).status_code == 422


# --- mDNS ---------------------------------------------------------------------


@pytest.mark.parametrize(("argv", "port"), [
    (["uvicorn", "app.main:app", "--port", "8004"], 8004),
    (["uvicorn", "app.main:app", "--port=8010"], 8010),
    (["python", "-m", "app.main"], 8123),
    (["uvicorn", "--port", "nope"], 8123),
])
def test_served_port(argv, port) -> None:
    assert discovery.served_port(Settings(http_port=8123), argv) == port


@pytest.mark.parametrize(("argv", "host"), [
    (["uvicorn", "app.main:app", "--host", "0.0.0.0"], "0.0.0.0"),
    (["uvicorn", "app.main:app", "--host=10.0.0.5", "--port", "8000"], "10.0.0.5"),
    # uvicorn's CLI binds loopback unless told otherwise: the dev script (#151).
    (["/repo/backend/.venv/bin/uvicorn", "app.main:app", "--port", "8000"], "127.0.0.1"),
    (["/usr/lib/python3/site-packages/uvicorn/__main__.py", "app.main:app"], "127.0.0.1"),
    # python -m app.main binds GT7_HTTP_HOST.
    (["/repo/backend/app/main.py"], "192.168.1.9"),
])
def test_served_host(argv, host) -> None:
    assert discovery.served_host(Settings(http_host="192.168.1.9"), argv) == host


@pytest.mark.parametrize(("host", "loopback"), [
    ("127.0.0.1", True), ("localhost", True), ("::1", True),
    ("0.0.0.0", False), ("::", False), ("10.0.0.5", False), ("gt7.local", False),
])
def test_is_loopback(host, loopback) -> None:
    assert discovery.is_loopback(host) is loopback


def test_lan_address_follows_the_bind_host(monkeypatch) -> None:
    monkeypatch.setattr(discovery.netinfo, "lan_ip", lambda: "192.168.1.20")
    settings = Settings()
    assert discovery.lan_address(settings, ["uvicorn", "--host", "0.0.0.0"]) == "192.168.1.20"
    assert discovery.lan_address(settings, ["uvicorn", "--host", "10.0.0.5"]) == "10.0.0.5"
    assert discovery.lan_address(settings, ["uvicorn", "--port", "8000"]) is None


def test_txt_record_carries_the_protocol() -> None:
    props = discovery.service_properties()
    assert props["protocol"] == "1"
    assert props["ws"] == "/ws/live"


class FakeZeroconf:
    registered: list = []
    closed = 0

    def __init__(self, **_: object) -> None:
        pass

    # Like the real one, each returns a task the caller must await too: the
    # list changes only when it is.
    async def async_register_service(self, info, allow_name_change=False):
        async def announce() -> None:
            FakeZeroconf.registered.append(info)
        return asyncio.ensure_future(announce())

    async def async_unregister_service(self, info):
        async def goodbye() -> None:
            FakeZeroconf.registered.remove(info)
        return asyncio.ensure_future(goodbye())

    async def async_close(self) -> None:
        FakeZeroconf.closed += 1


@pytest.fixture
def fake_zc(monkeypatch):
    import zeroconf.asyncio

    FakeZeroconf.registered = []
    FakeZeroconf.closed = 0
    monkeypatch.setattr(zeroconf.asyncio, "AsyncZeroconf", FakeZeroconf)
    monkeypatch.setattr(discovery.netinfo, "lan_ip", lambda: "192.168.1.20")
    return FakeZeroconf


async def test_advertiser_is_inert_until_started(fake_zc) -> None:
    adv = discovery.Advertiser(Settings())
    await adv.apply()
    assert fake_zc.registered == []


async def test_advertiser_follows_the_setting(fake_zc) -> None:
    settings = Settings(http_port=8123)
    adv = discovery.Advertiser(settings)
    await adv.start()
    [info] = fake_zc.registered
    assert info.type == discovery.SERVICE_TYPE
    assert info.port == discovery.served_port(settings)
    assert adv.advertised["ip"] == "192.168.1.20"
    settings.mdns = False
    await adv.apply()
    assert fake_zc.registered == [] and adv.advertised is None
    settings.mdns = True
    await adv.apply()
    assert len(fake_zc.registered) == 1
    await adv.stop()
    assert fake_zc.registered == []


async def test_loopback_only_server_is_not_advertised(fake_zc, monkeypatch) -> None:
    """#151: under the dev script uvicorn bound 127.0.0.1, and the phone was
    sent to the LAN address on a port that refused it."""
    monkeypatch.setattr(discovery.sys, "argv", ["uvicorn", "app.main:app", "--port", "8000"])
    adv = discovery.Advertiser(Settings())
    await adv.start()
    assert fake_zc.registered == [] and adv.advertised is None
    await adv.stop()


async def test_stats_say_when_the_lan_cannot_reach_the_server(client, monkeypatch) -> None:
    c, _ = client
    monkeypatch.setattr(discovery.netinfo, "lan_ip", lambda: "192.168.1.20")
    monkeypatch.setattr(discovery.sys, "argv", ["uvicorn", "app.main:app", "--port", "8000"])
    stats = (await c.get("/api/admin/stats")).json()
    assert stats["loopback_only"] is True and stats["lan_ip"] == ""
    monkeypatch.setattr(discovery.sys, "argv", ["uvicorn", "app.main:app", "--host", "0.0.0.0"])
    stats = (await c.get("/api/admin/stats")).json()
    assert stats["loopback_only"] is False and stats["lan_ip"] == "192.168.1.20"


async def test_mdns_toggle_in_admin_applies_live(client, fake_zc) -> None:
    c, service = client
    await service.discovery.start()
    assert len(fake_zc.registered) == 1
    await c.put("/api/admin/config", json={"values": {"mdns": False}})
    assert fake_zc.registered == []
    stats = (await c.get("/api/admin/stats")).json()
    assert stats["mdns"] is None and stats["mdns_enabled"] is False
    await service.discovery.stop()
