"""The notify-only update check against GitHub Releases (#196).

Every request here goes to an httpx.MockTransport: nothing touches the network.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from app import updates
from app.config import Settings
from app.main import create_app
from app.processing.cars import CarDatabase
from app.service import TelemetryService
from app.storage.db import init_db, make_engine, make_session_factory
from app.storage.repository import Repository
from app.updates import UpdateChecker, is_newer, parse

T0 = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.t = T0

    def __call__(self) -> datetime:
        return self.t


class GitHub:
    """A stand-in for api.github.com: records requests, answers as told."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.latest: dict | None = {"tag_name": "v0.9.0", "html_url": "https://x/0.9.0"}
        self.listing: list[dict] = []
        self.etag = '"abc"'
        self.status = 200
        self.headers: dict[str, str] = {}
        self.fail = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.fail:
            raise httpx.ConnectError("offline", request=request)
        if self.status != 200:
            return httpx.Response(self.status, headers=self.headers, json={})
        if request.headers.get("if-none-match") == self.etag:
            return httpx.Response(304, headers={"etag": self.etag})
        if request.url.path.endswith("/releases/latest"):
            if self.latest is None:
                return httpx.Response(404, json={"message": "Not Found"})
            return httpx.Response(200, json=self.latest, headers={"etag": self.etag})
        return httpx.Response(200, json=self.listing, headers={"etag": self.etag})

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


@pytest.fixture
async def repo(tmp_path):
    engine = make_engine(tmp_path / "test.db")
    await init_db(engine)
    yield Repository(make_session_factory(engine))
    await engine.dispose()


def checker(repo, gh: GitHub, clock: Clock, *, current="0.8.1", **settings) -> UpdateChecker:
    s = Settings(db_path="unused.db", update_check=True, **settings)
    return UpdateChecker(s, repo, transport=gh.transport, now=clock, current=lambda: current)


# --- precedence ---------------------------------------------------------------


def test_rc_ordering() -> None:
    assert parse("0.9.0-rc.1") < parse("0.9.0-rc.2") < parse("0.9.0")
    assert parse("v0.9.0-rc.10") > parse("v0.9.0-rc.2")
    assert parse("0.8.1") < parse("0.9.0-rc.1")


def test_never_offers_lower_or_equal() -> None:
    assert is_newer("v0.9.0", "0.8.1")
    assert is_newer("v0.9.0-rc.2", "0.9.0-rc.1")
    assert is_newer("v0.9.0", "0.9.0-rc.2")
    assert not is_newer("v0.9.0", "0.9.0")
    assert not is_newer("v0.9.0-rc.2", "0.9.0")
    assert not is_newer("v0.8.0", "0.8.1")
    # A build off a tag is that release, not something below it.
    assert not is_newer("v0.9.0", "0.9.0+g1a2b3c4")
    assert not is_newer("nonsense", "0.8.1")
    assert not is_newer("v0.9.0", "garbage")


# --- the check ----------------------------------------------------------------


async def test_stable_channel_offers_latest(repo) -> None:
    gh, clock = GitHub(), Clock()
    c = checker(repo, gh, clock)
    status = await c.check()
    assert status == {
        "available": True, "version": "0.9.0", "url": "https://x/0.9.0",
        "checked_at": T0.isoformat(timespec="seconds"),
    }
    assert gh.requests[0].url.path == "/repos/jbhoorasingh/gt7-datalogger/releases/latest"
    assert "if-none-match" not in gh.requests[0].headers
    stored = await repo.get_settings()
    assert stored[updates.ETAG_KEY] == '"abc"'
    assert stored[updates.CHECKED_AT_KEY] == T0.isoformat(timespec="seconds")


async def test_stable_channel_not_offered_when_current(repo) -> None:
    gh, clock = GitHub(), Clock()
    status = await checker(repo, gh, clock, current="0.9.0").check()
    assert status["available"] is False
    assert status["version"] is None and status["url"] is None


