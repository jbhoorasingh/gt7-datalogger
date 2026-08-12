"""Car category as a grouping key (#19): filtering sessions by it, and the
class benchmark at a circuit."""

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.models import SimulatorFlags
from app.processing.cars import CarDatabase
from app.service import TelemetryService
from app.storage.db import init_db, make_engine, make_session_factory
from app.storage.repository import Repository
from app.telemetry.packet import build_packet, parse_packet

ON_TRACK = int(SimulatorFlags.CAR_ON_TRACK)


@pytest.fixture
async def client(tmp_path):
    settings = Settings(source="udp", db_path=tmp_path / "test.db", ws_rate=1000)
    engine = make_engine(settings.db_path)
    await init_db(engine)
    repo = Repository(make_session_factory(engine))
    service = TelemetryService(settings, repo, CarDatabase())
    service.processor.min_lap_ticks = 1

    app = create_app()
    app.router.lifespan_context = None  # type: ignore[assignment]
    app.state.service = service

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, service
    await engine.dispose()


async def drive(
    service: TelemetryService,
    car_id: int,
    category: str,
    lap_ms: int,
    *,
    first_packet_fmt: str = "C",
) -> None:
    """One recorded lap in a given car and class.

    `first_packet_fmt` exists because the session row's category is taken from
    the FIRST packet of the session — which is often a narrower format than
    the rest of the session runs on.
    """
    for tick in range(30):
        await service._on_packet(
            parse_packet(
                build_packet(
                    packet_id=car_id * 10_000 + tick,
                    current_lap=1,
                    speed_mps=40.0,
                    flags=ON_TRACK,
                    car_id=car_id,
                    fmt=first_packet_fmt if tick == 0 else "C",
                    car_category=category,
                )
            )
        )
    await service._on_packet(
        parse_packet(
            build_packet(
                packet_id=car_id * 10_000 + 99, current_lap=2, last_lap_time_ms=lap_ms,
                speed_mps=40.0, flags=ON_TRACK, car_id=car_id, fmt="C",
                car_category=category,
            )
        )
    )


async def test_sessions_filter_by_category(client) -> None:
    c, service = client
    await drive(service, car_id=1, category="Gr.3", lap_ms=90_000)
    await drive(service, car_id=2, category="Gr.4", lap_ms=95_000)

    everything = (await c.get("/api/sessions")).json()
    assert {s["car_category"] for s in everything} == {"Gr.3", "Gr.4"}

    gr3 = (await c.get("/api/sessions?category=Gr.3")).json()
    assert [s["car_category"] for s in gr3] == ["Gr.3"]
    # Blank means everything, which is also the only way to reach recordings
    # made before packet C.
    assert len((await c.get("/api/sessions?category=")).json()) == 2
    assert (await c.get("/api/sessions?category=Gr.1")).json() == []


async def test_session_category_falls_back_to_its_laps(client) -> None:
    """A session whose first packet was packet A has no category of its own —
    but its laps do, and filtering on the class must still find it."""
    c, service = client
    await drive(service, car_id=1, category="Gr.3", lap_ms=90_000, first_packet_fmt="A")

    sessions = (await c.get("/api/sessions")).json()
    assert sessions[0]["car_category"] == "Gr.3"
    assert len((await c.get("/api/sessions?category=Gr.3")).json()) == 1


async def test_category_best_is_scoped_to_circuit_and_class(client) -> None:
    c, service = client
    await drive(service, car_id=1, category="Gr.3", lap_ms=90_000)
    await drive(service, car_id=2, category="Gr.3", lap_ms=88_000)
    await drive(service, car_id=3, category="Gr.4", lap_ms=85_000)
    for session in (await c.get("/api/sessions")).json():
        await service.repo.set_session_track(session["id"], "Suzuka")

    best = (await c.get("/api/laps/best?track=Suzuka&category=Gr.3")).json()
    assert best["time_ms"] == 88_000
    assert best["car_id"] == 2 and best["track_name"] == "Suzuka"

    # The quicker Gr.4 lap is a different achievement, not a better one.
    assert (await c.get("/api/laps/best?track=Suzuka&category=Gr.4")).json()["time_ms"] == 85_000
    assert (await c.get("/api/laps/best?track=Monza&category=Gr.3")).json() is None
    assert (await c.get("/api/laps/best?track=Suzuka&category=N100")).json() is None


async def test_category_best_ignores_partial_laps(client) -> None:
    """A pit out-lap carries a GT7 lap time that is short because it is not a
    lap — the same reason it never owns a session best."""
    c, service = client
    await drive(service, car_id=1, category="Gr.3", lap_ms=90_000)
    sessions = (await c.get("/api/sessions")).json()
    await service.repo.set_session_track(sessions[0]["id"], "Suzuka")
    lap = (await c.get("/api/laps")).json()[0]
    await service.repo.mark_session_laps_partial(sessions[0]["id"], [lap["number"]])

    assert (await c.get("/api/laps/best?track=Suzuka&category=Gr.3")).json() is None


async def test_best_route_is_not_swallowed_by_the_lap_id_route(client) -> None:
    c, _service = client
    r = await c.get("/api/laps/best?track=Suzuka&category=Gr.3")
    assert r.status_code == 200  # not 422 "best is not an integer"
