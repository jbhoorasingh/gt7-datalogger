"""Car balance from the front wheels (#155): the kinematic yaw rate the
wheels asked for, the measured yaw against it, and the steering lock.

Derived on read from `wheel_angle` (packet C, 60 Hz) and the lap's
`wheelbase_m`, at full rate; absent on a lap without both, never zero-filled.
"""

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.processing import analysis, balance
from app.processing.cars import CarDatabase
from app.processing.corner_metrics import LapTrace, measure, windows
from app.service import TelemetryService
from app.storage.db import init_db, make_engine, make_session_factory
from app.storage.repository import Repository
from tests import stadium_track as track

WHEELBASE = 2.6
RATIO = 12.0


def lap(
    *,
    length: int = 1000,
    turn: tuple[float, float] = (250.0, 350.0),
    wheel: float = 0.13,
    yaw: float = 0.8,
    ramp_to: float | None = None,
    lock: float | None = None,
) -> dict[str, list[float]]:
    """A lap in 1 m steps at 72 km/h (20 m/s). Through `turn` the front
    wheels are at `wheel` radians and the car rotates at `yaw` rad/s. With
    `ramp_to`, the steering asks for a road-wheel angle rising from straight
    to `ramp_to` through the turn instead, and with `lock` the wheels stop
    there while the input goes on rising."""
    dist = [float(d) for d in range(length)]
    lo, hi = turn
    steer: list[float] = []
    angle: list[float] = []
    for d in dist:
        inside = lo <= d < hi
        if ramp_to is None:
            asked = wheel if inside else 0.0
        else:
            asked = ramp_to * (d - lo) / (hi - lo) if inside else 0.0
        steer.append(asked * RATIO)
        angle.append(asked if lock is None else min(asked, lock))
    return {
        "dist": dist,
        "t": [d / 20.0 for d in dist],
        "speed": [72.0] * length,
        "brake": [0.0] * length,
        "throttle": [100.0] * length,
        "yaw_rate": [yaw if lo <= d < hi else 0.0 for d in dist],
        "steer": steer,
        "wheel_angle": angle,
    }


def corner(apex: float = 300.0) -> dict[str, float | int | str]:
    return {
        "n": 1, "entry_dist": apex - 50.0, "apex_dist": apex, "exit_dist": apex + 50.0,
        "direction": "R",
    }


# --- the channels ------------------------------------------------------------


def test_kinematic_yaw_is_speed_times_wheel_angle_over_wheelbase() -> None:
    samples = lap()
    kin = balance.kinematic_yaw(samples, WHEELBASE)
    assert kin is not None and len(kin) == len(samples["t"])
    # 20 m/s x 0.13 rad / 2.6 m.
    assert kin[300] == pytest.approx(1.0)
    assert kin[100] == 0.0


def test_it_is_a_magnitude_like_the_yaw_rate() -> None:
    """The stored yaw_rate is absolute, and how GT7 signs the wheels against
    the rotation is unchecked: a left-hander asks for rotation too."""
    samples = lap(wheel=-0.13)
    kin = balance.kinematic_yaw(samples, WHEELBASE)
    assert kin is not None and kin[300] == pytest.approx(1.0)


def test_yaw_balance_reads_understeer_below_zero_and_oversteer_above() -> None:
    under = balance.with_balance(lap(yaw=0.8), WHEELBASE)
    over = balance.with_balance(lap(yaw=1.3), WHEELBASE)
    assert under[balance.YAW_BALANCE][300] == pytest.approx(-0.2)
    assert over[balance.YAW_BALANCE][300] == pytest.approx(0.3)


@pytest.mark.parametrize("wheelbase", [None, 0.0])
def test_no_wheelbase_no_kinematic_yaw(wheelbase) -> None:
    out = balance.with_balance(lap(), wheelbase)
    assert balance.KIN_YAW not in out and balance.YAW_BALANCE not in out


