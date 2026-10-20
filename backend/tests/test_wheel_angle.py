"""Front road-wheel angle and wheelbase on every lap (packet C).

With both, the yaw rate a car would have with no slip at all is
speed * angle / wheelbase, and understeer or oversteer is measured against
that rather than a learned baseline. `wheel_angle` follows the optional-column
rule (absent, never zero-filled, on a recording without packet C), and
`wheelbase_m` is a per-lap figure, null without packet C.
"""

import io
import json
import zipfile

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.models import SimulatorFlags
from app.processing.cars import CarDatabase
from app.processing.laps import OPTIONAL_COLUMNS, SAMPLE_COLUMNS
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


async def drive(service: TelemetryService, fmt: str, laps: int = 2) -> None:
    """Laps steering left then right, the two front wheels a little apart the
    way Ackermann geometry puts them."""
    for lap in range(1, laps + 1):
        for tick in range(120):
            angle = 0.08 if tick < 60 else -0.05
            await service._on_packet(
                parse_packet(
                    build_packet(
                        packet_id=lap * 1000 + tick,
                        current_lap=lap,
                        last_lap_time_ms=59_000 if lap > 1 else -1,
                        speed_mps=30.0,
                        throttle=255,
                        flags=ON_TRACK,
                        fmt=fmt,
                        wheel_rotation=angle * 12,
                        wheel_steering_rad=(angle + 0.01, angle - 0.01),
                        wheelbase_m=2.6512,
                    )
                )
            )
    await service._on_packet(
        parse_packet(
            build_packet(
                packet_id=99_999, current_lap=laps + 1, last_lap_time_ms=59_000,
                speed_mps=30.0, flags=ON_TRACK, fmt=fmt,
            )
        )
    )


async def _first_lap(c: AsyncClient) -> dict:
    laps = sorted((await c.get("/api/laps")).json(), key=lambda lap: lap["number"])
    return (await c.get(f"/api/laps/{laps[0]['id']}")).json()


def test_the_column_is_optional_and_after_body_slip() -> None:
    assert "wheel_angle" in OPTIONAL_COLUMNS
    # Appended after body_slip; only later additions (engine_torque) follow.
    assert SAMPLE_COLUMNS.index("wheel_angle") == SAMPLE_COLUMNS.index("body_slip") + 1
    assert SAMPLE_COLUMNS[SAMPLE_COLUMNS.index("wheel_angle") + 1:] == ("engine_torque",)


async def test_packet_c_records_the_mean_front_wheel_angle(client) -> None:
    c, service = client
    await drive(service, fmt="C")
    lap = await _first_lap(c)
    samples = lap["samples"]
    assert len(samples["wheel_angle"]) == len(samples["t"])
    # The mean of the two wheels, sign as broadcast.
    assert samples["wheel_angle"][0] == pytest.approx(0.08, abs=1e-4)
    assert samples["wheel_angle"][-1] == pytest.approx(-0.05, abs=1e-4)
    assert lap["wheelbase_m"] == pytest.approx(2.651)


@pytest.mark.parametrize("fmt", ["A", "B"])
async def test_older_formats_record_neither(client, fmt) -> None:
    c, service = client
    await drive(service, fmt=fmt)
    lap = await _first_lap(c)
    assert "wheel_angle" not in lap["samples"]
    assert lap["wheelbase_m"] is None
    if fmt == "B":
        # Packet B still has the steering-wheel input; only the road wheels
        # are missing.
        assert "steer" in lap["samples"]


async def test_lap_export_round_trips_both(client) -> None:
    c, service = client
    await drive(service, fmt="C")
    lap = await _first_lap(c)
    exported = (await c.get(f"/api/laps/{lap['id']}/export")).json()
    assert exported["version"] == 2
    assert exported["lap"]["wheelbase_m"] == pytest.approx(2.651)
    assert "wheel_angle" in exported["lap"]["samples"]

    imported = (await c.post("/api/laps/import", json=exported)).json()["id"]
    back = (await c.get(f"/api/laps/{imported}")).json()
    assert back["wheelbase_m"] == pytest.approx(2.651)
    assert back["samples"]["wheel_angle"] == lap["samples"]["wheel_angle"]


async def test_import_without_them_keeps_them_unknown(client) -> None:
    """A file from a logger that predates both, or recorded on packet A."""
    c, service = client
    await drive(service, fmt="C")
    lap = await _first_lap(c)
    exported = (await c.get(f"/api/laps/{lap['id']}/export")).json()
    del exported["lap"]["wheelbase_m"]
    del exported["lap"]["samples"]["wheel_angle"]

    imported = (await c.post("/api/laps/import", json=exported)).json()["id"]
    back = (await c.get(f"/api/laps/{imported}")).json()
    assert back["wheelbase_m"] is None
    assert "wheel_angle" not in back["samples"]


async def test_import_rejects_a_nonsense_wheelbase(client) -> None:
    c, service = client
    await drive(service, fmt="C")
    lap = await _first_lap(c)
    exported = (await c.get(f"/api/laps/{lap['id']}/export")).json()
    exported["lap"]["wheelbase_m"] = -1.0
    resp = await c.post("/api/laps/import", json=exported)
    assert resp.status_code == 400


async def test_session_archive_and_csv_carry_both(client) -> None:
    c, service = client
    await drive(service, fmt="C")
    lap = await _first_lap(c)

    resp = await c.get(f"/api/sessions/{service.session_id}/export.zip")
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        name = next(n for n in zf.namelist() if f"-{lap['id']}.json" in n)
        doc = json.loads(zf.read(name))
    assert doc["lap"]["wheelbase_m"] == pytest.approx(2.651)
    assert "wheel_angle" in doc["lap"]["samples"]

    lines = (await c.get(f"/api/laps/{lap['id']}/export.csv")).text.splitlines()
    names = lines[7].split(",")
    units = lines[8].split(",")
    i = names.index('"Front Wheel Angle"')
    assert units[i] == '"rad"'
