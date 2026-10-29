"""Race Engineer WebSocket protocol: capabilities, active speaker, callouts."""

import asyncio
import json

import pytest

from app.api.ws import _handle_client_message
from app.config import Settings
from app.models import SimulatorFlags
from app.processing.cars import CarDatabase
from app.service import TelemetryService
from app.storage.db import init_db, make_engine, make_session_factory
from app.storage.repository import Repository
from app.telemetry.packet import build_packet, parse_packet
from tests.test_ws_broadcast import FakeWS

ON_TRACK = int(SimulatorFlags.CAR_ON_TRACK)


@pytest.fixture
async def service(tmp_path):
    settings = Settings(
        source="udp", db_path=tmp_path / "test.db", ws_rate=1000, telemetry_port=43744
    )
    engine = make_engine(settings.db_path)
    await init_db(engine)
    repo = Repository(make_session_factory(engine))
    svc = TelemetryService(settings, repo, CarDatabase())
    svc.processor.min_lap_ticks = 1
    yield svc
    await svc.stop()
    await engine.dispose()


def send(service: TelemetryService, ws, kind: str, **data: object) -> None:
    _handle_client_message(service, ws, json.dumps({"type": kind, "data": data}))


async def register(service: TelemetryService, ws, client_id: str, page: str = "dash",
                   voice_enabled: bool = True) -> None:
    await service.register(ws)
    send(
        service,
        ws,
        "client_capabilities",
        client_id=client_id,
        page=page,
        voice_supported=True,
        voice_enabled=voice_enabled,
    )


async def test_capabilities_and_claim(service) -> None:
    ws = FakeWS()
    await register(service, ws, "client-a")
    assert service.voice_clients[0]["client_id"] == "client-a"
    assert service.engineer_active is True

    send(service, ws, "claim_voice_output", client_id="client-a")
    assert service.engineer_status()["active_client_id"] == "client-a"
    await asyncio.sleep(0.01)
    status = ws.messages("voice_output_status")[-1]
    assert status["data"]["active_client_id"] == "client-a"


async def test_only_one_active_speaker(service) -> None:
    a, b = FakeWS(), FakeWS()
    await register(service, a, "client-a")
    await register(service, b, "client-b")
    send(service, a, "claim_voice_output", client_id="client-a")
    send(service, b, "claim_voice_output", client_id="client-b")
    assert service.engineer_status()["active_client_id"] == "client-b"
    flags = {c["client_id"]: c["is_active_speaker"] for c in service.voice_clients}
    assert flags == {"client-a": False, "client-b": True}


async def test_overlay_pages_may_not_claim_voice(service) -> None:
    """Several open OBS sources would otherwise all speak at once."""
    ws = FakeWS()
    await register(service, ws, "obs-1", page="overlay")
    send(service, ws, "claim_voice_output", client_id="obs-1")
    assert service.engineer_status()["active_client_id"] == ""


async def test_a_client_cannot_claim_on_behalf_of_another(service) -> None:
    a, b = FakeWS(), FakeWS()
    await register(service, a, "client-a")
    await register(service, b, "client-b")
    send(service, a, "claim_voice_output", client_id="client-b")
    assert service.engineer_status()["active_client_id"] == ""


async def test_release_and_disconnect_clear_the_claim(service) -> None:
    ws = FakeWS()
    await register(service, ws, "client-a")
    send(service, ws, "claim_voice_output", client_id="client-a")
    send(service, ws, "release_voice_output", client_id="client-a")
    assert service.engineer_status()["active_client_id"] == ""

    send(service, ws, "claim_voice_output", client_id="client-a")
    await service.unregister(ws)
    assert service.engineer_status()["active_client_id"] == ""


async def test_refresh_restores_the_previous_claim(service) -> None:
    ws = FakeWS()
    await register(service, ws, "client-a")
    send(service, ws, "claim_voice_output", client_id="client-a")
    await service.unregister(ws)

    again = FakeWS()
    await register(service, again, "client-a")
    assert service.engineer_status()["active_client_id"] == "client-a"


async def test_unparseable_client_messages_are_ignored(service) -> None:
    """Older pages send pings; a garbage frame must not disturb anything."""
    ws = FakeWS()
    await register(service, ws, "client-a")
    for raw in ("ping", "", "[]", '{"type": 5}', '{"type": "claim_voice_output"}'):
        _handle_client_message(service, ws, raw)
    assert service.engineer_status()["active_client_id"] == ""
    assert service.client_count == 1


async def test_detection_is_off_until_a_browser_enables_voice(service) -> None:
    ws = FakeWS()
    await service.register(ws)
    assert service.engineer_active is False
    for i in range(120):
        await service._on_packet(
            parse_packet(build_packet(fmt="C", packet_id=i, current_lap=3, total_laps=3,
                                      speed_mps=40.0, flags=ON_TRACK))
        )
    await asyncio.sleep(0.01)
    assert ws.messages("voice_callout") == []
    assert service.engineer.stats["evaluated"] == 0


