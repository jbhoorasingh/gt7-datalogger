"""API integration tests against an in-memory pipeline (no UDP, no network)."""

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


async def drive_laps(service: TelemetryService, laps: int = 2) -> None:
    # GT7 keeps last_lap_time_ms set to the previous lap's time on every packet.
    for lap in range(1, laps + 1):
        for tick in range(60):
            await service._on_packet(
                parse_packet(
                    build_packet(
                        packet_id=lap * 100 + tick,
                        current_lap=lap,
                        last_lap_time_ms=59_000 if lap > 1 else -1,
                        speed_mps=40.0 + lap,
                        throttle=255,
                        fuel_level=100.0 - lap,
                        flags=ON_TRACK,
                        car_id=7,
                    )
                )
            )
    # Final boundary completes the last lap
    await service._on_packet(
        parse_packet(
            build_packet(
                current_lap=laps + 1,
                last_lap_time_ms=59_000,
                fuel_level=100.0 - laps - 1.5,
                flags=ON_TRACK,
                car_id=7,
            )
        )
    )


async def test_empty_sessions_are_dropped(client) -> None:
    """Menu bounces open sessions that never see a lap; when a new session
    starts, the previous empty one is deleted instead of piling up."""
    c, service = client
    # Session A: one real lap
    await drive_laps(service, laps=1)
    # Session B: car change, no laps driven
    await service._on_packet(
        parse_packet(build_packet(current_lap=1, flags=ON_TRACK, car_id=42))
    )
    # Session C: another car change — session B was empty and must vanish
    await service._on_packet(
        parse_packet(build_packet(current_lap=1, flags=ON_TRACK, car_id=43))
    )
    sessions = (await c.get("/api/sessions")).json()
    lap_counts = [s["lap_count"] for s in sessions]
    assert len(sessions) == 2  # A (1 lap) + C (current, still empty)
    assert sorted(lap_counts) == [0, 1]


async def test_health(client) -> None:
    c, _ = client
    resp = await c.get("/api/health")
    assert resp.status_code == 200


async def test_pipeline_persists_sessions_and_laps(client) -> None:
    c, service = client
    await drive_laps(service, laps=2)

    sessions = (await c.get("/api/sessions")).json()
    assert len(sessions) == 1
    assert sessions[0]["lap_count"] == 2

    laps = (await c.get(f"/api/sessions/{sessions[0]['id']}/laps")).json()
    assert len(laps) == 2
    assert all(lap["time_ms"] == 59_000 or lap["time_ms"] > 0 for lap in laps)

    detail = (await c.get(f"/api/laps/{laps[0]['id']}")).json()
    assert "samples" in detail
    assert len(detail["samples"]["speed"]) == 60


async def test_compare_endpoint(client) -> None:
    c, service = client
    await drive_laps(service, laps=2)
    laps = (await c.get("/api/laps")).json()
    ids = [lap["id"] for lap in laps]
    resp = await c.get(f"/api/analysis/compare?laps={ids[0]},{ids[1]}&ref={ids[1]}")
    assert resp.status_code == 200
    data = resp.json()
    assert str(ids[0]) in data["laps"]
    assert "delta" in data["laps"][str(ids[0])]
    assert "pos_x" in data["laps"][str(ids[1])]["series"]
    assert "peaks_valleys" in data["laps"][str(ids[1])]


async def test_export_import_roundtrip(client) -> None:
    c, service = client
    await drive_laps(service, laps=1)
    laps = (await c.get("/api/laps")).json()
    exported = (await c.get(f"/api/laps/{laps[0]['id']}/export")).json()
    assert exported["format"] == "gt7-datalogger-lap"

    resp = await c.post("/api/laps/import", json=exported)
    assert resp.status_code == 200
    assert len((await c.get("/api/laps")).json()) == 2


async def test_import_rejects_bad_format(client) -> None:
    c, _ = client
    resp = await c.post(
        "/api/laps/import", json={"format": "nope", "version": 1, "lap": {}}
    )
    assert resp.status_code == 400


async def test_fuel_endpoint(client) -> None:
    c, service = client
    await drive_laps(service, laps=1)
    laps = (await c.get("/api/laps")).json()
    resp = await c.get(f"/api/analysis/fuel?lap_id={laps[0]['id']}")
    assert resp.status_code == 200
    assert len(resp.json()["rows"]) == 11


async def test_survey_edges_serve_the_shape_the_map_draws(client, tmp_path) -> None:
    """/survey/edges is the map's only source of border geometry.

    It must carry a resolved `kind` per metre — the frontend colours ticks and
    excludes run-off from the road fill by it, and must never have to reduce
    votes itself — with the evidence alongside for the raw inspector.
    """
    c, service = client
    survey = service.survey
    survey.start(tmp_path, track_width_m=1.6, track="Ring", track_user_set=True)
    survey._append_edge(x=10.0, z=5.0, hx=1.0, hz=0.0, side="L", kind="straddle", pid=1)
    survey._append_edge(x=10.2, z=5.0, hx=1.0, hz=0.0, side="L", kind="runoff", pid=2)

    body = (await c.get("/api/survey/edges")).json()
    assert body["total"] == 1  # one metre of border, not two points
    point = body["points"][0]
    assert point["kind"] == "runoff"  # the hand mark, resolved server-side
    assert point["votes"] == {"straddle": [1, 1], "runoff": [1, 1]}
    assert point["run"] == 1 and point["tw"] == 1.6
    assert {"x", "z", "hx", "hz", "side"} <= set(point)
    survey.stop()
