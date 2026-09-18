"""The live adapter (#79): one socket while the car is on track, a frame at
the configured rate in the shape the service reads, one last frame when the
car leaves, a reconnect with backoff on a drop and a hold on the server's
own verdicts; the upgrade refused is read the way a refused upload is."""

import asyncio
import json
import time

import httpx
import pytest
from websockets.asyncio.server import serve
from websockets.datastructures import Headers
from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK, InvalidStatus
from websockets.frames import Close
from websockets.http11 import Response

from app.config import Settings
from app.models import SimulatorFlags
from app.processing.cars import CarDatabase
from app.service import TelemetryService
from app.storage.db import init_db, make_engine, make_session_factory
from app.storage.repository import Repository
from app.sync import SyncClient
from app.sync import live as live_sync
from app.telemetry.packet import build_packet, parse_packet
from tests.test_sync import TOKEN, Clock

SERVER = "https://sync.test"
ON_TRACK = int(SimulatorFlags.CAR_ON_TRACK)
PAUSED = int(SimulatorFlags.PAUSED)
READY = {
    "type": "ready", "user_id": "usr_1", "started_at": "2026-09-17T14:00:00Z",
    "max_hz": 10, "ring_seconds": 30, "recording": False, "spectators": 0,
}


def packet(x=10.0, z=-5.0, speed_kmh=180.0, gear=4, lap=3, flags=ON_TRACK):
    return parse_packet(build_packet(
        position=(x, 1.0, z), speed_mps=speed_kmh / 3.6, current_gear=gear,
        current_lap=lap, flags=flags,
    ))


class FakeSocket:
    """A connection as the adapter sees it, driven by the test."""

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.inbox: asyncio.Queue = asyncio.Queue()
        self.closed: tuple[int, str] | None = None

    async def send(self, message: str) -> None:
        if self.closed is not None:
            raise ConnectionClosedOK(None, Close(*self.closed))
        self.sent.append(json.loads(message))

    async def recv(self) -> str:
        item = await self.inbox.get()
        if isinstance(item, BaseException):
            raise item
        return str(item)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = (code, reason)
        self.inbox.put_nowait(ConnectionClosedOK(None, Close(code, reason)))

    def serve(self, message: dict) -> None:
        self.inbox.put_nowait(json.dumps(message))

    def drop(self, code: int, reason: str = "") -> None:
        self.inbox.put_nowait(ConnectionClosedError(Close(code, reason), None))

    @property
    def frames(self) -> list[dict]:
        return [m for m in self.sent if "type" not in m]


class FakeConnector:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []
        self.sockets: list[FakeSocket] = []
        self.refuse: Exception | None = None
        self.ready: dict | None = dict(READY)

    async def __call__(self, url: str, headers: dict[str, str]) -> FakeSocket:
        self.calls.append((url, headers))
        if self.refuse is not None:
            raise self.refuse
        sock = FakeSocket()
        self.sockets.append(sock)
        if self.ready:
            sock.serve(self.ready)
        return sock

    @property
    def socket(self) -> FakeSocket:
        return self.sockets[-1]


def refused(status: int, body: dict | None = None) -> InvalidStatus:
    return InvalidStatus(Response(
        status, "refused", Headers([("Content-Type", "application/json")]),
        json.dumps(body or {}).encode(),
    ))


async def until(check, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while not check():
        if time.monotonic() > deadline:
            raise AssertionError("waited too long for that to become true")
        await asyncio.sleep(0.01)


def _settings(tmp_path, **over) -> Settings:
    base = dict(
        source="udp", db_path=tmp_path / "data" / "test.db", ws_rate=1000,
        sync_url=SERVER, sync_token=TOKEN, sync_enabled=True, sync_live=True,
    )
    return Settings(**{**base, **over})


def _capabilities(types: dict) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"server": "fake", "version": "1", "types": types})
    return httpx.MockTransport(handler)


@pytest.fixture
async def live(tmp_path):
    settings = _settings(tmp_path)
    persisted: list[tuple[str, str]] = []

    async def persist(key: str, value: str) -> None:
        persisted.append((key, value))

    client = SyncClient(settings, tmp_path, persist=persist)
    client.http = _capabilities({"live": {"max_hz": 10}})
    connector = FakeConnector()
    clock = Clock()
    adapter = live_sync.LiveAdapter(client, connect=connector, clock=clock)
    client.live = adapter
    client.adapters["live"] = adapter
    client.persisted = persisted  # type: ignore[attr-defined]
    await client.check()
    yield adapter, connector, clock
    await client.stop()


# --- frames ---------------------------------------------------------------------


