"""Body slip angle (#109): the angle between where the car points and where
it is going, per tick and per corner.

What GT7 sends for the car's orientation is documented nowhere it can be
trusted, so the reading is pinned to packets a real console sent: the three
records below are from a surface-survey capture at Mount Panorama, with the
angle worked out independently on the map (heading of the nose less heading
of travel) beside them.
"""

import math

import pytest

from app.models import BODY_SLIP_MIN_SPEED, TelemetryPacket
from app.processing import analysis
from app.processing.corner_metrics import LapTrace, measure, windows
from app.processing.laps import OPTIONAL_COLUMNS
from app.telemetry import simulator
from app.telemetry.packet import build_packet, parse_packet

# (velocity, "rotation" x y z, "orientation to north" w, degrees on the map)
CAPTURED = [
    ((-5.521, 1.245, 31.262), (0.021525, 0.995642, -0.017712), 0.088992, 0.155),
    ((-15.402, 1.236, 11.639), (0.015852, 0.893694, -0.031025), 0.447322, 0.255),
    ((27.556, -3.168, -82.381), (-0.016551, -0.171817, -0.012018), 0.984917, 1.277),
]


def packet(
    velocity: tuple[float, float, float],
    orientation: tuple[float, float, float, float],
) -> TelemetryPacket:
    return parse_packet(build_packet(velocity=velocity, orientation=orientation))


def yawed(nose_heading: float) -> tuple[float, float, float, float]:
    """The orientation of a level car whose nose points along `nose_heading`,
    an angle in the app's map convention: atan2(z, x), rising in a
    right-hander."""
    turn = math.atan2(-math.cos(nose_heading), -math.sin(nose_heading))
    return (0.0, math.sin(turn / 2), 0.0, math.cos(turn / 2))


@pytest.mark.parametrize(("velocity", "xyz", "w", "on_the_map"), CAPTURED)
def test_a_real_packet_reads_as_it_does_on_the_map(velocity, xyz, w, on_the_map) -> None:
    slip = packet(velocity, (*xyz, w)).body_slip_deg
    # The map's figure ignores gradient and banking; the car's frame does
    # not, and on a road this level they differ by hundredths of a degree.
    assert slip == pytest.approx(on_the_map, abs=0.05)


def test_the_orientation_is_a_unit_quaternion_in_every_captured_packet() -> None:
    for _velocity, (x, y, z), w, _ in CAPTURED:
        assert x * x + y * y + z * z + w * w == pytest.approx(1.0, abs=1e-4)


def test_positive_is_the_nose_to_the_right_of_travel() -> None:
    """A right-hander is a rising heading everywhere in the app. A nose
    turned five degrees further round than the car is travelling is pointing
    into it."""
    travel = 0.6
    velocity = (30.0 * math.cos(travel), 0.0, 30.0 * math.sin(travel))
    into_a_right_hander = packet(velocity, yawed(travel + math.radians(5.0)))
    assert into_a_right_hander.body_slip_deg == pytest.approx(5.0, abs=0.01)
    into_a_left_hander = packet(velocity, yawed(travel - math.radians(5.0)))
    assert into_a_left_hander.body_slip_deg == pytest.approx(-5.0, abs=0.01)


def test_a_car_pointing_where_it_is_going_has_none() -> None:
    travel = -2.1
    velocity = (40.0 * math.cos(travel), 0.0, 40.0 * math.sin(travel))
    assert packet(velocity, yawed(travel)).body_slip_deg == pytest.approx(0.0, abs=0.01)


def test_climbing_a_hill_is_not_slip() -> None:
    """The velocity of a car going straight up a gradient has a vertical
    part. Measured in the car's frame it is still straight ahead."""
    pitch = math.radians(8.0)
    # Nose along -Z, pitched up about the car's right-hand side (+X).
    orientation = (math.sin(pitch / 2), 0.0, 0.0, math.cos(pitch / 2))
    velocity = (0.0, 30.0 * math.sin(pitch), -30.0 * math.cos(pitch))
    assert packet(velocity, orientation).body_slip_deg == pytest.approx(0.0, abs=0.01)


def test_below_the_speed_gate_the_angle_is_zero() -> None:
    crawl = BODY_SLIP_MIN_SPEED - 0.5
    sideways = packet((crawl, 0.0, 0.0), yawed(math.radians(40.0)))
    assert sideways.body_slip_deg == 0.0
    moving = packet((BODY_SLIP_MIN_SPEED + 0.5, 0.0, 0.0), yawed(math.radians(40.0)))
    assert moving.body_slip_deg == pytest.approx(40.0, abs=0.01)