def test_no_wheel_angle_no_kinematic_yaw() -> None:
    samples = lap()
    del samples["wheel_angle"]
    assert balance.KIN_YAW not in balance.with_balance(samples, WHEELBASE)
    samples = lap()
    samples["wheel_angle"] = samples["wheel_angle"][:-1]  # not the whole lap
    assert balance.kinematic_yaw(samples, WHEELBASE) is None


def test_the_stored_samples_are_left_alone() -> None:
    samples = lap()
    balance.with_balance(samples, WHEELBASE)
    assert balance.KIN_YAW not in samples and balance.STEER_LOCK not in samples


# --- the steering lock -------------------------------------------------------

# A long turn the input winds on through, to twice the lock: the wheels follow
# it to 0.3 rad at 300 m and stop.
LONG_TURN = {"turn": (100.0, 500.0), "ramp_to": 0.6}


def test_the_steering_ratio_comes_from_the_rack_inside_its_travel() -> None:
    assert balance.steering_ratio(lap(**LONG_TURN, lock=0.3)) == pytest.approx(RATIO)


def test_the_lock_is_where_the_input_outruns_the_wheels() -> None:
    flags = balance.lock_flags(lap(**LONG_TURN, lock=0.3))
    assert flags is not None
    # Flagged once the input runs 10 % past the wheels: 0.33 rad asked, at
    # 320 m. Until the turn ends.
    first = flags.index(1.0)
    assert 319 <= first <= 321
    assert all(flags[first:500])
    assert not any(flags[:first]) and not any(flags[500:])


def test_no_lock_while_the_wheels_follow_the_input() -> None:
    flags = balance.lock_flags(lap(**LONG_TURN))
    assert flags is not None and not any(flags)


def test_no_ratio_from_a_step_of_steering() -> None:
    """Straight to full angle in one tick leaves nothing inside the rack's
    travel to take a ratio from, and so nothing to say about the lock."""
    assert balance.steering_ratio(lap()) is None
    assert balance.STEER_LOCK not in balance.with_balance(lap(), WHEELBASE)


def test_no_lock_without_the_steering_input() -> None:
    samples = lap(**LONG_TURN, lock=0.3)
    del samples["steer"]
    assert balance.lock_flags(samples) is None
    assert balance.STEER_LOCK not in balance.with_balance(samples, WHEELBASE)


# --- per corner --------------------------------------------------------------


def test_a_corner_reports_its_rotation_against_the_wheels() -> None:
    samples = balance.with_balance(lap(yaw=0.8), WHEELBASE)
    m = measure(LapTrace(samples), windows([corner()]))[1]
    assert m.yaw_ratio == pytest.approx(0.8)


def test_a_corner_reports_the_metres_spent_at_the_lock() -> None:
    samples = balance.with_balance(lap(**LONG_TURN, lock=0.3), WHEELBASE)
    m = measure(LapTrace(samples), windows([corner()]))[1]
    # From 320 m to the corner's exit at 350.
    assert m.lock_m == pytest.approx(30.0, abs=1.5)
    # The wheels following all the way is no lock, and that is known.
    samples = balance.with_balance(lap(**LONG_TURN), WHEELBASE)
    assert measure(LapTrace(samples), windows([corner()]))[1].lock_m == 0.0


def test_no_balance_without_packet_c() -> None:
    samples = lap()
    for column in ("wheel_angle", "steer"):
        del samples[column]
    m = measure(LapTrace(balance.with_balance(samples, None)), windows([corner()]))[1]
    assert m.yaw_ratio is None and m.lock_m is None


def test_the_corner_report_carries_the_balance() -> None:
    samples = balance.with_balance(lap(**LONG_TURN, lock=0.3), WHEELBASE)
    row = analysis.corner_report([corner()], samples)[0]
    assert row["yaw_ratio"] is not None
    assert row["lock_m"] == pytest.approx(30.0, abs=1.5)
    plain = lap()
    del plain["wheel_angle"]
    row = analysis.corner_report([corner()], plain)[0]
    assert row["yaw_ratio"] is None and row["lock_m"] is None