async def test_frames_go_at_the_rate_in_the_shape_the_service_reads(live) -> None:
    adapter, connector, clock = live
    adapter.set_meta(car="Mazda 787B")
    adapter.on_packet(packet(), lambda: 41.25)
    await until(lambda: connector.sockets and connector.socket.frames)

    url, headers = connector.calls[0]
    assert url == "wss://sync.test/v1/live?car=Mazda+787B"
    assert headers["Authorization"] == f"Bearer {TOKEN}"
    assert headers["User-Agent"].startswith("gt7-datalogger/")
    assert TOKEN not in url

    (frame,) = connector.socket.frames
    assert frame == {
        "t": 0.0, "x": 10.0, "z": -5.0, "speed": 180.0, "gear": 4, "lap": 3,
        "lap_time": 41.25, "on_track": True,
    }

    # Too soon: kept, not sent. On the quarter second: sent, the newest one.
    clock.now += 0.1
    adapter.on_packet(packet(x=11.0), lambda: 41.35)
    clock.now += 0.15
    adapter.on_packet(packet(x=12.0), lambda: 41.5)
    await until(lambda: len(connector.socket.frames) == 2)
    assert connector.socket.frames[1]["x"] == 12.0
    assert connector.socket.frames[1]["t"] == 0.25

    await until(lambda: adapter.user_id == "usr_1")
    status = adapter.status()
    assert status["state"] == "connected"
    assert status["live"]["connected"] is True
    assert status["live"]["streaming"] is True
    assert status["live"]["spectate_url"] == "https://sync.test/live/usr_1"
    assert status["live"]["hz"] == 4
    assert status["live"]["frames"] == 2
    assert status["uploads"] == 2
    assert status["error"] == ""


async def test_leaving_the_track_sends_one_last_frame(live) -> None:
    adapter, connector, clock = live
    adapter.on_packet(packet(), lambda: 1.0)
    await until(lambda: connector.sockets and connector.socket.frames)
    clock.now += 1
    adapter.on_packet(packet(flags=0), lambda: 1.0)
    clock.now += 1
    adapter.on_packet(packet(flags=0), lambda: 1.0)
    clock.now += 1
    adapter.on_packet(packet(flags=ON_TRACK | PAUSED), lambda: 1.0)
    await until(lambda: len(connector.socket.frames) == 2)
    await asyncio.sleep(0.05)
    assert [f["on_track"] for f in connector.socket.frames] == [True, False]
    assert adapter.status()["live"]["streaming"] is False
    # Back on the road: frames again, the clock still running.
    clock.now += 1
    adapter.on_packet(packet(), lambda: 2.0)
    await until(lambda: len(connector.socket.frames) == 3)
    assert connector.socket.frames[2]["t"] == 4.0


async def test_the_server_ceiling_caps_the_rate(live) -> None:
    adapter, connector, clock = live
    connector.ready = {**READY, "max_hz": 2}
    adapter.on_packet(packet(), lambda: 0.0)
    await until(lambda: adapter.server_max_hz == 2)
    assert adapter.interval == 0.5
    assert adapter.status()["live"]["hz"] == 2
    adapter.client.settings.sync_live_hz = 1.0
    assert adapter.interval == 1.0


async def test_meta_goes_on_connect_and_again_when_it_changes(live) -> None:
    adapter, connector, clock = live
    adapter.set_meta(official_id="ring-gp", track="Ring GP", car="Car")
    adapter.on_packet(packet(), lambda: 0.0)
    await until(lambda: connector.sockets and connector.socket.frames)
    assert connector.calls[0][0].endswith("?official_id=ring-gp&track=Ring+GP&car=Car")

    adapter.set_meta(track="Ring GP")  # unchanged: nothing to say
    adapter.set_meta(car="Another")
    clock.now += 1
    adapter.on_packet(packet(), lambda: 1.0)
    await until(lambda: len(connector.socket.sent) == 3)
    assert connector.socket.sent[1] == {
        "type": "meta", "official_id": "ring-gp", "track_name": "Ring GP", "car": "Another",
    }
    assert "type" not in connector.socket.sent[2]


async def test_spectators_are_counted(live) -> None:
    adapter, connector, clock = live
    adapter.on_packet(packet(), lambda: 0.0)
    await until(lambda: adapter.user_id == "usr_1")
    connector.socket.serve({"type": "spectators", "n": 2})
    await until(lambda: adapter.spectators == 2)
    connector.socket.serve({"type": "warning", "reason": "that frame was not readable"})
    connector.socket.serve({"type": "spectators", "n": 1})
    await until(lambda: adapter.spectators == 1)


# --- the connection -------------------------------------------------------------


