"""The sync client (#79): the connection string is parsed once and the token
never shown again; a confirmed bundle is uploaded once per change with a
bearer header and nothing else; the server's answers — accepted, refused,
type disabled, unreachable — each land the adapter where the spec says."""

import json

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.processing import track_bundle
from app.processing.cars import CarDatabase
from app.service import TelemetryService
from app.storage.db import init_db, make_engine, make_session_factory
from app.storage.repository import Repository
from app.sync import (
    BadConnectionString,
    SyncClient,
    SyncError,
    Transport,
    check_token,
    mask_token,
    normalise_server,
    parse_connection_string,
)
from app.sync import tracks as tracks_sync
from app.sync.transport import classify, validate_capabilities
from tests.test_track_manager import _foreign_bundle

SERVER = "https://sync.test"
TOKEN = "gt7s_0123456789abcdefghijklmnop"
OFFICIAL = {
    "track": "Ring", "layout": "GP", "official_id": "ring-gp", "official_name": "Ring GP",
    "turns": 10, "length_m": 4000.0, "reverse": False,
}


# --- the connection string ----------------------------------------------------


def test_connection_string_maps_gt7sync_to_https() -> None:
    conn = parse_connection_string(f"gt7sync://sync.gt7-datalogger.com/?token={TOKEN}")
    assert conn.url == "https://sync.gt7-datalogger.com"
    assert conn.token == TOKEN


def test_connection_string_lan_variants() -> None:
    conn = parse_connection_string(f"gt7sync+http://192.168.1.20:8787/sync/?token={TOKEN}")
    assert conn.url == "http://192.168.1.20:8787/sync"
    assert conn.token == TOKEN
    conn = parse_connection_string(f"  https://my.server/?token={TOKEN}&x=1 ")
    assert conn.url == "https://my.server"
    conn = parse_connection_string(f"gt7sync://[::1]:9000/?token={TOKEN}")
    assert conn.url == "https://[::1]:9000"


@pytest.mark.parametrize(
    "text",
    [
        "",
        "sync.test",  # no scheme
        f"ftp://sync.test/?token={TOKEN}",
        "gt7sync://sync.test/",  # no token
        "gt7sync://sync.test/?token=",
        f"gt7sync://user:pw@sync.test/?token={TOKEN}",  # secrets never in the authority
        "gt7sync://sync.test/?token=has%20space",
        "gt7sync://sync.test:notaport/?token=abc",
    ],
)
def test_connection_string_rejects(text: str) -> None:
    with pytest.raises(BadConnectionString):
        parse_connection_string(text)


def test_server_address_typed_by_hand() -> None:
    assert normalise_server("sync.example.com") == "https://sync.example.com"
    assert normalise_server(" sync.example.com/ ") == "https://sync.example.com"
    assert normalise_server("https://sync.example.com/api/") == "https://sync.example.com/api"
    assert normalise_server("http://192.168.1.20:8787") == "http://192.168.1.20:8787"
    assert normalise_server("gt7sync+http://pi.local:8787") == "http://pi.local:8787"
    assert normalise_server("gt7sync://sync.example.com") == "https://sync.example.com"


@pytest.mark.parametrize(
    "text",
    [
        "",
        "ftp://sync.example.com",
        f"gt7sync://sync.example.com/?token={TOKEN}",  # a connection string in the wrong box
        "sync.example.com:notaport",
        "user:pw@sync.example.com",
    ],
)
def test_server_address_rejects(text: str) -> None:
    with pytest.raises(BadConnectionString):
        normalise_server(text)


def test_check_token() -> None:
    assert check_token(f"  {TOKEN} ") == TOKEN
    for bad in ("", "has space", "tab\there", "x" * 600):
        with pytest.raises(BadConnectionString):
            check_token(bad)


def test_mask_token_shows_enough_to_recognise_and_no_more() -> None:
    assert mask_token("") == ""
    assert mask_token("short") == "••••"
    hint = mask_token(TOKEN)
    assert hint == "…mnop"
    assert TOKEN[:-4] not in hint


