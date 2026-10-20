"""Car balance from the front wheels (#155): the yaw rate the front wheels
asked for, the yaw rate the car had, and where the steering ran out of lock.

Packet C broadcasts the angle the front road wheels are turned to
(`wheel_angle`, the mean of the two) and the car's wheelbase. With them the
kinematic yaw rate is

    kin_yaw = speed * wheel_angle / wheelbase_m

the rate a car would rotate at if its tyres went exactly where the wheels
point, with no slip at all: the bicycle model at its simplest. Measured
against it, the car's own `yaw_rate` reads directly as balance. Rotating less
than the wheels ask is understeer, more is oversteer, and neither needs a
learned baseline of steering against yaw for the car.

Both are compared as magnitudes. The stored `yaw_rate` is an absolute value,
and how GT7 signs the wheel angle against the car's rotation has not been
checked against a console, so `kin_yaw` is made absolute too. The price is
opposite lock: a slide caught with the wheels pointing out of the corner can
ask for as much rotation as the car has, and reads closer to neutral than it
was. Body slip (#109) is the channel that sees that.

All of it is derived on read, at the full 60 Hz, from the stored columns: no
column is recorded for it, and a lap without both inputs has none of it —
absent, never zero-filled. The analysis charts resample the result for
drawing; the corner figures are taken from the full-rate ticks.

The steering lock comes from the same pair of channels. `steer` is the
driver's input at the steering wheel and `wheel_angle` what the road wheels
did; between them is the steering ratio, constant while the rack has travel.
Past full lock the input keeps rising and the wheels stop, so the input runs
ahead of what the ratio says it should be. Those ticks are the lock limit:
lock the driver asked for and the car could not give, which is where a ride
height or a steering-angle setting shows up lap against lap.
"""

from __future__ import annotations

from statistics import median

Samples = dict[str, list[float]]

# Derived columns this module adds to a lap's samples. `kin_yaw` and
# `yaw_balance` are chart channels; `steer_lock` (1 past the lock, else 0)
# is read by the corner measurements.
KIN_YAW = "kin_yaw"
YAW_BALANCE = "yaw_balance"
STEER_LOCK = "steer_lock"
CHART_COLUMNS = (KIN_YAW, YAW_BALANCE)

# The steering ratio is taken from ticks with the wheels turned at least this
# far (radians): near the centre both channels are a few thousandths of a
# radian and their quotient is noise.
RATIO_MIN_WHEEL_RAD = 0.02
# ...and no further than this share of the lap's largest wheel angle, which
# keeps any ticks at the lock out of the ratio they are judged against.
RATIO_MAX_SHARE = 0.7
# The fewest ticks (a second) a ratio is taken from.
RATIO_MIN_SAMPLES = 60
# A tick is at the lock when the wheels sit at the lap's largest angle (to
# within this share of it) while the input runs ahead of the ratio...
LOCK_PLATEAU_SHARE = 0.97
# ...by at least this share of what the ratio says the input should be...
LOCK_EXCESS_SHARE = 0.10
# ...and by at least this much steering-wheel rotation (radians, about 6
# degrees), so a car steered on a few degrees of input is not judged on
# hundredths.
LOCK_EXCESS_MIN_RAD = 0.1


def kinematic_yaw(samples: Samples, wheelbase_m: float | None) -> list[float] | None:
    """|speed x wheel_angle / wheelbase| per tick, rad/s. None unless the lap
    has a front wheel angle on every tick and a wheelbase."""
    angle = samples.get("wheel_angle") or []
    speed = samples.get("speed") or []
    n = len(samples.get("t") or [])
    if not wheelbase_m or wheelbase_m <= 0 or n == 0 or len(angle) != n or len(speed) != n:
        return None
    return [round(abs(v / 3.6 * a / wheelbase_m), 4) for v, a in zip(speed, angle, strict=True)]


def steering_ratio(samples: Samples) -> float | None:
    """Steering-wheel radians per road-wheel radian on this lap: the median
    over ticks the rack was well inside its travel. None without both
    channels, or with too little steering to take it from."""
    steer = samples.get("steer") or []
    angle = samples.get("wheel_angle") or []
    if not steer or len(steer) != len(angle):
        return None
    largest = max(abs(a) for a in angle)
    ceiling = largest * RATIO_MAX_SHARE
    ratios = [
        abs(s) / abs(a)
        for s, a in zip(steer, angle, strict=True)
        if RATIO_MIN_WHEEL_RAD <= abs(a) <= ceiling
    ]
    if len(ratios) < RATIO_MIN_SAMPLES:
        return None
    return median(ratios)


def lock_flags(samples: Samples) -> list[float] | None:
    """1 on the ticks the steering input ran ahead of the road wheels at the
    lap's largest wheel angle — the lock limit — and 0 elsewhere. None
    without both channels or a steering ratio."""
    ratio = steering_ratio(samples)
    if ratio is None:
        return None
    steer = samples["steer"]
    angle = samples["wheel_angle"]
    plateau = max(abs(a) for a in angle) * LOCK_PLATEAU_SHARE
    out: list[float] = []
    for s, a in zip(steer, angle, strict=True):
        expected = abs(a) * ratio
        excess = abs(s) - expected
        locked = (
            abs(a) >= plateau
            and excess >= LOCK_EXCESS_MIN_RAD
            and excess >= expected * LOCK_EXCESS_SHARE
        )
        out.append(1.0 if locked else 0.0)
    return out


def with_balance(samples: Samples, wheelbase_m: float | None) -> Samples:
    """The lap's samples with the balance columns added where the lap can
    carry them. A new dict; the columns already there are shared, not
    copied."""
    out = dict(samples)
    kin = kinematic_yaw(samples, wheelbase_m)
    yaw = samples.get("yaw_rate") or []
    if kin is not None and len(yaw) == len(kin):
        out[KIN_YAW] = kin
        out[YAW_BALANCE] = [round(y - k, 4) for y, k in zip(yaw, kin, strict=True)]
    lock = lock_flags(samples)
    if lock is not None:
        out[STEER_LOCK] = lock
    return out