def test_a_packet_without_an_orientation_has_no_angle() -> None:
    assert parse_packet(build_packet(velocity=(30.0, 0.0, 0.0))).body_slip_deg is None


def test_the_column_is_optional() -> None:
    """A recording from a source that sends no orientation carries no column,
    not a column of zeros: see prune_optional."""
    assert "body_slip" in OPTIONAL_COLUMNS


# --- the simulator -----------------------------------------------------------


def test_the_simulated_car_broadcasts_the_slip_it_was_given() -> None:
    for distance, speed in ((400.0, 60.0), (650.0, 30.0), (2100.0, 45.0)):
        velocity, orientation = simulator._motion(distance, speed)
        slip = packet(velocity, orientation).body_slip_deg
        assert slip == pytest.approx(
            math.degrees(simulator._body_slip(distance, speed)), abs=0.01
        )


def test_the_simulated_car_points_into_a_fast_corner_and_out_of_a_slow_one() -> None:
    tightest = max(
        range(0, int(simulator.TRACK_LENGTH), 5),
        key=lambda d: abs(simulator._curvature_at(float(d))),
    )
    k = simulator._curvature_at(float(tightest))
    into = 1.0 if k > 0 else -1.0
    assert simulator._body_slip(float(tightest), 30.0) * into > 0
    assert simulator._body_slip(float(tightest), 10.0) * into < 0


# --- per corner --------------------------------------------------------------


def lap(slip: list[tuple[float, float, float]], length: int = 1000) -> dict[str, list[float]]:
    """A lap in 1 m steps; `slip` is (from, to, degrees) stretches."""
    dist = [float(d) for d in range(length)]

    def at(d: float) -> float:
        for lo, hi, value in slip:
            if lo <= d < hi:
                return value
        return 0.0

    return {
        "dist": dist,
        "t": [d / 50.0 for d in dist],
        "speed": [180.0] * length,
        "brake": [0.0] * length,
        "throttle": [100.0] * length,
        "body_slip": [at(d) for d in dist],
    }


def corner(n: int, apex: float, direction: str) -> dict[str, float | int | str]:
    return {
        "n": n,
        "entry_dist": apex - 50.0,
        "apex_dist": apex,
        "exit_dist": apex + 50.0,
        "direction": direction,
    }


def test_a_corner_reports_rotation_into_it_whichever_way_it_turns() -> None:
    """Nose right of travel is into a right-hander and out of a left-hander.
    Per corner the sign is turned so that rotation reads the same in both."""
    samples = lap([(260.0, 300.0, 4.0), (560.0, 600.0, -4.0)])
    m = measure(
        LapTrace(samples), windows([corner(1, 300.0, "R"), corner(2, 600.0, "L")])
    )
    assert m[1].slip_peak == pytest.approx(4.0)
    assert m[2].slip_peak == pytest.approx(4.0)
    # 40 of the window's 101 metres at four degrees.
    assert m[1].slip_mean == pytest.approx(4.0 * 40 / 101, abs=0.01)
    assert m[2].slip_mean == pytest.approx(m[1].slip_mean)


def test_a_nose_pointing_out_of_the_corner_is_negative() -> None:
    m = measure(LapTrace(lap([(250.0, 351.0, 2.0)])), windows([corner(1, 300.0, "L")]))
    assert m[1].slip_peak == pytest.approx(-2.0)
    assert m[1].slip_mean == pytest.approx(-2.0)


def test_no_balance_without_the_channel_or_the_direction() -> None:
    samples = lap([(260.0, 300.0, 4.0)])
    no_direction = {k: v for k, v in corner(1, 300.0, "R").items() if k != "direction"}
    assert measure(LapTrace(samples), windows([no_direction]))[1].slip_peak is None
    del samples["body_slip"]
    m = measure(LapTrace(samples), windows([corner(1, 300.0, "R")]))
    assert m[1].slip_peak is None and m[1].slip_mean is None


def test_the_corner_report_carries_the_balance() -> None:
    corners = [corner(1, 300.0, "R")]
    row = analysis.corner_report(corners, lap([(260.0, 300.0, 4.0)]))[0]
    assert row["slip_peak"] == pytest.approx(4.0)
    assert row["slip_mean"] == pytest.approx(1.58, abs=0.01)
    before = lap([])
    del before["body_slip"]
    row = analysis.corner_report(corners, before)[0]
    assert row["slip_peak"] is None and row["slip_mean"] is None
