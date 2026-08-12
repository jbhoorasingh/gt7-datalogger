"""Tier 1: per-corner channels, event detection, aid metrics, channel API."""

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.models import AidsBits, SimulatorFlags
from app.processing.cars import CarDatabase
from app.processing.events import detect_events
from app.processing.laps import (
    OPTIONAL_COLUMNS,
    SAMPLE_COLUMNS,
    CompletedLap,
    new_sample_store,
)
from app.service import TelemetryService
from app.storage.db import init_db, make_engine, make_session_factory
from app.storage.repository import Repository
from app.telemetry.packet import build_packet, parse_packet

ON_TRACK = int(SimulatorFlags.CAR_ON_TRACK)


# --- event detection (pure) --------------------------------------------------


def _base_samples(n: int = 100) -> dict[str, list[float]]:
    s = new_sample_store()
    for i in range(n):
        s["t"].append(i / 60)
        s["dist"].append(float(i * 5))
        s["speed"].append(150.0)
        s["throttle"].append(0.0)
        s["brake"].append(0.0)
        s["coast"].append(1.0)
        s["gear"].append(4.0)
        s["rpm"].append(6000.0)
        s["boost"].append(0.0)
        s["tire_slip"].append(1.0)
        s["yaw_rate"].append(0.0)
        s["pos_x"].append(float(i))
        s["pos_z"].append(0.0)
        s["body_height"].append(80.0)
        s["fuel"].append(50.0)
        s["aids"].append(0.0)
        for w in ("fl", "fr", "rl", "rr"):
            s[f"slip_{w}"].append(1.0)
            s[f"tt_{w}"].append(75.0)
            s[f"sus_{w}"].append(30.0)
    return s


def test_detect_lockup() -> None:
    s = _base_samples()
    for i in range(40, 50):  # 10 ticks of front-left lockup under braking
        s["brake"][i] = 100.0
        s["slip_fl"][i] = 0.7
    events = detect_events(s)
    lockups = [e for e in events if e["type"] == "lockup"]
    assert len(lockups) == 1
    assert lockups[0]["wheels"] == ["fl"]
    assert lockups[0]["start_dist"] == 200.0
    assert lockups[0]["severity"] == pytest.approx(0.7)


def test_detect_wheelspin() -> None:
    s = _base_samples()
    for i in range(10, 20):
        s["throttle"][i] = 100.0
        s["slip_rl"][i] = 1.3
        s["slip_rr"][i] = 1.25
    spins = [e for e in detect_events(s) if e["type"] == "wheelspin"]
    assert len(spins) == 1
    assert spins[0]["wheels"] == ["rl", "rr"]
    assert spins[0]["severity"] == pytest.approx(1.3)


def test_detect_bottoming_and_kerb() -> None:
    s = _base_samples()
    for i in range(60, 66):  # sustained near-max compression
        s["sus_fr"][i] = 60.0
    s["sus_rl"][30] = 55.0  # single-tick spike = kerb strike
    types = {e["type"] for e in detect_events(s)}
    assert "bottoming" in types
    assert "kerb" in types


def test_no_events_on_clean_lap() -> None:
    assert detect_events(_base_samples()) == []


def test_old_samples_without_new_columns() -> None:
    s = {k: v for k, v in _base_samples().items() if not k.startswith(("slip_", "sus_", "tt_"))}
    s.pop("aids", None)
    assert detect_events(s) == []
    lap = CompletedLap(
        number=1, time_ms=60_000, finished_at="", car_id=0,
        samples=s, fuel_start=10.0, fuel_end=9.0,
    )
    lap.compute_metrics()  # must not raise
    assert lap.tcs_active_pct == 0.0


def test_aid_metrics() -> None:
    s = _base_samples(100)
    s["aids"] = [float(AidsBits.TCS)] * 25 + [float(AidsBits.ASM | AidsBits.TCS)] * 25 + [0.0] * 50
    lap = CompletedLap(
        number=1, time_ms=60_000, finished_at="", car_id=0,
        samples=s, fuel_start=10.0, fuel_end=9.0,
    )
    lap.compute_metrics()
    assert lap.tcs_active_pct == pytest.approx(50.0)
    assert lap.asm_active_pct == pytest.approx(25.0)