async def test_prerelease_channel_takes_highest_non_draft(repo) -> None:
    gh, clock = GitHub(), Clock()
    gh.listing = [
        {"tag_name": "v0.9.0-rc.3", "draft": True, "html_url": "https://x/rc3"},
        {"tag_name": "v0.9.0-rc.2", "prerelease": True, "html_url": "https://x/rc2"},
        {"tag_name": "v0.9.0-rc.1", "prerelease": True, "html_url": "https://x/rc1"},
        {"tag_name": "v0.8.1", "html_url": "https://x/0.8.1"},
    ]
    c = checker(repo, gh, clock, current="0.9.0-rc.1", update_channel="prerelease")
    status = await c.check()
    assert gh.requests[0].url.path == "/repos/jbhoorasingh/gt7-datalogger/releases"
    assert gh.requests[0].url.params["per_page"] == "20"
    assert status["available"] and status["version"] == "0.9.0-rc.2"
    assert status["url"] == "https://x/rc2"


async def test_stable_build_on_stable_channel_never_sees_rc(repo) -> None:
    # /releases/latest never names a prerelease; the stable channel asks only it.
    gh, clock = GitHub(), Clock()
    gh.latest = {"tag_name": "v0.8.1", "html_url": "https://x/0.8.1"}
    gh.listing = [{"tag_name": "v0.9.0-rc.1", "html_url": "https://x/rc1"}]
    status = await checker(repo, gh, clock).check()
    assert status["available"] is False
    assert all(r.url.path.endswith("/latest") for r in gh.requests)


async def test_etag_304_reuses_the_stored_answer(repo) -> None:
    gh, clock = GitHub(), Clock()
    await checker(repo, gh, clock).check()
    # A restart: a fresh checker that reads the rows back.
    clock.t += timedelta(days=1, minutes=1)
    c = checker(repo, gh, clock)
    c.load(await repo.get_settings())
    status = await c.check()
    assert gh.requests[-1].headers["if-none-match"] == '"abc"'
    assert status["available"] and status["version"] == "0.9.0"
    assert status["checked_at"] == clock.t.isoformat(timespec="seconds")


async def test_channel_change_does_not_send_the_other_etag(repo) -> None:
    gh, clock = GitHub(), Clock()
    c = checker(repo, gh, clock)
    await c.check()
    c.settings.update_channel = "prerelease"
    assert c.due()  # a different question, so asked now, not in a day
    gh.listing = [{"tag_name": "v0.9.1-rc.1", "html_url": "https://x/rc"}]
    status = await c.check()
    assert "if-none-match" not in gh.requests[-1].headers
    assert status["version"] == "0.9.1-rc.1"


async def test_failure_is_silent_and_keeps_the_last_answer(repo, caplog) -> None:
    gh, clock = GitHub(), Clock()
    c = checker(repo, gh, clock)
    await c.check()
    gh.fail = True
    clock.t += timedelta(days=2)
    status = await c.check()  # does not raise
    assert status["available"] and status["checked_at"] == T0.isoformat(timespec="seconds")
    assert c.next_at == clock.t + updates.INTERVAL
    assert not [r for r in caplog.records if r.levelname in ("ERROR", "WARNING")]


async def test_server_error_is_silent(repo) -> None:
    gh, clock = GitHub(), Clock()
    gh.status = 500
    status = await checker(repo, gh, clock).check()
    assert status["available"] is False and status["checked_at"] is None


