"""Find console: broadcast discovery that reports without taking over.

A fake console on loopback stands in for the PlayStation: it answers each
heartbeat it receives with a few encrypted packets, sent back to the port
the heartbeat came from, which is what GT7 does. The broadcast address is
pointed at loopback so no datagram leaves the machine.
"""

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.processing.cars import CarDatabase
from app.service import TelemetryService
from app.storage.db import init_db, make_engine, make_session_factory
from app.storage.repository import Repository
from app.telemetry import listener
from app.telemetry.crypto import encrypt_packet
from app.telemetry.packet import build_packet

# High ports so tests never collide with a live server on 33739/33740.
TELEMETRY_PORT = 43742
HEARTBEAT_PORT = 43743
# TEST-NET-1: heartbeats sent here go nowhere and nothing answers.
WRONG_IP = "192.0.2.10"


class FakeConsole(asyncio.DatagramProtocol):
    """Answers every heartbeat with GT7 telemetry, like the console does."""

    def __init__(self) -> None:
        self.heartbeats = 0
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        assert isinstance(transport, asyncio.DatagramTransport)
        self.transport = transport

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self.heartbeats += 1
        assert self.transport is not None
        for i in range(3):
            self.transport.sendto(encrypt_packet(build_packet(packet_id=i)), addr)


@pytest.fixture
def loopback_broadcast(monkeypatch):
    monkeypatch.setattr(listener, "BROADCAST_ADDR", "127.0.0.1")


@pytest.fixture
async def console():
    loop = asyncio.get_running_loop()
    transport, protocol = await loop.create_datagram_endpoint(
        FakeConsole, local_addr=("127.0.0.1", HEARTBEAT_PORT)
    )
    yield protocol
    transport.close()


@pytest.fixture
async def service(tmp_path, loopback_broadcast):
    settings = Settings(
        source="udp",
        ps_ip=WRONG_IP,
        db_path=tmp_path / "test.db",
        ws_rate=1000,
        telemetry_port=TELEMETRY_PORT,
        heartbeat_port=HEARTBEAT_PORT,
    )
    engine = make_engine(settings.db_path)
    await init_db(engine)
    repo = Repository(make_session_factory(engine))
    svc = TelemetryService(settings, repo, CarDatabase())
    yield svc
    await svc.stop()
    await engine.dispose()


@pytest.fixture
async def client(service):
    app = create_app()
    app.router.lifespan_context = None  # type: ignore[assignment]
    app.state.service = service
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def test_finds_the_console_that_answers_the_broadcast(client, service, console) -> None:
    await service.start()
    resp = await client.post("/api/admin/discover-console")
    assert resp.status_code == 200
    assert resp.json() == {"found": True, "ip": "127.0.0.1"}
    assert console.heartbeats >= 1
    # Reported, not applied: the saved IP is the user's to change.
    assert service.settings.ps_ip == WRONG_IP


async def test_a_console_found_by_broadcast_stays_out_of_the_pipeline(service, console) -> None:
    await service.start()
    source = service.source
    assert await source.discover(window=1.0) == "127.0.0.1"
    # Its packets answered only the broadcast: no session blip, and the
    # listener still belongs to the configured console.
    assert source.stats["packets_received"] == 0
    assert source.stats["console_ip"] == WRONG_IP
    assert not source.connected
    # GT7 keeps streaming for a moment after the last heartbeat; that tail
    # is quarantined too.
    source._handle_datagram(encrypt_packet(build_packet()), ("127.0.0.1", HEARTBEAT_PORT))
    assert source.stats["packets_received"] == 0


async def test_the_live_console_keeps_streaming_and_is_what_is_found(service) -> None:
    # The configured console is already streaming; discovery must neither
    # interrupt it nor report anyone else first.
    service.settings.ps_ip = "127.0.0.1"
    await service.start()
    source = service.source
    wire = encrypt_packet(build_packet())
    source._handle_datagram(wire, ("127.0.0.1", 33739))
    task = asyncio.create_task(source.discover(window=1.0))
    await asyncio.sleep(0)
    source._handle_datagram(encrypt_packet(build_packet(packet_id=1)), ("10.0.0.99", 33739))
    source._handle_datagram(encrypt_packet(build_packet(packet_id=2)), ("127.0.0.1", 33739))
    assert await task == "127.0.0.1"
    assert source.stats["packets_received"] == 2
    assert source.stats["console_ip"] == "127.0.0.1"


async def test_no_answer_is_not_found(client, service, monkeypatch) -> None:
    monkeypatch.setattr(listener, "DISCOVERY_WINDOW", 0.3)
    await service.start()
    resp = await client.post("/api/admin/discover-console")
    assert resp.json() == {"found": False, "ip": None}


async def test_garbage_from_another_host_is_not_a_console(service) -> None:
    await service.start()
    source = service.source
    task = asyncio.create_task(source.discover(window=0.3))
    await asyncio.sleep(0)
    source._handle_datagram(b"\x00" * 300, ("10.0.0.5", 33739))
    assert await task is None


async def test_simulated_source_has_no_console(client, service) -> None:
    await service.switch_source("sim")
    resp = await client.post("/api/admin/discover-console")
    assert resp.json() == {"found": False, "ip": None, "reason": "simulated source"}


async def test_stopped_listener_says_so(client) -> None:
    resp = await client.post("/api/admin/discover-console")
    assert resp.json() == {"found": False, "ip": None, "reason": "listener not running"}