async def test_callouts_reach_every_client_on_the_event_lane(service) -> None:
    """Non-speaking pages still get callouts — for captions and diagnostics."""
    driver, viewer = FakeWS(), FakeWS()
    await register(service, driver, "client-a")
    await service.register(viewer)
    send(service, driver, "claim_voice_output", client_id="client-a")

    for i in range(120):
        await service._on_packet(
            parse_packet(build_packet(fmt="C", packet_id=i, current_lap=3, total_laps=3,
                                      speed_mps=40.0, flags=ON_TRACK))
        )
    await asyncio.sleep(0.01)
    for ws in (driver, viewer):
        callouts = ws.messages("voice_callout")
        assert [c["data"]["event_type"] for c in callouts] == ["final_lap"]
        assert callouts[0]["data"]["ttl_ms"] > 0


async def test_acks_are_recorded_but_never_gate_anything(service) -> None:
    ws = FakeWS()
    await register(service, ws, "client-a")
    send(
        service,
        ws,
        "voice_callout_ack",
        callout_id="final_lap-1-1",
        client_id="client-a",
        status="spoken",
        spoken_at_ms=1,
    )
    assert service.engineer.ack_counts == {"spoken": 1}


async def test_new_clients_get_status_but_never_past_callouts(service) -> None:
    first = FakeWS()
    await register(service, first, "client-a")
    send(service, first, "claim_voice_output", client_id="client-a")
    for i in range(120):
        await service._on_packet(
            parse_packet(build_packet(fmt="C", packet_id=i, current_lap=3, total_laps=3,
                                      speed_mps=40.0, flags=ON_TRACK))
        )
    late = FakeWS()
    await service.register(late)
    await asyncio.sleep(0.01)
    assert late.messages("voice_callout") == []
    assert late.messages("race_engineer_status")[0]["data"]["active_client_id"] == "client-a"


async def test_a_finished_lap_is_lined_up_off_the_loop_before_coaching_runs(service) -> None:
    """Coaching compares laps by place (#110): the service has the finished
    lap placed on the reference's axis before the detectors see it."""
    from tests.circle_track import RADIUS, Driver

    await register(service, FakeWS(), "client-a")
    order: list[str] = []
    prepare, on_lap = service.engineer.prepare_lap, service.engineer.on_lap

    async def spy_prepare(lap):  # noqa: ANN001, ANN202
        order.append(f"prepare {lap.number}")
        await prepare(lap)

    def spy_on_lap(lap):  # noqa: ANN001, ANN202
        order.append(f"on_lap {lap.number}")
        return on_lap(lap)

    service.engineer.prepare_lap = spy_prepare  # type: ignore[method-assign]
    service.engineer.on_lap = spy_on_lap  # type: ignore[method-assign]

    driver = Driver()
    radii = [RADIUS, RADIUS + 6.0, RADIUS - 4.0, RADIUS + 3.0, RADIUS + 5.0]
    # The first lap is the quickest, so it stays the reference throughout.
    times = [37_000, 37_700, 37_800, 37_900, 38_000]
    for number, radius in enumerate(radii, start=1):
        previous = times[number - 2] if number > 1 else -1
        for p in driver.lap(number, radius=radius, last_lap_ms=previous):
            await service._on_packet(p)
    await service._on_packet(driver.cross(len(radii) + 1, times[-1]))

    assert order == [f"{step} {n}" for n in range(1, len(radii) + 1)
                     for step in ("prepare", "on_lap")]
    ctx = service.engineer.ctx
    assert ctx.reference is not None
    assert ctx.best_lap_ms == 37_000
    latest = ctx.laps[0]
    assert latest.samples is not ctx.reference
    assert latest.counts_for_best and ctx.span_confirmed
    # Placed by prepare_lap, against the reference in force, by position:
    # five metres wider is 31 m more road, and the two axes differ by it.
    assert latest.axis is not None
    assert latest.axis.reference is ctx.reference
    assert latest.axis.aligned is True
    assert latest.samples["dist"][-1] - latest.axis.samples["dist"][-1] > 20.0

async def test_service_forwards_live_samples_to_sector_observer(
    service, monkeypatch
) -> None:
    """Forward recorded telemetry and interrupt when recording stops."""
    ws = FakeWS()
    await register(service, ws, "sector-test")
    await service.set_recording(True)

    observations = []

    def capture_sector(manager, lap, sample):
        observations.append((lap, sample))
        return None

    monkeypatch.setattr(
        type(service.engineer), "observe_sector", capture_sector
    )

    for i in range(3):
        packet = parse_packet(build_packet(
            fmt="C",
            packet_id=1000 + i,
            current_lap=2,
            speed_mps=40.0,
            flags=ON_TRACK,
        ))
        await service._on_packet(packet)

    assert any(
        lap == 2 and sample is not None
        for lap, sample in observations
    ), "Recorded live samples must reach the sector observer"

    await service.set_recording(False)

    packet = parse_packet(build_packet(
        fmt="C",
        packet_id=1003,
        current_lap=2,
        speed_mps=40.0,
        flags=ON_TRACK,
    ))
    await service._on_packet(packet)

    assert observations[-1] == (2, None)