# --- error classification -----------------------------------------------------


def _resp(status: int, body=None, headers=None) -> httpx.Response:
    resp = httpx.Response(status, json=body, headers=headers)
    resp.request = httpx.Request("POST", SERVER + "/v1/tracks/uploads")
    return resp


def test_classify_each_status() -> None:
    assert classify(_resp(401)).kind == "auth"
    assert classify(_resp(403, {"error": "type_disabled", "reason": "off"})).kind == "type_disabled"
    assert classify(_resp(403, {"error": "scope"})).kind == "auth"
    assert classify(_resp(409, {"reason": "source bound elsewhere"})).kind == "conflict"
    rejected = classify(_resp(422, {"error": "validation", "reason": "edges: 60000 points"}))
    assert rejected.kind == "rejected"
    assert rejected.message == "edges: 60000 points"
    limited = classify(_resp(429, headers={"Retry-After": "12"}))
    assert limited.kind == "rate_limited"
    assert limited.retry_after == 12.0
    assert classify(_resp(500)).kind == "server"
    assert classify(_resp(500)).transient
    assert not classify(_resp(422)).transient
    # FastAPI's shape, and a proxy's HTML, both reduce to something showable.
    assert classify(_resp(400, {"detail": "bad"})).message == "bad"
    html = httpx.Response(502, text="<html>Bad gateway</html>")
    assert classify(html).kind == "server"


def test_validate_capabilities() -> None:
    caps = validate_capabilities(
        {"server": "gt7-track-sync", "version": "0.1", "types": {"tracks": {"max_mb": 64}}}
    )
    assert caps == {
        "server": "gt7-track-sync", "version": "0.1", "types": {"tracks": {"max_mb": 64}}
    }
    with pytest.raises(SyncError):
        validate_capabilities({"server": "x"})
    with pytest.raises(SyncError):
        validate_capabilities(["tracks"])


# --- a fake service -----------------------------------------------------------


