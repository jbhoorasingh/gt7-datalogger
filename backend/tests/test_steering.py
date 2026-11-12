"""The steering wheel's rotation past GT7's half-turn stop (#233).

GT7 holds `wheel_rotation` at ±π; packet C's road-wheel angle keeps turning,
and below the stop the two differ by a constant steering ratio. The figures
here are the shape of real laps: a ratio of 12.8, the stop reached with the
road wheels at π / 12.8 = 0.245 rad, full lock at 0.612 rad.
"""

import math

import pytest

from app.processing import steering
from app.telemetry import simulator

RATIO = 12.8
LOCK = 0.612


def sweep(peak: float = LOCK, ticks: int = 600, ratio: float = RATIO) -> dict[str, list[float]]:
    """The wheel taken from straight to `peak` radians of road-wheel angle to
    the left and back, then the same to the right, as GT7 would report it."""
    half = ticks // 2
    angle = [peak * math.sin(math.pi * i / half) for i in range(half)]
    angle += [-a for a in angle]
    steer = [max(-math.pi, min(math.pi, a * ratio)) for a in angle]
    return {"steer": steer, "wheel_angle": angle}


def test_the_ratio_comes_from_the_ticks_inside_the_stop() -> None:
    assert steering.lap_ratio(sweep()) == pytest.approx(RATIO)


def test_the_held_ticks_are_filled_in_from_the_road_wheels() -> None:
    samples = sweep()
    full = steering.full_range(samples)
    assert full is not None
    assert max(samples["steer"]) == pytest.approx(math.pi)
    assert max(full) == pytest.approx(LOCK * RATIO, abs=0.01)
    assert min(full) == pytest.approx(-LOCK * RATIO, abs=0.01)
    # Signed as broadcast, and continuous across the stop.
    assert all((f > 0) == (s > 0) for f, s in zip(full, samples["steer"], strict=True) if s)
    assert max(abs(b - a) for a, b in zip(full, full[1:], strict=False)) < 0.1


def test_inside_the_stop_the_broadcast_value_is_left_alone() -> None:
    samples = sweep(peak=0.2)
    assert max(samples["steer"]) < steering.AT_STOP_RAD
    assert steering.full_range(samples) == [round(s, 4) for s in samples["steer"]]


def test_never_less_than_the_stop_the_wheel_is_known_to_have_reached() -> None:
    # A rack that ran out of travel before the wheel did: the road wheels say
    # less than half a turn, the broadcast value says at least that.
    assert steering.full_rotation(math.pi, 0.2, RATIO) == pytest.approx(math.pi)
    assert steering.full_rotation(-math.pi, -0.2, RATIO) == pytest.approx(-math.pi)


def test_a_lap_without_the_road_wheels_has_no_full_range() -> None:
    samples = sweep()
    del samples["wheel_angle"]
    assert steering.full_range(samples) is None
    assert steering.STEER_FULL not in steering.with_full_range(samples)


def test_no_ratio_from_a_lap_that_barely_steered() -> None:
    assert steering.lap_ratio(sweep(peak=0.01)) is None
    assert steering.STEER_FULL in steering.with_full_range(sweep())


def test_live_the_ratio_is_learned_as_the_car_is_driven() -> None:
    live = steering.LiveSteering()
    samples = sweep()
    out = [
        live.update(1, s, a)
        for s, a in zip(samples["steer"], samples["wheel_angle"], strict=True)
    ]
    assert live.ratio == pytest.approx(RATIO)
    assert max(v for v in out if v is not None) == pytest.approx(LOCK * RATIO, abs=0.01)


def test_live_the_value_goes_out_as_broadcast_until_a_ratio_is_known() -> None:
    live = steering.LiveSteering()
    assert live.update(1, math.pi, LOCK) == pytest.approx(math.pi)
    assert live.update(1, None, None) is None  # packet A
    assert live.update(1, 0.5, None) == 0.5  # packet B: no road wheels


def test_live_a_new_car_starts_from_nothing() -> None:
    live = steering.LiveSteering()
    samples = sweep()
    for s, a in zip(samples["steer"], samples["wheel_angle"], strict=True):
        live.update(1, s, a)
    assert live.update(2, math.pi, LOCK) == pytest.approx(math.pi)
    assert live.ratio is None


def test_the_simulator_signs_steering_as_gt7_does() -> None:
    """Positive to the left. In GT7's x/z plane a left turn is the one whose
    path has a negative cross product, as on its anticlockwise ovals."""
    d = float(max(
        range(0, int(simulator.TRACK_LENGTH), 5),
        key=lambda d: abs(simulator._curvature_at(float(d))),
    ))
    (x0, z0), (x1, z1), (x2, z2) = (simulator._position_at(d + k) for k in (-5.0, 0.0, 5.0))
    cross = (x1 - x0) * (z2 - z1) - (z1 - z0) * (x2 - x1)
    wheels = simulator._front_wheel_angle(d, 20.0)
    assert (wheels > 0) == (cross < 0)
    assert (simulator._wheel_rotation(wheels) > 0) == (wheels > 0)
    assert simulator._wheel_rotation(1.0) == pytest.approx(math.pi)