async def test_a_drop_reconnects_with_backoff_and_keeps_the_clock(live) -> None:
    adapter, connector, clock = live
    adapter.on_packet(packet(), lambda: 0.0)
    await until(lambda: connector.sockets and connector.socket.frames)
    connector.socket.drop(1006)
    await until(lambda: adapter.status()["state"] == "error")
    status = adapter.status()
    assert "closed (1006)" in status["error"]
    assert status["due_in_s"] == live_sync.BACKOFF_BASE_S
    assert status["live"]["connected"] is False
    assert len(connector.calls) == 1

    # Frames offered meanwhile are dropped, not queued.
    clock.now += 1
    adapter.on_packet(packet(x=99.0), lambda: 1.0)
    clock.now += live_sync.BACKOFF_BASE_S
    adapter._wake.set()
    await until(lambda: len(connector.calls) == 2)
    clock.now += 1
    adapter.on_packet(packet(x=50.0), lambda: 2.0)
    await until(lambda: connector.socket.frames)
    (frame,) = connector.socket.frames
    assert frame["x"] == 50.0
    assert frame["t"] == clock.now - 1000.0  # the stream's clock did not restart
    assert adapter.status()["state"] == "connected"
    assert adapter.status()["error"] == ""

    # A second drop starts from the base again — the connection in between
    # succeeded — and a reconnect that fails doubles it from there.
    connector.socket.drop(1011, "server going away")
    await until(lambda: adapter.status()["state"] == "error")
    assert adapter.status()["due_in_s"] == live_sync.BACKOFF_BASE_S
    connector.refuse = refused(503)
    clock.now += live_sync.BACKOFF_BASE_S
    adapter._wake.set()
    await until(lambda: adapter.status()["due_in_s"] == 2 * live_sync.BACKOFF_BASE_S)
    assert len(connector.calls) == 3


@pytest.mark.parametrize(
    ("code", "reason", "expected"),
    [
        (live_sync.CLOSE_KICKED, "that is enough", "closed by the server: that is enough"),
        (live_sync.CLOSE_REPLACED, "", "another logger is streaming on this account"),
        (live_sync.CLOSE_RUBBISH, "", "the server could not read the stream: closed"),
    ],
)
async def test_the_servers_verdicts_hold_the_stream(live, code, reason, expected) -> None:
    adapter, connector, clock = live
    adapter.on_packet(packet(), lambda: 0.0)
    await until(lambda: connector.sockets and connector.socket.frames)
    connector.socket.drop(code, reason)
    await until(lambda: adapter.status()["state"] == "error")
    assert adapter.status()["error"] == expected
    assert adapter.status()["due_in_s"] == live_sync.BACKOFF_MAX_S
    # Not reconnected, however many frames are offered.
    clock.now += 60
    adapter.on_packet(packet(), lambda: 0.0)
    await asyncio.sleep(0.05)
    assert len(connector.calls) == 1
    # A person may override the wait.
    adapter.clear_backoff()
    await until(lambda: len(connector.calls) == 2)


async def test_a_refused_upgrade_is_read_like_a_refused_upload(live) -> None:
    adapter, connector, clock = live
    connector.refuse = refused(403, {"error": "type_disabled", "reason": "live is off"})
    adapter.on_packet(packet(), lambda: 0.0)
    await until(lambda: adapter.client.settings.sync_live is False)
    assert ("sync_live", "false") in adapter.client.persisted
    await until(lambda: adapter._task is None)  # the worker stops with the type
    assert adapter.status()["state"] == "off"
    assert adapter.status()["error"] == "server no longer accepts this"
    assert adapter.status()["live"]["connected"] is False

    def again(refuse: Exception) -> None:
        # Switched on again, held no longer, and a frame due: one more try.
        adapter.client.settings.sync_live = True
        adapter.clear_backoff()
        connector.refuse = refuse
        clock.now += 1
        adapter.on_packet(packet(), lambda: 0.0)

    again(refused(401, {"error": "unauthorized", "reason": "token rejected"}))
    await until(lambda: "token" in adapter.status()["error"])
    assert adapter.status()["due_in_s"] == live_sync.BACKOFF_MAX_S

    again(refused(403, {"error": "insufficient_scope", "reason": "no live:write"}))
    await until(lambda: adapter.status()["error"] == "no live:write")
    assert adapter.status()["due_in_s"] == live_sync.BACKOFF_MAX_S

    again(refused(503))
    await until(lambda: "503" in adapter.status()["error"])
    assert adapter.status()["due_in_s"] == live_sync.BACKOFF_BASE_S

    again(refused(426))
    await until(lambda: "live stream" in adapter.status()["error"])
    assert adapter.status()["due_in_s"] == live_sync.BACKOFF_MAX_S

    again(OSError("connection refused"))
    await until(lambda: "connect" in adapter.status()["error"])
    assert adapter.status()["due_in_s"] == live_sync.BACKOFF_BASE_S