class FakeService:
    """The sync service as httpx sees it: canned answers, recorded requests."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.types: dict = {"tracks": {}}
        self.upload = lambda req: httpx.Response(
            202, json={"upload_id": "up-1", "status": "pending"}
        )
        self.down = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.down:
            raise httpx.ConnectError("connection refused", request=request)
        if request.url.path == "/v1/capabilities":
            return httpx.Response(200, json={"server": "fake", "version": "1", "types": self.types})
        if request.url.path == "/v1/tracks/uploads":
            return self.upload(request)
        return httpx.Response(404, json={"error": "not_found"})

    @property
    def uploads(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path == "/v1/tracks/uploads"]

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


def _settings(tmp_path, **over) -> Settings:
    base = dict(
        source="udp", db_path=tmp_path / "test.db", ws_rate=1000,
        sync_url=SERVER, sync_token=TOKEN, sync_enabled=True, sync_tracks=True,
    )
    return Settings(**{**base, **over})


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def fake() -> FakeService:
    return FakeService()


@pytest.fixture
def sync(tmp_path, fake):
    """A client + adapter with a fake clock and the fake service behind it."""
    settings = _settings(tmp_path)
    persisted: list[tuple[str, str]] = []

    async def persist(key: str, value: str) -> None:
        persisted.append((key, value))

    client = SyncClient(settings, tmp_path, persist=persist)
    client.http = fake.transport
    clock = Clock()
    client.tracks = tracks_sync.TracksAdapter(client, tmp_path, clock=clock)
    client.adapters = {"tracks": client.tracks}
    client.persisted = persisted  # type: ignore[attr-defined]
    client.clock = clock  # type: ignore[attr-defined]
    return client


def _bundle(tmp_path, track="Ring", confirmed=True, n=6) -> str:
    doc = track_bundle.validate_document(_foreign_bundle(track=track, n=n))
    track_bundle.merge_document(tmp_path, doc)
    if confirmed:
        track_bundle.set_official(tmp_path, track, OFFICIAL)
    return track_bundle.slugify(track)


# --- the tracks adapter -------------------------------------------------------


async def test_unconfirmed_bundle_is_never_sent(sync, fake, tmp_path) -> None:
    slug = _bundle(tmp_path, confirmed=False)
    await sync.tracks._push(slug)
    assert fake.uploads == []
    assert sync.tracks.track_status(slug)["status"] == "unconfirmed"
    # ...and nothing is remembered about it.
    assert not (tmp_path / tracks_sync.STATE_FILE).exists()


async def test_a_change_settles_before_it_is_sent(sync, fake, tmp_path) -> None:
    """A running survey writes its bundle every minute; none of those writes
    is an upload. The clock restarts on each, and runs out only once the
    bundle has been left alone."""
    slug = _bundle(tmp_path)
    sync.tracks.changed("Ring")
    await sync.tracks.wait_idle()
    assert fake.uploads == []
    assert sync.tracks.track_status(slug) == {
        "status": "queued", "due_in_s": tracks_sync.SETTLE_S,
    }
    # Six autosaves later, still not due — and the wait has restarted.
    for _ in range(6):
        sync.clock.now += 60
        sync.tracks.changed("Ring")
    assert sync.tracks._due() == []
    assert sync.tracks.track_status(slug)["due_in_s"] == tracks_sync.SETTLE_S
    # Left alone, it goes.
    sync.clock.now += tracks_sync.SETTLE_S
    assert sync.tracks._due() == [slug]
    sync.tracks._kick()
    await sync.tracks.wait_idle()
    assert len(fake.uploads) == 1
    assert sync.tracks.track_status(slug)["status"] == "synced"

    # Another change after that: queued again, the synced record intact.
    sync.tracks.changed("Ring")
    status = sync.tracks.track_status(slug)
    assert status["status"] == "queued"
    assert status["upload_id"] == "up-1"


async def test_sweep_uploads_the_document_now_and_once(sync, fake, tmp_path) -> None:
    slug = _bundle(tmp_path)
    await sync.tracks.sweep()
    await sync.tracks.wait_idle()

    assert len(fake.uploads) == 1
    req = fake.uploads[0]
    assert req.headers["authorization"] == f"Bearer {TOKEN}"
    assert req.headers["content-type"] == "application/json"
    assert TOKEN not in str(req.url)
    body = json.loads(req.content)
    assert body["format"] == track_bundle.BUNDLE_FORMAT
    assert body["meta"]["official"]["official_id"] == "ring-gp"
    assert set(body) == {"format", "version", "meta", "edges", "finish_crossings",
                         "corners", "sections"}

    status = sync.tracks.track_status(slug)
    assert status["status"] == "synced"
    assert status["upload_id"] == "up-1"
    assert status["remote_status"] == "pending"
    assert sync.tracks.status()["state"] == "idle"
    assert sync.tracks.status()["uploads"] == 1

    # The same document again — a save with no new evidence — is not sent
    # again, whatever the timestamp says.
    sync.clock.now += 3600
    track_bundle.set_official(tmp_path, "Ring", OFFICIAL)  # rewrites updated_at only
    await sync.tracks.sweep()
    await sync.tracks.wait_idle()
    assert len(fake.uploads) == 1


async def test_changed_evidence_is_sent_again_after_the_interval(sync, fake, tmp_path) -> None:
    _bundle(tmp_path, n=6)
    await sync.tracks._push("ring")
    assert len(fake.uploads) == 1

    # More evidence, but within the minute: deferred, not dropped.
    doc = track_bundle.validate_document(_foreign_bundle(n=12))
    track_bundle.merge_document(tmp_path, doc)
    sync.clock.now += 10
    await sync.tracks._push("ring")
    assert len(fake.uploads) == 1
    status = sync.tracks.track_status("ring")
    assert status["status"] == "queued"
    assert 45 <= status["due_in_s"] <= 50

    sync.clock.now += 60
    assert sync.tracks._due() == ["ring"]
    await sync.tracks._push("ring")
    assert len(fake.uploads) == 2
    assert len(json.loads(fake.uploads[1].content)["edges"]) == 12


async def test_type_disabled_flips_the_toggle_off_and_persists_it(sync, fake, tmp_path) -> None:
    _bundle(tmp_path)
    fake.upload = lambda req: httpx.Response(
        403, json={"error": "type_disabled", "reason": "tracks are off"}
    )
    await sync.tracks._push("ring")
    assert sync.settings.sync_tracks is False
    assert ("sync_tracks", "false") in sync.persisted
    assert sync.active("tracks") is False
    # Off — and the status line says why, which is the one message that
    # has to outlive the state it describes.
    assert sync.tracks.status()["state"] == "off"
    assert sync.tracks.status()["error"] == "server no longer accepts this"
    # Switched back on by hand, the notice goes with the next sweep.
    sync.settings.sync_tracks = True
    assert "no longer accepts" in sync.tracks.status()["error"]
    await sync.tracks.sweep()
    assert sync.tracks.status()["error"] == ""


async def test_rejected_document_is_not_retried_until_it_changes(sync, fake, tmp_path) -> None:
    _bundle(tmp_path, n=6)
    fake.upload = lambda req: httpx.Response(
        422, json={"error": "validation", "reason": "meta.official is not confirmed"}
    )
    await sync.tracks._push("ring")
    assert len(fake.uploads) == 1
    status = sync.tracks.track_status("ring")
    assert status["status"] == "rejected"
    assert status["error"] == "meta.official is not confirmed"
    # Same document: not sent again, no matter how often it is saved.
    sync.clock.now += 600
    await sync.tracks._push("ring")
    assert len(fake.uploads) == 1
    # A rejection is about the document, not the connection.
    assert sync.tracks.status()["state"] == "idle"

    # New evidence: worth another try.
    track_bundle.merge_document(tmp_path, track_bundle.validate_document(_foreign_bundle(n=9)))
    fake.upload = lambda req: httpx.Response(202, json={"upload_id": "up-2", "status": "pending"})
    await sync.tracks._push("ring")
    assert len(fake.uploads) == 2
    assert sync.tracks.track_status("ring")["status"] == "synced"


async def test_unreachable_server_backs_off_and_retries(sync, fake, tmp_path) -> None:
    _bundle(tmp_path)
    fake.down = True
    await sync.tracks._push("ring")
    status = sync.tracks.track_status("ring")
    assert status["status"] == "error"
    assert status["attempts"] == 1
    assert status["due_in_s"] == tracks_sync.BACKOFF_BASE_S
    assert sync.tracks.status()["state"] == "error"
    assert "connect" in sync.tracks.status()["error"]

    # Not due yet; due after the delay; the delay doubles each time.
    assert sync.tracks._due() == []
    sync.clock.now += tracks_sync.BACKOFF_BASE_S
    assert sync.tracks._due() == ["ring"]
    await sync.tracks._push("ring")
    assert sync.tracks.track_status("ring")["due_in_s"] == 2 * tracks_sync.BACKOFF_BASE_S

    fake.down = False
    sync.clock.now += 2 * tracks_sync.BACKOFF_BASE_S
    await sync.tracks._push("ring")
    assert sync.tracks.track_status("ring")["status"] == "synced"
    assert sync.tracks.status()["state"] == "idle"
    assert sync.tracks.status()["error"] == ""


async def test_rate_limit_honours_retry_after(sync, fake, tmp_path) -> None:
    _bundle(tmp_path)
    fake.upload = lambda req: httpx.Response(429, headers={"Retry-After": "7"})
    await sync.tracks._push("ring")
    assert sync.tracks.track_status("ring")["due_in_s"] == 7


async def test_rejected_token_holds_every_bundle_back(sync, fake, tmp_path) -> None:
    _bundle(tmp_path, "Ring")
    _bundle(tmp_path, "Loop")
    fake.upload = lambda req: httpx.Response(401)
    sync.tracks._dirty.add("loop")  # queued behind the one about to fail
    await sync.tracks._push("ring")
    assert len(fake.uploads) == 1
    assert sync.tracks.status()["state"] == "error"
    assert "token" in sync.tracks.status()["error"]
    # Neither is due: a bad token fails every upload the same way.
    assert sync.tracks._due() == []
    assert sync.tracks.track_status("loop")["due_in_s"] == tracks_sync.BACKOFF_MAX_S


async def test_state_survives_a_restart_and_another_server_does_not(
    sync, fake, tmp_path
) -> None:
    _bundle(tmp_path)
    await sync.tracks._push("ring")
    assert len(fake.uploads) == 1
    saved = json.loads((tmp_path / tracks_sync.STATE_FILE).read_text())
    assert saved["server"] == SERVER
    assert saved["tracks"]["ring"]["status"] == "synced"
    assert saved["tracks"]["ring"]["upload_id"] == "up-1"

    # A new process: the same document is known to be on the server.
    again = tracks_sync.TracksAdapter(sync, tmp_path, clock=sync.clock)
    await again._push("ring")
    assert len(fake.uploads) == 1
    assert again.track_status("ring")["status"] == "synced"

    # A different server: nothing is assumed about what it holds.
    sync.settings.sync_url = "https://other.test"
    fresh = tracks_sync.TracksAdapter(sync, tmp_path, clock=sync.clock)
    assert fresh.track_status("ring") == {"status": "unknown"}
    await fresh._push("ring")
    assert len(fake.uploads) == 2


async def test_forget_drops_the_record(sync, fake, tmp_path) -> None:
    _bundle(tmp_path)
    await sync.tracks._push("ring")
    sync.tracks.forget("ring")
    assert sync.tracks.track_status("ring") == {"status": "unknown"}
    assert json.loads((tmp_path / tracks_sync.STATE_FILE).read_text())["tracks"] == {}


async def test_capabilities_gate_the_type(sync, fake, tmp_path) -> None:
    fake.types = {"sessions": {}}  # a server that takes no tracks
    await sync.check()
    assert sync.offered("tracks") is False
    assert sync.active("tracks") is False
    assert sync.status()["types"]["tracks"]["state"] == "off"
    # The type it does offer, this build cannot send.
    assert sync.status()["types"]["sessions"]["supported"] is False
    assert sync.status()["types"]["sessions"]["state"] == "unsupported"

    fake.types = {"tracks": {}}
    await sync.check()
    assert sync.active("tracks") is True
    assert sync.status()["types"]["tracks"]["offered"] is True


def test_nothing_is_active_without_the_master_switch(tmp_path) -> None:
    client = SyncClient(_settings(tmp_path, sync_enabled=False), tmp_path)
    assert client.configured
    assert not client.enabled
    assert not client.active("tracks")
    assert client.status()["types"]["tracks"]["state"] == "off"
    client = SyncClient(_settings(tmp_path, sync_token=""), tmp_path)
    assert not client.configured
    assert client.status()["url"] == ""
    assert client.status()["token_hint"] == ""


def test_status_never_carries_the_token(tmp_path) -> None:
    client = SyncClient(_settings(tmp_path), tmp_path)
    assert TOKEN not in json.dumps(client.status())


async def test_transport_capabilities_sends_no_token(fake) -> None:
    transport = Transport(SERVER, TOKEN, http=fake.transport)
    await transport.capabilities()
    assert "authorization" not in fake.requests[0].headers
    assert fake.requests[0].headers["user-agent"].startswith("gt7-datalogger/")


# --- through the service and the API ------------------------------------------


@pytest.fixture
async def client(tmp_path, fake):
    settings = _settings(tmp_path, sync_enabled=False, sync_tracks=False, sync_token="")
    engine = make_engine(settings.db_path)
    await init_db(engine)
    repo = Repository(make_session_factory(engine))
    service = TelemetryService(settings, repo, CarDatabase())
    service.sync.http = fake.transport

    app = create_app()
    app.router.lifespan_context = None  # type: ignore[assignment]
    app.state.service = service

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, service, tmp_path
    await service.stop()
    await engine.dispose()


async def test_survey_autosave_reaches_the_adapter(client, tmp_path) -> None:
    _c, service, tmp = client
    # The hook is wired at construction, not discovered at save time — and
    # it fans out: the upload (#79) and the re-judge of the circuit's laps
    # (#91) both wait on the same bundle settling.
    assert service.survey.on_bundle_saved == service._bundle_saved
    service.settings.sync_token = TOKEN
    service.settings.sync_enabled = True
    service.settings.sync_tracks = True
    service.survey.start(tmp, track_width_m=12.0, track="Ring")
    service.survey.finish_crossings.append({"x": 0.0, "z": 0.0, "hx": 1.0, "hz": 0.0, "lap": 1})
    service.survey._save_bundle(count_run=False)
    assert "ring" in service.sync.tracks._dirty
    assert "ring" in service.rejudge.pending()
    service.survey.stop()


async def test_settings_roundtrip_hides_the_token(client, fake) -> None:
    c, service, _tmp = client
    resp = await c.put(
        "/api/admin/settings",
        json={"sync_url": f"gt7sync://sync.test/?token={TOKEN}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["sync_url"] == SERVER
    assert body["sync_token_set"] is True
    assert body["sync_token_hint"] == "…mnop"
    assert TOKEN not in resp.text
    assert service.settings.sync_token == TOKEN
    stored = await service.repo.get_settings()
    assert stored["sync_url"] == SERVER
    assert stored["sync_token"] == TOKEN

    # Toggles, persisted as the other booleans are.
    resp = await c.put("/api/admin/settings", json={"sync_enabled": True, "sync_tracks": True})
    assert resp.json()["sync_enabled"] is True
    assert resp.json()["sync_tracks"] is True
    stored = await service.repo.get_settings()
    assert stored["sync_enabled"] == "true"
    assert stored["sync_tracks"] == "true"
    assert service.sync.active("tracks")

    # Clearing forgets the token and goes back to the hosted default.
    resp = await c.put("/api/admin/settings", json={"sync_url": "", "sync_token": ""})
    assert resp.json()["sync_token_set"] is False
    assert resp.json()["sync_url"] == "https://sync.gt7-datalogger.com"
    assert (await service.repo.get_settings())["sync_token"] == ""
    assert not service.sync.configured


async def test_server_and_token_can_be_set_separately(client, fake) -> None:
    c, service, _tmp = client
    # Address first, as a bare host: read as https, nothing else changes.
    resp = await c.put("/api/admin/settings", json={"sync_url": "sync.test"})
    assert resp.status_code == 200
    assert resp.json()["sync_url"] == SERVER
    assert resp.json()["sync_token_set"] is False
    assert not service.sync.configured
    # Then the token, on its own.
    resp = await c.put("/api/admin/settings", json={"sync_token": f" {TOKEN} "})
    assert resp.json()["sync_token_set"] is True
    assert resp.json()["sync_token_hint"] == "…mnop"
    assert TOKEN not in resp.text
    assert service.settings.sync_token == TOKEN
    assert service.sync.configured
    stored = await service.repo.get_settings()
    assert (stored["sync_url"], stored["sync_token"]) == (SERVER, TOKEN)

    # Capabilities learned for one server are forgotten when it changes.
    await c.post("/api/admin/sync/test")
    assert service.sync.capabilities is not None
    resp = await c.put("/api/admin/settings", json={"sync_url": "http://pi.local:8787/"})
    assert resp.json()["sync_url"] == "http://pi.local:8787"
    assert service.sync.capabilities is None
    assert service.settings.sync_token == TOKEN  # the token is the user's; it stays

    # Both at once, and an empty address means the hosted default.
    resp = await c.put("/api/admin/settings", json={"sync_url": "", "sync_token": ""})
    assert resp.json()["sync_url"] == "https://sync.gt7-datalogger.com"
    assert resp.json()["sync_token_set"] is False

    # A connection string pasted into the address field sets both halves.
    resp = await c.put(
        "/api/admin/settings", json={"sync_url": f"gt7sync+http://pi.local:8787/?token={TOKEN}"}
    )
    assert resp.status_code == 200
    assert resp.json()["sync_url"] == "http://pi.local:8787"
    assert resp.json()["sync_token_set"] is True
    assert TOKEN not in resp.text
    resp = await c.put("/api/admin/settings", json={"sync_token": "not a token"})
    assert resp.status_code == 400


async def test_bad_connection_string_is_a_400(client) -> None:
    c, service, _tmp = client
    resp = await c.put("/api/admin/settings", json={"sync_url": "gt7sync://x/?token="})
    assert resp.status_code == 400
    assert "token" in resp.json()["detail"]
    assert service.settings.sync_token == ""
    assert service.settings.sync_url == SERVER  # untouched


async def test_sync_test_endpoint_reports_capabilities(client, fake) -> None:
    c, _service, _tmp = client
    resp = await c.post("/api/admin/sync/test")
    assert resp.status_code == 502  # nothing configured
    await c.put(
        "/api/admin/settings",
        json={"sync_url": f"gt7sync://sync.test/?token={TOKEN}"},
    )
    resp = await c.post("/api/admin/sync/test")
    assert resp.status_code == 200
    body = resp.json()
    assert body["capabilities"]["server"] == "fake"
    assert body["types"]["tracks"]["offered"] is True
    assert body["checked_at"]

    fake.down = True
    resp = await c.post("/api/admin/sync/test")
    assert resp.status_code == 502
    status = (await c.get("/api/admin/sync")).json()
    assert "connect" in status["capabilities_error"]


async def test_push_and_overview_carry_sync_status(client, fake) -> None:
    c, service, tmp = client
    _bundle(tmp, "Ring")
    _bundle(tmp, "Loop", confirmed=False)

    # Off: the overview rows say nothing about sync.
    overview = (await c.get("/api/track-overview")).json()
    assert overview["sync_tracks"]["active"] is False
    assert all(row["sync"] is None for row in overview["tracks"])
    resp = await c.post("/api/admin/sync/push")
    assert resp.status_code == 400

    await c.put(
        "/api/admin/settings",
        json={
            "sync_url": f"gt7sync://sync.test/?token={TOKEN}",
            "sync_enabled": True,
            "sync_tracks": True,
        },
    )
    resp = await c.post("/api/admin/sync/push")
    assert resp.status_code == 200
    await service.sync.tracks.wait_idle()
    assert len(fake.uploads) == 1

    overview = (await c.get("/api/track-overview")).json()
    assert overview["sync_tracks"] == {"active": True, "state": "idle", "error": ""}
    by_slug = {row["slug"]: row["sync"] for row in overview["tracks"]}
    assert by_slug["ring"]["status"] == "synced"
    assert by_slug["ring"]["upload_id"] == "up-1"
    assert by_slug["loop"]["status"] == "unconfirmed"

    # Confirming the layout is what queues a bundle — to go once it settles,
    # or now on a push.
    resp = await c.patch(
        "/api/track-bundles/loop", json={"official": OFFICIAL, "set_official": True}
    )
    assert resp.status_code == 200
    await service.sync.tracks.wait_idle()
    assert len(fake.uploads) == 1
    overview = (await c.get("/api/track-overview")).json()
    loop = next(row["sync"] for row in overview["tracks"] if row["slug"] == "loop")
    assert loop["status"] == "queued"
    assert 0 < loop["due_in_s"] <= tracks_sync.SETTLE_S
    await c.post("/api/admin/sync/push")
    await service.sync.tracks.wait_idle()
    assert len(fake.uploads) == 2
    # ...and deleting it forgets it.
    await c.delete("/api/track-bundles/loop")
    assert service.sync.tracks.track_status("loop") == {"status": "unknown"}