# --- pipeline + API ----------------------------------------------------------


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
    for lap in range(1, laps + 1):
        for tick in range(60):
            braking = 20 <= tick < 40
            await service._on_packet(
                parse_packet(
                    build_packet(
                        packet_id=lap * 100 + tick,
                        current_lap=lap,
                        last_lap_time_ms=59_000 if lap > 1 else -1,
                        engine_rpm=5000.0,
                        speed_mps=40.0,
                        throttle=0 if braking else 255,
                        brake=255 if braking else 0,
                        fuel_level=100.0 - lap,
                        flags=ON_TRACK | int(SimulatorFlags.TCS_ACTIVE),
                        # FL locks while braking
                        wheel_rps=(
                            40.0 / 0.33 * (0.7 if braking else 1.0),
                            40.0 / 0.33, 40.0 / 0.33, 40.0 / 0.33,
                        ),
                        suspension=(0.05 if braking else 0.03, 0.03, 0.03, 0.03),
                        oil_pressure=5.5,
                        water_temp=88.0,
                        oil_temp=95.0,
                        gear_ratios=(3.2, 2.3, 1.8),
                        transmission_top_speed=290.0,
                    )
                )
            )
    # closing boundary packet completes the last lap (gearing is captured
    # from this packet, so it must carry the same tune)
    await service._on_packet(
        parse_packet(
            build_packet(
                packet_id=9999, current_lap=laps + 1, last_lap_time_ms=59_000,
                speed_mps=40.0, flags=ON_TRACK, fuel_level=100.0 - laps,
                gear_ratios=(3.2, 2.3, 1.8), transmission_top_speed=290.0,
            )
        )
    )


async def test_lap_has_new_columns_and_metrics(client) -> None:
    c, service = client
    await drive_laps(service)
    laps = (await c.get("/api/laps")).json()
    assert len(laps) >= 1
    lap = laps[0]
    assert lap["tcs_active_pct"] == pytest.approx(100.0)
    assert lap["max_water_temp"] == pytest.approx(88.0)
    assert lap["max_oil_temp"] == pytest.approx(95.0)
    assert lap["min_oil_pressure"] == pytest.approx(5.5)
    assert lap["event_counts"].get("lockup", 0) >= 1

    detail = (await c.get(f"/api/laps/{lap['id']}")).json()
    assert detail["gearing"]["ratios"] == [3.2, 2.3, 1.8]
    assert any(e["type"] == "lockup" and "fl" in e["wheels"] for e in detail["events"])
    # Every column the base packet format can fill. The optional ones (#15,
    # #16, #18) need packet B/~ and are absent here on purpose — see
    # test_channels.py.
    for col in SAMPLE_COLUMNS:
        if col in OPTIONAL_COLUMNS:
            assert col not in detail["samples"], col
        else:
            assert col in detail["samples"], col


async def test_compare_channels_param(client) -> None:
    c, service = client
    await drive_laps(service, laps=3)
    laps = (await c.get("/api/laps")).json()
    ids = [laps[0]["id"], laps[1]["id"]]

    # Default: classic columns only, no per-corner data
    r = (await c.get(f"/api/analysis/compare?laps={ids[0]},{ids[1]}&ref={ids[1]}")).json()
    entry = r["laps"][str(ids[0])]
    assert "speed" in entry["series"] and "slip_fl" not in entry["series"]
    assert isinstance(entry["events"], list)

    # Requested channels come back; t/pos are always present
    r = (
        await c.get(
            f"/api/analysis/compare?laps={ids[0]}&ref={ids[1]}"
            "&channels=speed,slip_fl,tt_fl,sus_fl,aids"
        )
    ).json()
    series = r["laps"][str(ids[0])]["series"]
    for col in ("t", "pos_x", "pos_z", "speed", "slip_fl", "tt_fl", "sus_fl", "aids"):
        assert col in series, col
    assert "rpm" not in series

    # Unknown channel -> 400
    resp = await c.get(f"/api/analysis/compare?laps={ids[0]}&ref={ids[1]}&channels=bogus")
    assert resp.status_code == 400


async def test_export_import_roundtrip_v2(client) -> None:
    c, service = client
    await drive_laps(service)
    lap_id = (await c.get("/api/laps")).json()[0]["id"]
    exported = (await c.get(f"/api/laps/{lap_id}/export")).json()
    assert exported["version"] == 2

    imported = (await c.post("/api/laps/import", json=exported)).json()
    detail = (await c.get(f"/api/laps/{imported['id']}")).json()
    assert detail["max_oil_temp"] == pytest.approx(95.0)
    assert detail["gearing"]["top_speed"] == pytest.approx(290.0)
    assert detail["event_counts"].get("lockup", 0) >= 1


async def test_import_v1_file_without_new_columns(client) -> None:
    c, service = client
    await drive_laps(service)
    lap_id = (await c.get("/api/laps")).json()[0]["id"]
    exported = (await c.get(f"/api/laps/{lap_id}/export")).json()
    # Strip to a v1-era file
    exported["version"] = 1
    lap = exported["lap"]
    for key in ("events", "gearing", "max_water_temp", "max_oil_temp", "min_oil_pressure"):
        lap.pop(key, None)
    lap["samples"] = {
        k: v for k, v in lap["samples"].items()
        if not k.startswith(("slip_", "tt_", "sus_")) and k != "aids"
    }
    imported = (await c.post("/api/laps/import", json=exported)).json()
    detail = (await c.get(f"/api/laps/{imported['id']}")).json()
    assert detail["min_oil_pressure"] == -1.0
    assert detail["gearing"] is None
    assert detail["events"] == []