# --- through the API ---------------------------------------------------------


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


async def drive(service: TelemetryService, wheels: list[float | None]) -> dict[int, int]:
    driver = track.Driver()
    for number, w in enumerate(wheels, start=1):
        for packet in driver.lap(number, wheels=w):
            await service._on_packet(packet)
    await service._on_packet(driver.line(len(wheels) + 1))
    return {lap["number"]: lap["id"] for lap in (await service.repo.list_laps(service.session_id))}


async def test_compare_serves_the_balance_channels_at_full_rate(client) -> None:
    c, service = client
    ids = await drive(service, [1.25, 1.0])
    ref, other = ids[1], ids[2]
    data = (
        await c.get(
            f"/api/analysis/compare?laps={ref},{other}&ref={ref}"
            "&channels=wheel_angle,kin_yaw,yaw_balance,yaw_rate"
        )
    ).json()
    series = data["laps"][str(ref)]["series"]
    assert {"wheel_angle", "kin_yaw", "yaw_balance"} <= series.keys()
    # The reference lap turned its wheels a quarter more than the arc
    # needed: it rotated at 1 / 1.25 of what they asked for.
    rows = {row["n"]: row for row in data["laps"][str(ref)]["corner_report"]}
    assert rows[1]["yaw_ratio"] == pytest.approx(0.8, abs=0.03)
    mine = {row["n"]: row for row in data["laps"][str(other)]["corner_report"]}
    assert mine[1]["yaw_ratio"] == pytest.approx(1.0, abs=0.03)
    assert min(data["laps"][str(ref)]["series"]["yaw_balance"]) < -0.05


async def test_a_lap_without_packet_c_has_none_of_them(client) -> None:
    c, service = client
    ids = await drive(service, [None, None])
    ref = ids[1]
    data = (
        await c.get(f"/api/analysis/compare?laps={ref}&ref={ref}&channels=kin_yaw,yaw_balance")
    ).json()
    series = data["laps"][str(ref)]["series"]
    assert "kin_yaw" not in series and "yaw_balance" not in series
    assert all(row["yaw_ratio"] is None for row in data["laps"][str(ref)]["corner_report"])


async def test_the_lap_analysis_document_carries_the_balance(client) -> None:
    c, service = client
    await drive(service, [1.25, 1.0])
    doc = (await c.get(f"/api/sessions/{service.session_id}/analysis.json")).json()
    assert doc["car"]["wheelbase_m"] == pytest.approx(track.WHEELBASE_M)
    by_number = {lap["number"]: lap for lap in doc["laps"]}
    first = by_number[1]["corners"][0]["balance"]
    second = by_number[2]["corners"][0]["balance"]
    assert first["yaw_ratio"] == pytest.approx(0.8, abs=0.03)
    assert second["yaw_ratio"] == pytest.approx(1.0, abs=0.03)
    # Both laps run to the same clock, so the first is the reference.
    assert doc["reference"]["number"] == 1
    assert "yaw_ratio_vs_ref" not in first
    assert second["yaw_ratio_vs_ref"] == pytest.approx(0.2, abs=0.04)


def test_the_simulated_car_understeers_more_the_faster_the_corner() -> None:
    """The sim turns its wheels to the arc plus a little per m/s² of
    cornering, so the dev balance trace reads below neutral."""
    from app.telemetry import simulator

    tightest = max(
        range(0, int(simulator.TRACK_LENGTH), 5),
        key=lambda d: abs(simulator._curvature_at(float(d))),
    )
    d = float(tightest)

    def ratio(speed: float) -> float:
        kin = abs(speed * simulator._front_wheel_angle(d, speed) / simulator.WHEELBASE_M)
        return abs(speed * simulator._curvature_at(d)) / kin

    assert ratio(15.0) < 1.0
    assert ratio(30.0) < ratio(15.0)
