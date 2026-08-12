"""Extended-format channels: steering (#15), accelerometer (#16), filtered
pedals (#18) — and the rule that binds them, which is that a column the
recording never carried is ABSENT rather than zero-filled."""

import math

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.models import SimulatorFlags
from app.processing import analysis
from app.processing.cars import CarDatabase
from app.processing.laps import (
    OPTIONAL_COLUMNS,
    CompletedLap,
    new_sample_store,
    prune_optional,
)
from app.service import TelemetryService
from app.storage.db import init_db, make_engine, make_session_factory
from app.storage.repository import Repository
from app.telemetry.packet import build_packet, parse_packet

ON_TRACK = int(SimulatorFlags.CAR_ON_TRACK)
G = analysis.GRAVITY


# --- the absence rule --------------------------------------------------------


def test_prune_drops_short_optional_columns() -> None:
    s = new_sample_store()
    for i in range(10):
        for column in s:
            if column not in OPTIONAL_COLUMNS:
                s[column].append(float(i))
        s["steer"].append(0.1)  # complete
        if i < 4:
            s["acc_lat"].append(0.5)  # started late / stopped early

    dropped = prune_optional(s)
    assert "acc_lat" in dropped and "acc_long" in dropped
    assert "steer" not in dropped
    assert "steer" in s and "acc_lat" not in s
    # Never touches a column that isn't optional, whatever its length.
    assert "speed" in s


def test_compute_metrics_prunes_before_measuring() -> None:
    s = new_sample_store()
    for i in range(120):
        for column in s:
            if column in OPTIONAL_COLUMNS:
                continue
            s[column].append(0.0)
        s["t"][-1] = i / 60
        s["dist"][-1] = float(i)
        s["speed"][-1] = 100.0
        s["tire_slip"][-1] = 1.0
    lap = CompletedLap(
        number=1, time_ms=2000, finished_at="", car_id=1,
        samples=s, fuel_start=1.0, fuel_end=1.0,
    )
    lap.compute_metrics()
    assert not any(column in lap.samples for column in OPTIONAL_COLUMNS)
    assert lap.total_ticks == 120


# --- end to end through the packet pipeline ----------------------------------


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
    """Two laps of a constant-radius corner, so the physics references the
    calibration fits against (v·ω and dv/dt) are non-trivial."""
    radius = 120.0
    speed = 30.0
    omega = speed / radius  # rad/s
    for lap in range(1, laps + 1):
        for tick in range(400):
            angle = omega * tick / 60
            braking = 200 <= tick < 260
            v = speed - (tick - 200) * 0.15 if braking else speed
            await service._on_packet(
                parse_packet(
                    build_packet(
                        packet_id=lap * 1000 + tick,
                        current_lap=lap,
                        last_lap_time_ms=59_000 if lap > 1 else -1,
                        position=(radius * math.cos(angle), 0.0, radius * math.sin(angle)),
                        angular_velocity=(0.0, omega, 0.0),
                        speed_mps=v,
                        throttle=0 if braking else 255,
                        brake=255 if braking else 0,
                        flags=ON_TRACK,
                        fmt=fmt,
                        # sway/surge in m/s^2, sway positive to the right of
                        # travel — which on this CCW-in-x/z path is negative.
                        wheel_rotation=0.42,
                        sway=-(v * omega),
                        surge=-9.0 if braking else 0.0,
                        heave=0.3,
                        throttle_filtered=0 if braking else 140,
                        brake_filtered=180 if braking else 0,
                    )
                )
            )
    await service._on_packet(
        parse_packet(
            build_packet(
                packet_id=99_999, current_lap=laps + 1, last_lap_time_ms=59_000,
                speed_mps=speed, flags=ON_TRACK, fmt=fmt,
            )
        )
    )


async def test_packet_a_records_no_optional_columns(client) -> None:
    c, service = client
    await drive(service, fmt="A")
    lap = (await c.get("/api/laps")).json()[0]
    detail = (await c.get(f"/api/laps/{lap['id']}")).json()
    for column in OPTIONAL_COLUMNS:
        assert column not in detail["samples"], column


