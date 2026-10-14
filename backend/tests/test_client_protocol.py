"""The live client protocol: hello/welcome, per-client rates, frames, events."""

import asyncio
import importlib.util
import json
from pathlib import Path

import pytest

from app import protocol
from app.api.ws import _handle_client_message
from app.config import Settings
from app.processing.cars import CarDatabase
from app.service import TelemetryService
from app.storage.db import init_db, make_engine, make_session_factory
from app.storage.repository import Repository
from tests.test_ws_broadcast import FakeWS, packet


@pytest.fixture
async def service(tmp_path):
    settings = Settings(
        source="udp", db_path=tmp_path / "test.db", ws_rate=10, telemetry_port=43744
    )
    engine = make_engine(settings.db_path)
    await init_db(engine)
    repo = Repository(make_session_factory(engine))
    svc = TelemetryService(settings, repo, CarDatabase())
    svc.processor.min_lap_ticks = 1
    yield svc
    await svc.stop()
    await engine.dispose()


class Clock:
    """Stands in for time.monotonic in the service: packets 1/60 s apart."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch) -> Clock:
    c = Clock()
    monkeypatch.setattr("app.service.time.monotonic", c)
    return c


def send(service: TelemetryService, ws: FakeWS, kind: str, data: dict) -> None:
    _handle_client_message(service, ws, json.dumps({"type": kind, "data": data}))  # type: ignore[arg-type]


async def drive(service: TelemetryService, clock: Clock, seconds: float) -> None:
    for i in range(round(seconds * 60)):
        clock.now += 1 / 60
        await service._on_packet(packet(i))
        # Let every client's sender drain its mailbox before the next packet,
        # so a frame handed out is a frame sent.
        for _ in range(3):
            await asyncio.sleep(0)


async def test_client_without_hello_gets_the_browser_stream(service, clock) -> None:
    ws = FakeWS()
    await service.register(ws)  # type: ignore[arg-type]
    await drive(service, clock, 1.0)
    frames = ws.messages("telemetry")
    assert 9 <= len(frames) <= 11  # GT7_WS_RATE = 10
    assert "packet" not in frames[0]["data"]
    assert not ws.messages("welcome")
    status = ws.messages("status")[0]["data"]
    assert status["protocol"] == protocol.PROTOCOL_VERSION
    protocol.Status.model_validate(status)


async def test_hello_is_welcomed_with_the_subscription_in_effect(service) -> None:
    ws = FakeWS()
    await service.register(ws)  # type: ignore[arg-type]
    send(service, ws, "hello", {
        "protocol": 1, "app": "gt7-phone", "app_version": "0.1.0",
        "subscribe": {"telemetry_hz": 60, "frame": "full", "events": ["lap", "bogus"]},
    })
    await asyncio.sleep(0.01)
    [welcome] = ws.messages("welcome")
    data = protocol.Welcome.model_validate(welcome["data"])
    assert data.protocol == protocol.PROTOCOL_VERSION
    assert data.capabilities.max_telemetry_hz == 60
    assert data.capabilities.default_telemetry_hz == 10
    assert data.subscribe.telemetry_hz == 60
    assert data.subscribe.frame == "full"
    assert data.subscribe.events == ["lap"]  # unknown types dropped


async def test_each_client_streams_at_its_own_rate(service, clock) -> None:
    browser, phone, slow = FakeWS(), FakeWS(), FakeWS()
    for ws in (browser, phone, slow):
        await service.register(ws)  # type: ignore[arg-type]
    send(service, phone, "hello", {"protocol": 1, "subscribe": {"telemetry_hz": 60}})
    send(service, slow, "hello", {"protocol": 1, "subscribe": {"telemetry_hz": 2}})
    await drive(service, clock, 2.0)
    assert len(phone.messages("telemetry")) >= 115  # every packet
    assert 19 <= len(browser.messages("telemetry")) <= 21  # GT7_WS_RATE
    assert 3 <= len(slow.messages("telemetry")) <= 5


async def test_ws_rate_change_applies_to_default_clients_live(service, clock) -> None:
    ws = FakeWS()
    await service.register(ws)  # type: ignore[arg-type]
    service.settings.ws_rate = 30
    await drive(service, clock, 1.0)
    assert 28 <= len(ws.messages("telemetry")) <= 31


async def test_frames_match_the_published_models(service, clock) -> None:
    compact, full = FakeWS(), FakeWS()
    await service.register(compact)  # type: ignore[arg-type]
    await service.register(full)  # type: ignore[arg-type]
    send(service, full, "hello", {"protocol": 1, "subscribe": {"frame": "full"}})
    await drive(service, clock, 0.5)
    # extra="forbid": a field added to _live_frame without the model fails here.
    protocol.TelemetryFrame.model_validate(compact.messages("telemetry")[-1]["data"])
    frame = protocol.FullTelemetryFrame.model_validate(full.messages("telemetry")[-1]["data"])
    raw = full.messages("telemetry")[-1]["data"]["packet"]
    assert set(raw) == set(protocol.PACKET_FIELDS)
    assert frame.packet.speed_mps == pytest.approx(40.0)  # type: ignore[attr-defined]
    # The published schema names every field with its type, not a bare object.
    server = protocol.json_schemas()["server-message"]
    packet = server["$defs"]["Packet"]
    assert set(packet["properties"]) == set(protocol.PACKET_FIELDS)
    assert packet["properties"]["speed_mps"]["type"] == "number"


async def test_event_subscription_filters_but_status_always_arrives(service) -> None:
    ws = FakeWS()
    await service.register(ws)  # type: ignore[arg-type]
    send(service, ws, "hello", {"protocol": 1, "subscribe": {"events": ["lap"]}})
    service.publish_engineer_status()
    service._publish({"type": "lap", "data": {"number": 1}})
    service._publish({"type": "status", "data": {}})
    await asyncio.sleep(0.01)
    # The one on connect predates the hello; none after it.
    assert len(ws.messages("race_engineer_status")) == 1
    assert len(ws.messages("lap")) == 1
    assert len(ws.messages("status")) == 2


async def test_resubscribe_changes_the_stream(service, clock) -> None:
    ws = FakeWS()
    await service.register(ws)  # type: ignore[arg-type]
    send(service, ws, "hello", {"protocol": 1, "subscribe": {"telemetry_hz": 60}})
    await drive(service, clock, 0.5)
    before = len(ws.messages("telemetry"))
    send(service, ws, "subscribe", {"telemetry_hz": 1, "frame": "full"})
    await drive(service, clock, 1.0)
    after = ws.messages("telemetry")[before:]
    assert 1 <= len(after) <= 2
    assert "packet" in after[-1]["data"]


@pytest.mark.parametrize("message", [
    {"type": "hello", "data": {"subscribe": {"telemetry_hz": 60}}},  # no protocol
    {"type": "hello", "data": {"protocol": 1, "subscribe": {"telemetry_hz": 240}}},
    {"type": "subscribe", "data": {"frame": "raw"}},
])
async def test_bad_client_message_is_answered_with_an_error(service, message) -> None:
    ws = FakeWS()
    await service.register(ws)  # type: ignore[arg-type]
    _handle_client_message(service, ws, json.dumps(message))  # type: ignore[arg-type]
    await asyncio.sleep(0.01)
    [error] = ws.messages("error")
    protocol.ProtocolError.model_validate(error["data"])
    assert error["data"]["request"] == message["type"]
    assert not ws.messages("welcome")


async def test_hello_does_not_make_the_app_a_voice_client(service) -> None:
    ws = FakeWS()
    await service.register(ws)  # type: ignore[arg-type]
    send(service, ws, "hello", {"protocol": 1, "client_id": "phone-1", "app": "gt7-phone"})
    assert not service.engineer_active
    assert all(c["client_id"] != "phone-1" for c in service.voice_clients)


def test_published_schemas_are_current() -> None:
    script = Path(__file__).resolve().parents[1] / "scripts" / "export_schemas.py"
    spec = importlib.util.spec_from_file_location("export_schemas", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for path, text in module.documents().items():
        assert path.exists() and path.read_text() == text, (
            f"{path.name} is out of date: run python backend/scripts/export_schemas.py"
        )