@pytest.mark.parametrize(
    "invalid_flags",
    [
        pytest.param(
            ON_TRACK | int(SimulatorFlags.PAUSED),
            id="paused",
        ),
        pytest.param(
            int(SimulatorFlags.NONE),
            id="off_track",
        ),
        pytest.param(
            ON_TRACK | int(SimulatorFlags.LOADING),
            id="loading",
        ),
    ],
)
async def test_service_interrupts_sector_tracking_for_invalid_packet(
    service, monkeypatch, invalid_flags
) -> None:
    """Invalid packet states must break live sector position continuity."""
    ws = FakeWS()
    await register(service, ws, "sector-safety-test")
    await service.set_recording(True)

    observations = []

    def capture_sector(manager, lap, sample):
        observations.append((lap, sample))
        return None

    monkeypatch.setattr(
        type(service.engineer), "observe_sector", capture_sector
    )

    valid = parse_packet(build_packet(
        fmt="C",
        packet_id=4101,
        current_lap=2,
        speed_mps=40.0,
        flags=ON_TRACK,
    ))
    await service._on_packet(valid)

    assert observations
    assert observations[-1][0] == 2
    assert observations[-1][1] is not None

    # Same position deliberately tests reuse of a stale recorded sample.
    invalid = parse_packet(build_packet(
        fmt="C",
        packet_id=4102,
        current_lap=2,
        speed_mps=40.0,
        flags=invalid_flags,
    ))
    await service._on_packet(invalid)

    assert observations[-1] == (2, None)

async def test_sector_live_sample_rejects_past_race_finish(service) -> None:
    """Never reuse retained telemetry after the chequered flag."""
    await service.set_recording(True)

    packet = parse_packet(build_packet(
        fmt="C",
        packet_id=8101,
        current_lap=3,
        total_laps=2,
        speed_mps=40.0,
        flags=ON_TRACK,
    ))

    # Simulate a retained sample whose position matches the packet.
    live = service.processor.live_lap_samples
    live["t"].append(50.0)
    live["pos_x"].append(round(packet.position_x, 2))
    live["pos_z"].append(round(packet.position_z, 2))

    assert service._sector_live_sample(packet) is None

async def test_service_rejects_unrecorded_sector_sample(
    service, monkeypatch
) -> None:
    """A new packet must not reuse a sample the processor did not append."""
    ws = FakeWS()
    await register(service, ws, "sector-freshness-test")
    await service.set_recording(True)

    observations = []

    def capture_sector(manager, lap, sample):
        observations.append((lap, sample))
        return None

    monkeypatch.setattr(
        type(service.engineer), "observe_sector", capture_sector
    )

    first = parse_packet(build_packet(
        fmt="C",
        packet_id=9301,
        current_lap=2,
        speed_mps=40.0,
        flags=ON_TRACK,
    ))
    await service._on_packet(first)

    assert observations[-1][0] == 2
    assert observations[-1][1] is not None

    # Simulate a packet that the processor does not append.
    async def skip_feed(processor, packet):
        return None

    monkeypatch.setattr(type(service.processor), "feed", skip_feed)

    second = parse_packet(build_packet(
        fmt="C",
        packet_id=9302,
        current_lap=2,
        speed_mps=40.0,
        flags=ON_TRACK,
    ))
    await service._on_packet(second)

    assert observations[-1] == (2, None)


async def test_service_publishes_live_sector_callout(
    service, monkeypatch
) -> None:
    """A live sector crossing reaches the normal voice publisher."""
    from app.race_engineer.models import CalloutRequest
    from app.race_engineer.sectors import SectorCrossing

    ws = FakeWS()
    await register(service, ws, "sector-publication-test")
    await service.set_recording(True)
    service.engineer.configure(sector_voice_enabled=True)

    published = []

    monkeypatch.setattr(
        service,
        "_publish_callouts",
        lambda callouts: published.extend(callouts),
    )

    def observe_sector(manager, lap, sample):
        if sample is None:
            return None
        return SectorCrossing(
            sector=1,
            time_ms=30050.0,
            cumulative_ms=30050.0,
        )

    def sector_callout(manager, lap, crossing):
        return manager._emit(
            CalloutRequest(
                event_type="sector_time",
                text="Sector one, point four five zero seconds faster.",
                message_key="sector.time",
                dedupe_key=(
                    f"sector_time:{manager.ctx.session_seq}:"
                    f"{lap}:{crossing.sector}"
                ),
            )
        )

    monkeypatch.setattr(
        type(service.engineer), "observe_sector", observe_sector
    )
    monkeypatch.setattr(
        type(service.engineer), "sector_callout", sector_callout
    )

    packet = parse_packet(build_packet(
        fmt="C",
        packet_id=9401,
        current_lap=2,
        speed_mps=40.0,
        flags=ON_TRACK,
    ))

    await service._on_packet(packet)

    sector_events = [
        callout for callout in published
        if callout.event_type == "sector_time"
    ]

    assert len(sector_events) == 1
    assert sector_events[0].category == "lap"
    assert sector_events[0].priority == 55
    assert sector_events[0].ttl_ms == 15_000