async def test_packet_c_records_every_optional_column(client) -> None:
    c, service = client
    await drive(service, fmt="C")
    lap = (await c.get("/api/laps")).json()[0]
    detail = (await c.get(f"/api/laps/{lap['id']}")).json()
    samples = detail["samples"]
    n = len(samples["t"])
    for column in OPTIONAL_COLUMNS:
        assert column in samples, column
        assert len(samples[column]) == n, column
    assert samples["steer"][0] == pytest.approx(0.42)
    # Filtered pedals arrive as percentages, like their raw counterparts.
    assert max(samples["throttle_f"]) == pytest.approx(140 / 2.55, abs=0.1)
    assert max(samples["brake_f"]) == pytest.approx(180 / 2.55, abs=0.1)


async def test_packet_b_stops_at_the_accelerometer(client) -> None:
    """Packet B has steering and motion but no filtered pedals — and the lap
    should carry exactly that, not a half-filled throttle_f."""
    c, service = client
    await drive(service, fmt="B")
    lap = (await c.get("/api/laps")).json()[0]
    samples = (await c.get(f"/api/laps/{lap['id']}")).json()["samples"]
    assert {"steer", "acc_lat", "acc_long", "acc_vert"} <= samples.keys()
    assert "throttle_f" not in samples and "brake_f" not in samples


async def test_csv_export_carries_the_new_channels(client) -> None:
    c, service = client
    await drive(service, fmt="C")
    lap = (await c.get("/api/laps")).json()[0]
    body = (await c.get(f"/api/laps/{lap['id']}/export.csv")).text
    header = body.splitlines()[7]
    for name in ("Steering Angle", "Accel Lateral", "Throttle Applied", "Brake Applied"):
        assert name in header


# --- accelerometer calibration (#16) -----------------------------------------


def _synthetic(scale_lat: float, scale_long: float, n: int = 1200) -> dict[str, list[float]]:
    """A lap driving a constant-radius circle while braking and accelerating,
    whose accelerometer channels are a known multiple of the true value.

    The speed oscillates rather than stepping: a constant braking rate makes
    both the reference and the channel near-constant, which is degenerate for
    any goodness-of-fit measure and unlike anything a driver produces.
    """
    radius = 120.0
    period = 6.0  # s
    s: dict[str, list[float]] = {
        "t": [], "dist": [], "speed": [], "pos_x": [], "pos_z": [],
        "acc_lat": [], "acc_long": [],
    }
    angle = 0.0
    dist = 0.0
    for i in range(n):
        t = i / 60
        v = 30.0 + 10.0 * math.sin(2 * math.pi * t / period)
        dv = 10.0 * (2 * math.pi / period) * math.cos(2 * math.pi * t / period)
        omega = v / radius
        angle += omega / 60
        dist += v / 60
        s["t"].append(t)
        s["dist"].append(round(dist, 2))
        s["speed"].append(round(v * 3.6, 2))
        # Rounded exactly as LapProcessor stores them. Not incidental: at a
        # centimetre, position quantisation is larger than the heading change
        # one 60 Hz tick covers, and a yaw rate differenced tick-to-tick comes
        # out a staircase of noise instead of a curve.
        s["pos_x"].append(round(radius * math.cos(angle), 2))
        s["pos_z"].append(round(radius * math.sin(angle), 2))
        # True lateral accel is v*omega; its SIGN in the x/z frame is what the
        # fit has to recover, so feed the channel the opposite sign on purpose.
        s["acc_lat"].append(-(v * omega) * scale_lat)
        s["acc_long"].append(dv * scale_long)
    return s


def test_heading_rate_survives_stored_position_rounding() -> None:
    """The yaw rate the calibration fits against comes from positions rounded
    to a centimetre. Measured over one tick that rounding is larger than the
    turn itself; the estimate has to hold up anyway."""
    s = _synthetic(scale_lat=1.0, scale_long=1.0)
    omega = analysis._heading_rate(s)
    # Constant radius: v/R at every point, whatever the speed is doing.
    expected = [(s["speed"][i] / 3.6) / 120.0 for i in range(len(omega))]
    middle = range(120, len(omega) - 120)
    ratios = [omega[i] / expected[i] for i in middle if expected[i] > 0.05]
    assert len(ratios) > 500
    assert min(ratios) > 0.9 and max(ratios) < 1.1