async def test_idle_closes_the_socket_and_the_next_car_reopens_it(live, monkeypatch) -> None:
    adapter, connector, clock = live
    monkeypatch.setattr(live_sync, "IDLE_CLOSE_S", 0.05)
    adapter.on_packet(packet(), lambda: 0.0)
    await until(lambda: connector.sockets and connector.socket.frames)
    await until(lambda: connector.socket.closed is not None)
    await until(lambda: adapter._task is None)
    assert connector.socket.closed[0] == 1000
    assert adapter.status()["state"] == "idle"
    assert adapter.status()["error"] == ""

    clock.now += 600
    adapter.on_packet(packet(), lambda: 0.0)
    await until(lambda: len(connector.sockets) == 2 and connector.socket.frames)
    assert connector.socket.frames[0]["t"] == 0.0  # a new stream, a new clock


async def test_switching_the_type_off_closes_the_socket(live) -> None:
    adapter, connector, clock = live
    adapter.on_packet(packet(), lambda: 0.0)
    await until(lambda: connector.sockets and connector.socket.frames)
    adapter.client.settings.sync_live = False
    adapter.client.apply()
    await until(lambda: connector.socket.closed is not None)
    assert adapter.status()["state"] == "off"
    assert adapter.status()["live"]["connected"] is False
    # Off means off: packets do nothing.
    clock.now += 1
    adapter.on_packet(packet(), lambda: 0.0)
    await asyncio.sleep(0.02)
    assert len(connector.calls) == 1


def test_nothing_happens_without_the_toggle(tmp_path) -> None:
    client = SyncClient(_settings(tmp_path, sync_live=False), tmp_path)
    connector = FakeConnector()
    adapter = live_sync.LiveAdapter(client, connect=connector)
    adapter.on_packet(packet(), lambda: 0.0)
    assert connector.calls == []
    assert adapter.status()["state"] == "off"
    assert adapter.status()["live"]["spectate_url"] == ""


# --- through the service --------------------------------------------------------


async def test_the_packet_path_feeds_the_stream(tmp_path) -> None:
    settings = _settings(tmp_path)
    engine = make_engine(settings.db_path)
    await init_db(engine)
    repo = Repository(make_session_factory(engine))
    svc = TelemetryService(settings, repo, CarDatabase())
    svc.sync.http = _capabilities({"live": {}})
    connector = FakeConnector()
    svc.sync.live._connect = connector
    await svc.sync.check()
    try:
        await svc._on_packet(packet(x=3.0, z=4.0, lap=1))
        await until(lambda: connector.sockets and connector.socket.frames)
        (frame,) = connector.socket.frames
        assert (frame["x"], frame["z"], frame["lap"]) == (3.0, 4.0, 1)
        assert frame["lap_time"] >= 0.0
        # The session that opened on that packet named the car for the stream.
        assert svc.session_id is not None
        assert svc.sync.live._meta["car"]
    finally:
        await svc.stop()
        await engine.dispose()


async def test_the_real_client_against_a_loopback_server(tmp_path) -> None:
    """The default connector, end to end: the token as a header, the query
    string, a frame the server reads, the server's `ready` read back."""
    seen: dict = {}
    got_frame = asyncio.Event()

    async def handler(connection) -> None:
        seen["path"] = connection.request.path
        seen["auth"] = connection.request.headers.get("Authorization")
        seen["agent"] = connection.request.headers.get("User-Agent")
        await connection.send(json.dumps({**READY, "user_id": "usr_loop", "max_hz": 5}))
        seen["frame"] = json.loads(await connection.recv())
        got_frame.set()
        await connection.wait_closed()

    async with serve(handler, "127.0.0.1", 0) as server:
        port = next(iter(server.sockets)).getsockname()[1]
        settings = _settings(tmp_path, sync_url=f"http://127.0.0.1:{port}")
        client = SyncClient(settings, tmp_path)
        client.http = _capabilities({"live": {}})
        await client.check()
        client.live.set_meta(official_id="ring-gp", track="Ring", car="Car")
        client.live.on_packet(packet(), lambda: 12.5)
        await asyncio.wait_for(got_frame.wait(), 5)
        await until(lambda: client.live.user_id == "usr_loop")
        assert client.live.interval == 0.25
        await until(lambda: client.live.server_max_hz == 5)
        assert client.live.status()["live"]["spectate_url"] == (
            f"http://127.0.0.1:{port}/live/usr_loop"
        )
        await client.stop()

    assert seen["path"] == "/v1/live?official_id=ring-gp&track=Ring&car=Car"
    assert seen["auth"] == f"Bearer {TOKEN}"
    assert seen["agent"].startswith("gt7-datalogger/")
    assert seen["frame"]["lap_time"] == 12.5
    assert seen["frame"]["speed"] == 180.0
