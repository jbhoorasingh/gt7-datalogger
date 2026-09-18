"""The sessions adapter (#79): a drive reaches the service as the summary at
the first lap, one document per lap in driving order and the totals at the
end; the queue survives an outage and a restart; a refused lap is skipped
and a vanished session is closed; the token and the toggle behave as they
do for tracks."""

import json
import math

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.processing import track_bundle
from app.processing.cars import CarDatabase
from app.processing.laps import CompletedLap, SessionInfo, new_sample_store
from app.processing.tracks import signature_from_samples
from app.service import TelemetryService
from app.storage.db import init_db, make_engine, make_session_factory
from app.storage.repository import Repository
from app.sync import SyncClient
from app.sync import sessions as sessions_sync
from tests.test_sync import OFFICIAL, TOKEN, Clock
from tests.test_track_manager import _foreign_bundle

SERVER = "https://sync.test"


class FakeService:
    """The sessions half of the service: canned answers, recorded requests."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.types: dict = {"sessions": {"max_bytes": 16 * 1024 * 1024}}
        self.down = False
        self.next_id = 1
        # Override a reply per (method, path suffix): lambda req -> Response.
        self.answer: dict[str, object] = {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.down:
            raise httpx.ConnectError("connection refused", request=request)
        path = request.url.path
        if path == "/v1/capabilities":
            return httpx.Response(200, json={"server": "fake", "version": "1", "types": self.types})
        key = f"{request.method} {path}"
        for pattern, reply in self.answer.items():
            if key.endswith(pattern) if not pattern.startswith("*") else pattern[1:] in key:
                return reply(request)  # type: ignore[operator]
        if request.method == "POST" and path == "/v1/sessions":
            sid = f"ses_{self.next_id:020x}"
            self.next_id += 1
            return httpx.Response(201, json={"session_id": sid, "expires_at": None})
        if request.method == "POST" and path.endswith("/laps"):
            body = json.loads(request.content)
            return httpx.Response(
                202,
                json={
                    "session_id": path.split("/")[3],
                    "lap": body["lap"]["number"],
                    "replaced": False,
                    "laps": 1,
                    "best_lap_ms": body["lap"]["time_ms"],
                },
            )
        if request.method == "PATCH" and path.startswith("/v1/sessions/"):
            body = json.loads(request.content)
            return httpx.Response(200, json={"session_id": path.split("/")[3], **body})
        return httpx.Response(404, json={"error": "not_found", "reason": "no such route"})

    def calls(self, method: str | None = None) -> list[httpx.Request]:
        return [
            r for r in self.requests
            if r.url.path.startswith("/v1/sessions") and (method is None or r.method == method)
        ]

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


def _settings(tmp_path, **over) -> Settings:
    base = dict(
        source="udp", db_path=tmp_path / "data" / "test.db", ws_rate=1000,
        sync_url=SERVER, sync_token=TOKEN, sync_enabled=True, sync_sessions=True,
    )
    return Settings(**{**base, **over})


def make_lap(number: int, time_ms: int = 90_000, ticks: int = 12) -> CompletedLap:
    samples = new_sample_store()
    for i in range(ticks):
        for column in samples:
            samples[column].append(float(i))
    return CompletedLap(
        number=number, time_ms=time_ms, finished_at=f"2026-09-17T14:{number:02d}:00Z",
        car_id=3298, samples=samples, fuel_start=42.0, fuel_end=41.0, total_ticks=ticks,
    )


@pytest.fixture
def fake() -> FakeService:
    return FakeService()


@pytest.fixture
async def service(tmp_path, fake):
    """A real service over a real (temporary) database, its sync client
    talking to the fake, with a fake clock on the sessions adapter."""
    settings = _settings(tmp_path)
    engine = make_engine(settings.db_path)
    await init_db(engine)
    repo = Repository(make_session_factory(engine))
    svc = TelemetryService(settings, repo, CarDatabase())
    svc.sync.http = fake.transport
    clock = Clock()
    svc.sync.sessions = sessions_sync.SessionsAdapter(
        svc.sync, settings.db_path.parent,
        load_lap=repo.export_lap, load_stats=repo.session_lap_stats, clock=clock,
    )
    svc.sync.adapters["sessions"] = svc.sync.sessions
    svc.clock = clock  # type: ignore[attr-defined]
    svc._publish = lambda message: None  # type: ignore[method-assign]
    await svc.sync.check()
    yield svc
    await svc.stop()
    await engine.dispose()


async def drive(svc: TelemetryService, laps: int, car_id: int = 3298) -> int:
    await svc._on_session(SessionInfo(car_id=car_id, started_at="2026-09-17T14:00:00Z"))
    for n in range(1, laps + 1):
        await svc._on_lap(make_lap(n, 90_000 - n * 500))
    assert svc.session_id is not None
    return svc.session_id


# --- a drive, start to finish ---------------------------------------------------


async def test_a_drive_reaches_the_service_in_order(service, fake) -> None:
    svc = service
    local = await drive(svc, 2)
    await svc.sync.sessions.wait_idle()

    posts = fake.calls()
    assert [(r.method, r.url.path) for r in posts] == [
        ("POST", "/v1/sessions"),
        ("POST", "/v1/sessions/ses_00000000000000000001/laps"),
        ("POST", "/v1/sessions/ses_00000000000000000001/laps"),
    ]
    for req in posts:
        assert req.headers["authorization"] == f"Bearer {TOKEN}"
        assert req.headers["content-type"] == "application/json"
        assert TOKEN not in str(req.url)

    summary = json.loads(posts[0].content)
    assert summary["car_id"] == 3298
    assert summary["started_at"] == "2026-09-17T14:00:00Z"
    assert summary["car"]  # the inventory's name, or the placeholder
    assert set(summary) <= {"car", "car_id", "official_id", "track_name", "started_at",
                            "source_id"}
    assert summary["source_id"]

    lap = json.loads(posts[1].content)
    assert lap["format"] == "gt7-datalogger-lap"
    assert lap["version"] == 2
    assert lap["lap"]["number"] == 1
    assert lap["lap"]["time_ms"] == 89_500
    assert lap["lap"]["counts_for_best"] is True
    assert len(lap["lap"]["samples"]["t"]) == 12
    assert "pos_x" in lap["lap"]["samples"]

    status = svc.sync.sessions.status()
    assert status["state"] == "idle"
    assert status["uploads"] == 2
    assert status["queued"] == 0
    assert status["sessions"]["current"] == {
        "local_id": local, "remote_id": "ses_00000000000000000001",
        "laps_synced": 2, "laps_queued": 0, "closed": "",
    }
    assert svc.sync.sessions.session_status(local)["status"] == "synced"

    # The next stint closes this one: the totals go, then the new summary.
    await drive(svc, 1)
    await svc.sync.sessions.wait_idle()
    patch = fake.calls("PATCH")
    assert len(patch) == 1
    assert patch[0].url.path == "/v1/sessions/ses_00000000000000000001"
    totals = json.loads(patch[0].content)
    assert totals["laps"] == 2
    assert totals["best_lap_ms"] == 89_000
    assert totals["ended_at"]
    assert fake.calls("POST")[-2].url.path == "/v1/sessions"
    assert fake.calls("POST")[-1].url.path == "/v1/sessions/ses_00000000000000000002/laps"


async def test_a_session_with_no_lap_is_never_announced(service, fake) -> None:
    svc = service
    await svc._on_session(SessionInfo(car_id=1, started_at="2026-09-17T14:00:00Z"))
    await svc._on_session(SessionInfo(car_id=2, started_at="2026-09-17T14:01:00Z"))
    await svc.sync.sessions.wait_idle()
    assert fake.calls() == []
    assert svc.sync.sessions.status()["sessions"] == {
        "synced": 0, "pending": 0, "closed": 0, "laps_rejected": 0,
        "current": {"local_id": svc.session_id, "remote_id": "", "laps_synced": 0,
                    "laps_queued": 0, "closed": ""},
    }


async def test_stopping_the_logger_queues_the_totals(service, fake, tmp_path) -> None:
    svc = service
    await drive(svc, 1)
    await svc.sync.sessions.wait_idle()
    await svc.stop()
    saved = json.loads((tmp_path / "data" / sessions_sync.STATE_FILE).read_text())
    (rec,) = saved["sessions"].values()
    assert rec["end_pending"] is True
    assert rec["remote_id"] == "ses_00000000000000000001"


# --- the queue ------------------------------------------------------------------


async def test_laps_queue_while_the_service_is_away_and_flush_in_order(service, fake) -> None:
    svc = service
    fake.down = True
    await drive(svc, 3)
    await svc.sync.sessions.wait_idle()
    status = svc.sync.sessions.status()
    assert status["state"] == "error"
    assert "connect" in status["error"]
    assert status["queued"] == 3
    assert status["due_in_s"] == sessions_sync.BACKOFF_BASE_S
    assert svc.sync.sessions.session_status(svc.session_id)["status"] == "queued"
    # One attempt was made — the summary — and it is what failed.
    assert len(fake.calls()) == 1

    fake.down = False
    svc.clock.now += sessions_sync.BACKOFF_BASE_S
    svc.sync.sessions._wake.set()
    await svc.sync.sessions.wait_idle()
    paths = [r.url.path for r in fake.calls() if r.method == "POST"][1:]
    assert paths == ["/v1/sessions"] + ["/v1/sessions/ses_00000000000000000001/laps"] * 3
    numbers = [json.loads(r.content)["lap"]["number"] for r in fake.calls("POST")[2:]]
    assert numbers == [1, 2, 3]
    assert svc.sync.sessions.status()["state"] == "idle"
    assert svc.sync.sessions.status()["error"] == ""


async def test_the_queue_survives_a_restart(service, fake, tmp_path) -> None:
    svc = service
    fake.down = True
    local = await drive(svc, 2)
    await svc.sync.sessions.wait_idle()
    svc.sync.sessions.session_ended(local)

    # A new process reads the file and sends everything the old one could not.
    fake.down = False
    again = sessions_sync.SessionsAdapter(
        svc.sync, tmp_path / "data",
        load_lap=svc.repo.export_lap, load_stats=svc.repo.session_lap_stats, clock=svc.clock,
    )
    svc.sync.sessions = again
    svc.sync.adapters["sessions"] = again
    assert again.status()["queued"] == 3  # two laps and the totals
    await again.sweep()
    await again.wait_idle()
    methods = [(r.method, r.url.path.split("/")[-1]) for r in fake.calls()[1:]]
    assert methods == [("POST", "sessions"), ("POST", "laps"), ("POST", "laps"),
                       ("PATCH", "ses_00000000000000000001")]
    assert again.status()["queued"] == 0

    # Another server: nothing is assumed about what it holds.
    svc.settings.sync_url = "https://other.test"
    fresh = sessions_sync.SessionsAdapter(svc.sync, tmp_path / "data", clock=svc.clock)
    assert fresh.status()["sessions"]["synced"] == 0


async def test_the_queue_is_capped(service, fake, monkeypatch) -> None:
    svc = service
    monkeypatch.setattr(sessions_sync, "MAX_QUEUED_LAPS", 4)
    fake.down = True
    await drive(svc, 6)
    await svc.sync.sessions.wait_idle()
    rec = svc.sync.sessions._sessions[svc.session_id]
    assert [n for _, n in rec.queue] == [3, 4, 5, 6]


# --- what the server says -------------------------------------------------------


async def test_a_refused_lap_is_skipped_and_the_rest_go(service, fake) -> None:
    svc = service

    def refuse_lap_two(req: httpx.Request) -> httpx.Response:
        if json.loads(req.content)["lap"]["number"] == 2:
            return httpx.Response(400, json={"error": "invalid_lap",
                                             "reason": "samples.speed has 3 values"})
        return httpx.Response(202, json={"lap": 0, "replaced": False, "laps": 1,
                                         "best_lap_ms": None})

    fake.answer["/laps"] = refuse_lap_two
    local = await drive(svc, 3)
    await svc.sync.sessions.wait_idle()
    assert len(fake.calls("POST")) == 4  # summary + 3 laps, each tried once
    row = svc.sync.sessions.session_status(local)
    assert row["status"] == "synced"
    assert row["laps_synced"] == 2
    assert row["laps_rejected"] == 1
    assert svc.sync.sessions.status()["state"] == "idle"
    assert svc.sync.sessions.status()["sessions"]["laps_rejected"] == 1
    rec = svc.sync.sessions._sessions[local]
    (rejected,) = [v for v in rec.laps.values() if v["status"] == "rejected"]
    assert rejected == {"number": 2, "status": "rejected", "error": "samples.speed has 3 values"}


async def test_a_session_the_server_no_longer_has_is_closed(service, fake) -> None:
    svc = service
    local = await drive(svc, 1)
    await svc.sync.sessions.wait_idle()
    fake.answer["/laps"] = lambda req: httpx.Response(
        404, json={"error": "not_found", "reason": "no such session"}
    )
    await svc._on_lap(make_lap(2))
    await svc._on_lap(make_lap(3))
    await svc.sync.sessions.wait_idle()
    # One attempt told us; the rest were not tried.
    assert len(fake.calls("POST")) == 3
    row = svc.sync.sessions.session_status(local)
    assert row["status"] == "closed"
    assert row["error"] == "no such session"
    assert svc.sync.sessions.status()["queued"] == 0
    # Not a connection problem: the next session is sent normally.
    fake.answer.clear()
    await drive(svc, 1)
    await svc.sync.sessions.wait_idle()
    assert fake.calls("POST")[-1].url.path == "/v1/sessions/ses_00000000000000000002/laps"


async def test_rate_limit_and_server_errors_back_off(service, fake) -> None:
    svc = service
    fake.answer["/v1/sessions"] = lambda req: httpx.Response(429, headers={"Retry-After": "9"})
    await drive(svc, 1)
    await svc.sync.sessions.wait_idle()
    assert svc.sync.sessions.status()["due_in_s"] == 9
    assert svc.sync.sessions.status()["queued"] == 1

    fake.answer["/v1/sessions"] = lambda req: httpx.Response(503)
    svc.clock.now += 9
    svc.sync.sessions._wake.set()
    await svc.sync.sessions.wait_idle()
    status = svc.sync.sessions.status()
    assert status["state"] == "error"
    assert "503" in status["error"]
    assert status["due_in_s"] == 2 * sessions_sync.BACKOFF_BASE_S  # the second failure


async def test_type_disabled_flips_the_toggle_off_and_keeps_the_queue(service, fake) -> None:
    svc = service
    fake.answer["/v1/sessions"] = lambda req: httpx.Response(
        403, json={"error": "type_disabled", "reason": "sessions are off"}
    )
    await drive(svc, 1)
    await svc.sync.sessions.wait_idle()
    assert svc.settings.sync_sessions is False
    assert (await svc.repo.get_settings())["sync_sessions"] == "false"
    assert svc.sync.sessions.status()["state"] == "off"
    assert svc.sync.sessions.status()["error"] == "server no longer accepts this"
    assert svc.sync.sessions.status()["queued"] == 1
    assert svc.sync.sessions.session_status(svc.session_id) is None

    fake.answer.clear()
    svc.settings.sync_sessions = True
    svc.sync.apply()
    await svc.sync.sessions.wait_idle()
    assert svc.sync.sessions.status()["error"] == ""
    assert svc.sync.sessions.status()["queued"] == 0


async def test_rejected_token_holds_everything(service, fake) -> None:
    svc = service
    fake.answer["/v1/sessions"] = lambda req: httpx.Response(401)
    await drive(svc, 2)
    await svc.sync.sessions.wait_idle()
    assert len(fake.calls()) == 1
    status = svc.sync.sessions.status()
    assert status["state"] == "error"
    assert "token" in status["error"]
    assert status["due_in_s"] == sessions_sync.BACKOFF_MAX_S
    assert status["queued"] == 2


async def test_a_lap_ruled_by_hand_is_sent_again(service, fake) -> None:
    svc = service
    local = await drive(svc, 2)
    await svc.sync.sessions.wait_idle()
    lap_one = next(row for row in await svc.repo.list_laps(local) if row["number"] == 1)
    ruled = await svc.repo.set_lap_best_override(lap_one["id"], False, "contact")
    assert ruled is not None
    await svc.apply_best_override(ruled)
    await svc.sync.sessions.wait_idle()
    again = fake.calls("POST")[-1]
    assert again.url.path == "/v1/sessions/ses_00000000000000000001/laps"
    doc = json.loads(again.content)["lap"]
    assert doc["number"] == 1
    assert doc["counts_for_best"] is False
    assert doc["exclude_reason"] == "contact"
    assert svc.sync.sessions.status()["uploads"] == 3


async def test_an_oversized_lap_is_refused_here(service, fake, monkeypatch) -> None:
    svc = service
    fake.types = {"sessions": {"max_bytes": 1024}}
    await svc.sync.check()
    await drive(svc, 1)
    await svc.sync.sessions.wait_idle()
    assert [r.url.path for r in fake.calls("POST")] == ["/v1/sessions"]
    row = svc.sync.sessions.session_status(svc.session_id)
    assert row["laps_rejected"] == 1
    rec = svc.sync.sessions._sessions[svc.session_id]
    (verdict,) = rec.laps.values()
    assert "the server takes" in verdict["error"]


# --- the summary ----------------------------------------------------------------


def test_summary_leaves_out_a_time_it_cannot_read() -> None:
    rec = sessions_sync.SessionSync(local_id=1, car="Car", car_id=1, started_at="now")
    assert "started_at" not in sessions_sync._summary(rec)
    rec.started_at = "2026-09-17T14:03:11.421000+00:00"
    assert sessions_sync._summary(rec)["started_at"] == rec.started_at
    rec.started_at = "2026-09-17T14:03:11Z"
    assert "started_at" in sessions_sync._summary(rec)


async def test_the_summary_carries_the_circuit_once_a_lap_named_it(service, fake) -> None:
    """Identification lands one lap late, and the summary goes with the first
    lap for exactly this reason: it names the circuit, and the layout id a
    confirmed bundle carries for it."""
    svc = service
    lap = make_lap(1, ticks=400)
    for i in range(400):
        lap.samples["pos_x"][i] = 100.0 * math.cos(i / 400 * 2 * math.pi)
        lap.samples["pos_z"][i] = 100.0 * math.sin(i / 400 * 2 * math.pi)
    sig = signature_from_samples(lap.samples)
    assert sig is not None
    await svc.repo.create_track("Ring", sig)
    data_dir = svc.settings.db_path.parent
    track_bundle.merge_document(data_dir, track_bundle.validate_document(_foreign_bundle()))
    track_bundle.set_official(data_dir, "Ring", OFFICIAL)

    await svc._on_session(SessionInfo(car_id=3298, started_at="2026-09-17T14:00:00Z"))
    await svc._on_lap(lap)
    await svc.sync.sessions.wait_idle()
    assert svc.track_name == "Ring"
    summary = json.loads(fake.calls("POST")[0].content)
    assert summary["track_name"] == "Ring"
    assert summary["official_id"] == "ring-gp"
    # ...and the live stream learned the same facts for its `meta`.
    assert svc.sync.live._meta == {"official_id": "ring-gp", "track": "Ring", "car": summary["car"]}


# --- through the API ------------------------------------------------------------


@pytest.fixture
async def client(tmp_path, fake):
    settings = _settings(tmp_path, sync_enabled=False, sync_sessions=False, sync_token="")
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
        yield c, service
    await service.stop()
    await engine.dispose()


async def test_settings_and_status_carry_the_new_types(client, fake) -> None:
    c, service = client
    resp = await c.get("/api/admin/settings")
    assert resp.json()["sync_sessions"] is False
    assert resp.json()["sync_live"] is False

    resp = await c.put(
        "/api/admin/settings",
        json={"sync_url": f"gt7sync://sync.test/?token={TOKEN}", "sync_enabled": True,
              "sync_sessions": True, "sync_live": True},
    )
    assert resp.status_code == 200
    assert resp.json()["sync_sessions"] is True
    assert resp.json()["sync_live"] is True
    stored = await service.repo.get_settings()
    assert stored["sync_sessions"] == "true"
    assert stored["sync_live"] == "true"

    status = (await c.post("/api/admin/sync/test")).json()
    assert status["types"]["sessions"]["supported"] is True
    assert status["types"]["sessions"]["active"] is True
    assert status["types"]["sessions"]["state"] == "idle"
    assert status["types"]["sessions"]["sessions"]["synced"] == 0
    # The fake offers no live: wanted, supported, not offered.
    assert status["types"]["live"]["supported"] is True
    assert status["types"]["live"]["offered"] is False
    assert status["types"]["live"]["active"] is False
    assert status["types"]["live"]["state"] == "off"
    assert status["types"]["live"]["live"]["connected"] is False

    # Push: one type, or every active one.
    resp = await c.post("/api/admin/sync/push?type=live")
    assert resp.status_code == 400
    resp = await c.post("/api/admin/sync/push?type=sessions")
    assert resp.status_code == 200
    resp = await c.post("/api/admin/sync/push")
    assert resp.status_code == 200
    resp = await c.post("/api/admin/sync/push?type=nonsense")
    assert resp.status_code == 400


def test_client_without_loaders_still_stands_up(tmp_path) -> None:
    client = SyncClient(_settings(tmp_path), tmp_path)
    assert set(client.adapters) == {"tracks", "sessions", "live"}
    status = client.status()
    assert status["types"]["sessions"]["supported"] is True
    assert status["types"]["live"]["supported"] is True
    assert TOKEN not in json.dumps(status)
