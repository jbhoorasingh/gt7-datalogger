"""The steering wheel's full rotation, past the half turn GT7 reports (#233).

GT7 broadcasts the steering wheel's rotation (`wheel_rotation`, packet B) in
radians, positive to the LEFT, and stops it at ±π: turn the wheel past half a
turn and the value sits at ±3.1416 until the wheel comes back. It does not
wrap, so there is nothing to unwrap; the rest of the turn is simply not in
that field.

Packet C also broadcasts the angle of the front road wheels, with the same
sign, and those keep turning. Below the stop the two differ by the car's
steering ratio and nothing else: measured on a GT3 car, a formula car and a
road car, the quotient held to within half a percent from a few degrees of
lock up to the stop (14.3, 12.8 and 12.8). So while the broadcast value is
pinned, the wheel's rotation is taken to be

    wheel_angle x ratio

with the ratio learned from the ticks where the broadcast value was still
moving. That the ratio carries on unchanged past the stop is an assumption:
it cannot be checked there, only that it is flat everywhere it can be, and
that the full lock it gives a road car (about two and a half turns lock to
lock) is the lock such a car has.

Without packet C there is no road-wheel angle and the value stays as
broadcast, stop and all.

Pure functions and one small learner; nothing here touches the database.
"""

from __future__ import annotations

import math
from collections import deque
from statistics import median

Samples = dict[str, list[float]]

# The derived column a lap gains when it can carry it.
STEER_FULL = "steer_full"

# GT7 holds `wheel_rotation` at ±π. A tick counts as held from just inside
# it: the stored column is rounded to four places, the live value is a float32.
STOP_RAD = math.pi
AT_STOP_RAD = STOP_RAD - 0.005

# The ratio is taken from ticks with the road wheels turned at least this far
# (radians): nearer the centre both values are a few thousandths of a radian
# and their quotient is noise.
RATIO_MIN_WHEEL_RAD = 0.02
# The fewest ticks (half a second) a ratio is taken from.
RATIO_MIN_SAMPLES = 30

# The live learner: how many recent ticks the ratio is the median of, and how
# many new ones arrive before it is worked out again.
LIVE_WINDOW = 600
LIVE_REFRESH = 30


def _ratio_sample(rotation: float, wheel_angle: float) -> float | None:
    """This tick's steering ratio, or None for a tick that cannot give one:
    the broadcast value held at its stop, or the wheels near the centre."""
    if abs(rotation) >= AT_STOP_RAD or abs(wheel_angle) < RATIO_MIN_WHEEL_RAD:
        return None
    return abs(rotation) / abs(wheel_angle)


def full_rotation(rotation: float, wheel_angle: float | None, ratio: float | None) -> float:
    """The steering wheel's rotation on one tick, radians, signed as GT7
    signs it. The broadcast value wherever it is still moving; past its stop,
    the road wheels' angle times the ratio — never less than the stop itself,
    which the wheel is known to have reached."""
    if wheel_angle is None or ratio is None or abs(rotation) < AT_STOP_RAD:
        return rotation
    return math.copysign(max(abs(rotation), abs(wheel_angle) * ratio), rotation)


def lap_ratio(samples: Samples) -> float | None:
    """Steering-wheel radians per road-wheel radian on this lap: the median
    over the ticks the broadcast value was inside its stop. None without both
    channels, or with too little steering to take it from."""
    steer = samples.get("steer") or []
    angle = samples.get("wheel_angle") or []
    if not steer or len(steer) != len(angle):
        return None
    ratios = [
        r for s, a in zip(steer, angle, strict=True) if (r := _ratio_sample(s, a)) is not None
    ]
    if len(ratios) < RATIO_MIN_SAMPLES:
        return None
    return median(ratios)


def full_range(samples: Samples) -> list[float] | None:
    """The lap's steering with the ticks held at the stop filled in from the
    road wheels. None when the lap cannot carry it: no steering, no
    road-wheel angle, or no ratio."""
    ratio = lap_ratio(samples)
    if ratio is None:
        return None
    return [
        round(full_rotation(s, a, ratio), 4)
        for s, a in zip(samples["steer"], samples["wheel_angle"], strict=True)
    ]


def with_full_range(samples: Samples) -> Samples:
    """The lap's samples with `steer_full` added where the lap can carry it.
    A new dict; the columns already there are shared, not copied."""
    full = full_range(samples)
    if full is None:
        return samples
    return {**samples, STEER_FULL: full}


class LiveSteering:
    """The same estimate on the live stream, where the ratio has to be learned
    as the car is driven: the median of the last ten seconds of ticks that
    could give one, forgotten when the car changes. Until half a second of
    steering has been seen the value goes out as broadcast."""

    def __init__(self) -> None:
        self._car_id: int | None = None
        self._ratios: deque[float] = deque(maxlen=LIVE_WINDOW)
        self._ratio: float | None = None
        self._pending = 0

    @property
    def ratio(self) -> float | None:
        return self._ratio

    def update(
        self, car_id: int, rotation: float | None, wheel_angle: float | None
    ) -> float | None:
        """Take one packet in; the wheel's rotation on it, or None on a
        packet format that has no steering."""
        if car_id != self._car_id:
            self._car_id = car_id
            self._ratios.clear()
            self._ratio = None
            self._pending = 0
        if rotation is None:
            return None
        if wheel_angle is None:
            return rotation
        sample = _ratio_sample(rotation, wheel_angle)
        if sample is not None:
            self._ratios.append(sample)
            self._pending += 1
            due = self._ratio is None or self._pending >= LIVE_REFRESH
            if due and len(self._ratios) >= RATIO_MIN_SAMPLES:
                self._ratio = median(self._ratios)
                self._pending = 0
        return full_rotation(rotation, wheel_angle, self._ratio)