async def test_rate_limit_backs_off_until_reset(repo) -> None:
    gh, clock = GitHub(), Clock()
    reset = T0 + timedelta(hours=3)
    gh.status = 403
    gh.headers = {"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(int(reset.timestamp()))}
    c = checker(repo, gh, clock)
    await c.check()
    assert c.next_at == reset and not c.due()
    # A manual check while the limit is in force does not ask again.
    await c.check()
    assert len(gh.requests) == 1
    gh.status = 200
    clock.t = reset + timedelta(seconds=1)
    assert c.due()
    assert (await c.check())["available"] is True
    assert len(gh.requests) == 2


async def test_429_with_retry_after(repo) -> None:
    gh, clock = GitHub(), Clock()
    gh.status = 429
    gh.headers = {"retry-after": "120"}
    c = checker(repo, gh, clock)
    await c.check()
    assert c.next_at == T0 + timedelta(seconds=120)


async def test_no_stable_release_yet(repo) -> None:
    gh, clock = GitHub(), Clock()
    gh.latest = None
    status = await checker(repo, gh, clock).check()
    assert status["available"] is False
    assert status["checked_at"] is not None


async def test_status_after_upgrade_stops_offering(repo) -> None:
    gh, clock = GitHub(), Clock()
    c = checker(repo, gh, clock)
    await c.check()
    c.current = lambda: "0.9.0"
    assert c.status()["available"] is False


# --- the schedule -------------------------------------------------------------


async def test_off_by_default_does_nothing(repo) -> None:
    assert Settings().update_check is False
    gh, clock = GitHub(), Clock()
    s = Settings(db_path="unused.db")
    c = UpdateChecker(s, repo, transport=gh.transport, now=clock, current=lambda: "0.8.1")
    task = asyncio.create_task(c.run())
    await asyncio.sleep(0.05)
    assert gh.requests == []
    assert c.status() == {"available": False, "version": None, "url": None, "checked_at": None}
    # Turned on live: woken, it checks without a restart.
    s.update_check = True
    c.wake()
    for _ in range(50):
        if gh.requests:
            break
        await asyncio.sleep(0.01)
    assert len(gh.requests) == 1
    # And once a day, not again straight away.
    await asyncio.sleep(0.05)
    assert len(gh.requests) == 1
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def test_restart_does_not_recheck(repo) -> None:
    gh, clock = GitHub(), Clock()
    await checker(repo, gh, clock).check()
    clock.t += timedelta(hours=5)
    c = checker(repo, gh, clock)
    c.load(await repo.get_settings())
    assert not c.due()
    assert c.status()["available"] is True
    clock.t = T0 + updates.INTERVAL
    assert c.due()


# --- the API ------------------------------------------------------------------


@pytest.fixture
async def client(tmp_path):
    settings = Settings(
        source="udp", db_path=tmp_path / "test.db", ws_rate=1000, telemetry_port=43741
    )
    engine = make_engine(settings.db_path)
    await init_db(engine)
    service = TelemetryService(settings, Repository(make_session_factory(engine)), CarDatabase())
    gh = GitHub()
    service.updates.transport = gh.transport
    service.updates.current = lambda: "0.8.1"
    app = create_app()
    app.router.lifespan_context = None  # type: ignore[assignment]
    app.state.service = service
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c, service, gh
    await service.stop()
    await engine.dispose()


async def test_stats_carries_update_object_when_off(client) -> None:
    c, _, gh = client
    data = (await c.get("/api/admin/stats")).json()
    assert data["update"] == {"available": False, "version": None, "url": None,
                              "checked_at": None}
    assert gh.requests == []


async def test_update_check_endpoint_and_stats(client) -> None:
    c, service, gh = client
    body = (await c.post("/api/admin/update-check")).json()
    assert set(body) == {"available", "version", "url", "checked_at"}
    assert body["available"] is True and body["version"] == "0.9.0"
    assert len(gh.requests) == 1
    # The daily check is still off, so /stats offers nothing until it is on.
    assert (await c.get("/api/admin/stats")).json()["update"]["available"] is False
    resp = await c.put("/api/admin/config", json={"values": {"update_check": True}})
    assert resp.status_code == 200
    assert service.settings.update_check is True
    update = (await c.get("/api/admin/stats")).json()["update"]
    assert update["available"] is True and update["url"] == "https://x/0.9.0"


async def test_channel_setting_validated(client) -> None:
    c, _, _ = client
    resp = await c.put("/api/admin/config", json={"values": {"update_channel": "nightly"}})
    assert resp.status_code == 400