def test_calibration_recovers_metres_per_second_squared() -> None:
    cal = analysis.accel_calibration(_synthetic(scale_lat=1.0, scale_long=1.0))
    assert cal["available"]
    assert cal["lateral"]["fitted"] and cal["longitudinal"]["fitted"]
    # Fed the negated true lateral, the fit reports a negative slope — and the
    # multiplier it hands the UI carries that sign, so the diagram comes out
    # upright whichever way the console counts.
    assert cal["lateral"]["slope"] == pytest.approx(-1.0, abs=0.05)
    assert cal["lateral"]["g_per_unit"] == pytest.approx(-1 / G, rel=0.05)
    assert cal["longitudinal"]["slope"] == pytest.approx(1.0, abs=0.05)
    assert cal["unit"] == "m/s^2"


def test_calibration_recognises_a_channel_already_in_g() -> None:
    cal = analysis.accel_calibration(_synthetic(scale_lat=1 / G, scale_long=1 / G))
    assert cal["unit"] == "g"
    # 1 raw unit is 1 g, so the multiplier to g is 1.
    assert abs(cal["lateral"]["g_per_unit"]) == pytest.approx(1.0, rel=0.05)


def test_calibration_declines_when_the_lap_proves_nothing() -> None:
    """A channel that does not track the reference must not be 'calibrated'
    at whatever slope least squares happens to land on."""
    s = _synthetic(scale_lat=1.0, scale_long=1.0)
    s["acc_lat"] = [((i * 37) % 11) - 5.0 for i in range(len(s["t"]))]  # noise
    cal = analysis.accel_calibration(s)
    assert not cal["lateral"]["fitted"]
    assert cal["lateral"]["g_per_unit"] == pytest.approx(1 / G, rel=1e-4)  # assumes m/s^2


def test_calibration_unavailable_without_the_channels() -> None:
    s = _synthetic(1.0, 1.0)
    del s["acc_lat"]
    assert analysis.accel_calibration(s) == {"available": False}


def test_gg_extremes_clamp_directions_the_lap_never_used() -> None:
    s = _synthetic(scale_lat=1.0, scale_long=1.0)
    cal = analysis.accel_calibration(s)
    peaks = analysis.gg_extremes(s, cal)
    # The circle turns one way only, so the other lateral direction saw no g
    # at all — 0, never the smallest value on the used side negated.
    assert peaks["lat_right"] > 0.5
    assert peaks["lat_left"] == 0.0
    assert peaks["braking"] == pytest.approx(10.0 * (2 * math.pi / 6) / G, rel=0.1)
    assert peaks["accel"] == pytest.approx(10.0 * (2 * math.pi / 6) / G, rel=0.1)


async def test_compare_exposes_calibration_and_peaks(client) -> None:
    c, service = client
    await drive(service, fmt="C")
    laps = (await c.get("/api/laps")).json()
    ids = [laps[0]["id"], laps[1]["id"]]
    r = (
        await c.get(
            f"/api/analysis/compare?laps={ids[0]},{ids[1]}&ref={ids[1]}"
            "&channels=acc_lat,acc_long,speed"
        )
    ).json()
    assert r["accel"]["available"]
    assert r["laps"][str(ids[0])]["gg"]["braking"] > 0
    assert "acc_lat" in r["laps"][str(ids[0])]["series"]


async def test_compare_reports_no_accelerometer_on_packet_a(client) -> None:
    c, service = client
    await drive(service, fmt="A")
    laps = (await c.get("/api/laps")).json()
    r = (await c.get(f"/api/analysis/compare?laps={laps[0]['id']}&ref={laps[0]['id']}")).json()
    assert r["accel"] == {"available": False}
    assert "gg" not in r["laps"][str(laps[0]["id"])]
