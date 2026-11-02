"""The listener's failure modes: a taken port, send errors, strangers, lost laps.

#181: UDP 33740 held by another program used to take the whole server down;
switching to the PlayStation while it was held left no source at all; and
asyncio's UDP errors were never looked at. #185: the console was adopted
from any datagram before it was decrypted. #207: a lap that failed to save
was only a log line. #184: a stop left the open session unclosed.
"""

from __future__ import annotations

import asyncio
import errno
import socket
import sys

import pytest
from httpx import ASGITransport, AsyncClient

from app.api import admin
from app.config import Settings
from app.main import create_app
from app.processing.cars import CarDatabase
from app.service import TelemetryService
from app.storage.db import init_db, make_engine, make_session_factory
from app.storage.repository import Repository
from app.telemetry import listener
from app.telemetry.crypto import encrypt_packet
from app.telemetry.listener import UdpTelemetrySource
from app.telemetry.packet import build_packet
from app.telemetry.simulator import SimTelemetrySource

TELEMETRY_PORT = 43752
HEARTBEAT_PORT = 43753
CONSOLE = "192.0.2.20"
STRANGER = "192.0.2.99"


@pytest.fixture
def held_port():
    """UDP TELEMETRY_PORT bound by someone else, as Docker or a second copy would."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", TELEMETRY_PORT))
    yield sock
    sock.close()


async def _service(tmp_path, **overrides) -> tuple[TelemetryService, object]:
    settings = Settings(
        _env_file=None,
        db_path=tmp_path / "test.db",
        telemetry_port=TELEMETRY_PORT,
        heartbeat_port=HEARTBEAT_PORT,
        **{"source": "udp", "ps_ip": "", **overrides},
    )
    engine = make_engine(settings.db_path)
    await init_db(engine)
    repo = Repository(make_session_factory(engine))
    return TelemetryService(settings, repo, CarDatabase()), engine


@pytest.fixture
async def service(tmp_path):
    svc, engine = await _service(tmp_path)
    yield svc
    await svc.stop()
    await engine.dispose()  # type: ignore[attr-defined]


def _wire(packet_id: int = 0) -> bytes:
    return encrypt_packet(build_packet(packet_id=packet_id))


# --- #181 -------------------------------------------------------------------


async def test_a_taken_port_keeps_the_server_up_and_says_why(service, held_port) -> None:
    await service.start()
    status = await service.status()
    assert not service.source.running
    assert "UDP 43752 is in use by another program" in status["listener_error"]


async def test_switching_onto_a_taken_port_puts_the_simulator_back(tmp_path, held_port) -> None:
    svc, engine = await _service(tmp_path, source="sim")
    app = create_app()
    app.router.lifespan_context = None  # type: ignore[assignment]
    app.state.service = svc
    await svc.start()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.put("/api/admin/settings", json={"source": "udp"})
        assert r.status_code == 400
        assert "in use by another program" in r.json()["detail"]
        assert svc.settings.source == "sim"
        assert isinstance(svc.source, SimTelemetrySource)
        assert (await svc.status())["listener_error"] is None
    finally:
        await svc.stop()
        await engine.dispose()  # type: ignore[attr-defined]


async def test_a_port_change_still_raises_for_its_own_rollback(service, held_port) -> None:
    """_apply_generic restores the old port when restart_source raises."""
    service.settings.telemetry_port = TELEMETRY_PORT + 2
    await service.start()
    service.settings.telemetry_port = TELEMETRY_PORT
    with pytest.raises(OSError):
        await service.restart_source()
    assert admin._FALLIBLE  # the caller that relies on the raise


def test_a_send_error_shows_in_status_with_a_reason(service) -> None:
    source = service.source
    assert isinstance(source, UdpTelemetrySource)
    source._on_error(OSError(errno.EHOSTUNREACH, "No route to host"))
    error = source.stats["heartbeat_error"]
    assert error["errno"] == errno.EHOSTUNREACH
    if sys.platform == "darwin":
        assert "Local Network" in error["reason"]
    source._handle_datagram(_wire(), (CONSOLE, HEARTBEAT_PORT))
    assert source.stats["heartbeat_error"] is None


async def test_receive_errors_back_off(service, monkeypatch) -> None:
    source = service.source
    assert isinstance(source, UdpTelemetrySource)
    reopened: list[float] = []

    async def fake_reopen(delay: float) -> None:
        reopened.append(delay)

    monkeypatch.setattr(listener.os, "name", "nt")
    monkeypatch.setattr(source, "_reopen", fake_reopen)
    source._running = True
    for _ in range(3):
        source._on_error(OSError(errno.ECONNRESET, "reset"))
    await asyncio.gather(*source._tasks)
    source._tasks.clear()
    source._running = False
    assert reopened == [1.0, 2.0, 5.0]


# --- #185 -------------------------------------------------------------------


def test_junk_is_never_adopted_as_the_console(service) -> None:
    source = service.source
    assert isinstance(source, UdpTelemetrySource)
    source._handle_datagram(b"\x00" * 400, (STRANGER, 1234))
    assert source._console_addr is None
    assert not source.connected
    assert source.stats["decode_errors"] == 1
    source._handle_datagram(_wire(), (CONSOLE, HEARTBEAT_PORT))
    assert source._console_addr == (CONSOLE, HEARTBEAT_PORT)
    source._handle_datagram(b"\x00" * 400, (STRANGER, 1234))
    assert source._console_addr == (CONSOLE, HEARTBEAT_PORT)
    assert source._heartbeat_targets() == [CONSOLE]


def test_with_an_ip_saved_strangers_are_dropped_unread(service) -> None:
    source = service.source
    assert isinstance(source, UdpTelemetrySource)
    service.settings.ps_ip = CONSOLE
    source._handle_datagram(_wire(0), (STRANGER, HEARTBEAT_PORT))
    assert source.stats["packets_received"] == 0
    assert source.stats["decode_errors"] == 0
    source._handle_datagram(_wire(1), (CONSOLE, HEARTBEAT_PORT))
    assert source.stats["packets_received"] == 1


async def test_a_hostname_is_resolved_once(service, monkeypatch) -> None:
    source = service.source
    assert isinstance(source, UdpTelemetrySource)
    service.settings.ps_ip = "playstation.lan"
    calls: list[str] = []

    async def fake_getaddrinfo(host, port, **kwargs):
        calls.append(host)
        return [(socket.AF_INET, socket.SOCK_DGRAM, 0, "", (CONSOLE, 0))]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", fake_getaddrinfo)
    assert await source._console_ip() == CONSOLE
    assert await source._console_ip() == CONSOLE
    assert calls == ["playstation.lan"]
    assert source._heartbeat_targets(CONSOLE) == [CONSOLE]
    source._handle_datagram(_wire(), (CONSOLE, HEARTBEAT_PORT))
    assert source.stats["packets_received"] == 1


def test_decode_warnings_are_rate_limited(service, caplog) -> None:
    source = service.source
    assert isinstance(source, UdpTelemetrySource)
    for _ in range(50):
        source._handle_datagram(b"\x00" * 400, (STRANGER, 1234))
    warned = [r for r in caplog.records if "failed to decode" in r.message]
    assert len(warned) == 1
    assert source.stats["decode_errors"] == 50


# --- #207 / #184 --------------------------------------------------------------


async def test_a_lap_that_cannot_be_saved_is_counted(tmp_path, monkeypatch) -> None:
    svc, engine = await _service(tmp_path, source="sim")
    try:
        sid = await svc.repo.create_session(
            build_session_info(), svc.cars.get(0), bests_excluded=False
        )
        svc.session_id = sid

        async def broken(*_args, **_kwargs):
            raise OSError(errno.ENOSPC, "No space left on device")

        monkeypatch.setattr(svc.repo, "save_lap", broken)
        await svc._on_lap(build_lap())
        status = await svc.status()
        assert status["save_errors"] == 1
        assert "No space left" in status["last_save_error"]["error"]
    finally:
        await svc.stop()
        await engine.dispose()  # type: ignore[attr-defined]


async def test_a_stop_drops_the_open_session_when_it_has_no_laps(tmp_path) -> None:
    svc, engine = await _service(tmp_path, source="sim")
    try:
        sid = await svc.repo.create_session(
            build_session_info(), svc.cars.get(0), bests_excluded=False
        )
        svc.session_id = sid
        await svc.stop()
        assert svc.session_id is None
        assert await svc.repo.get_session(sid) is None
    finally:
        await engine.dispose()  # type: ignore[attr-defined]


def build_session_info():
    from datetime import UTC, datetime

    from app.processing.laps import SessionInfo

    return SessionInfo(car_id=0, started_at=datetime.now(UTC).isoformat())


def build_lap():
    from datetime import UTC, datetime

    from app.processing.laps import CompletedLap

    lap = CompletedLap(
        number=1, time_ms=90_000, finished_at=datetime.now(UTC).isoformat(), car_id=0,
        samples={"t": [0.0, 1.0], "dist": [0.0, 50.0], "fuel": [1.0, 1.0]},
        fuel_start=1.0, fuel_end=1.0,
    )
    return lap
