"""Typed telemetry model normalized from raw GT7 packets."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from enum import IntFlag

# Below this the car's direction of travel is noise, and so is any angle
# measured from it (m/s, about 29 km/h). Over a real capture the angle between
# nose and travel has a median of 0.2-0.5 degrees at every speed above this
# and of 1.6, 4.6 and 10.6 degrees in the three bands below it. The same gate
# the accelerometer calibration puts on a heading taken from positions.
BODY_SLIP_MIN_SPEED = 8.0
# How far from 1 the orientation's length may be and still be an orientation.
# A real packet's is 1 to five places; a source that sends no orientation
# sends zeros.
ORIENTATION_TOLERANCE = 0.05


class SimulatorFlags(IntFlag):
    NONE = 0
    CAR_ON_TRACK = 1 << 0
    PAUSED = 1 << 1
    LOADING = 1 << 2
    IN_GEAR = 1 << 3
    HAS_TURBO = 1 << 4
    REV_LIMITER = 1 << 5
    HANDBRAKE = 1 << 6
    LIGHTS = 1 << 7
    HIGH_BEAM = 1 << 8
    LOW_BEAM = 1 << 9
    ASM_ACTIVE = 1 << 10
    TCS_ACTIVE = 1 << 11


class AidsBits(IntFlag):
    """Compact per-tick driver-aid bitmask stored in the lap samples.

    Four flags for the price of one column; decoded by the frontend and the
    per-lap aid metrics. Distinct from SimulatorFlags so the stored format
    stays stable even if GT7 shuffles its flag bits.
    """

    NONE = 0
    TCS = 1 << 0
    ASM = 1 << 1
    HANDBRAKE = 1 << 2
    REV_LIMITER = 1 << 3


@dataclass(slots=True)
class TelemetryPacket:
    packet_id: int

    # Position / motion (meters, meters/second, radians/second)
    position_x: float
    position_y: float
    position_z: float
    velocity_x: float
    velocity_y: float
    velocity_z: float
    # Named as the community layout names them, which is not what they are:
    # the three "rotation" floats are the x, y and z of a unit quaternion and
    # "orientation to north" is its w. None of the four is an angle, and the
    # last is not a heading. See body_slip_deg.
    rotation_pitch: float
    rotation_yaw: float
    rotation_roll: float
    rel_orientation_to_north: float
    angular_velocity_x: float
    angular_velocity_y: float
    angular_velocity_z: float

    body_height: float  # meters
    engine_rpm: float
    fuel_level: float
    fuel_capacity: float
    speed_mps: float
    boost: float  # bar (raw value - 1)

    oil_pressure: float
    water_temp: float
    oil_temp: float

    tire_temp_fl: float
    tire_temp_fr: float
    tire_temp_rl: float
    tire_temp_rr: float

    current_lap: int
    total_laps: int
    best_lap_time_ms: int  # -1 when not set
    last_lap_time_ms: int  # -1 when not set
    day_progression_ms: int

    race_position: int
    total_positions: int

    rpm_alert_min: float
    rpm_alert_max: float
    calculated_max_speed: int

    flags: int
    current_gear: int  # 0 = reverse/neutral handling per GT7 (15 = neutral)
    suggested_gear: int  # 15 = none
    throttle: int  # 0..255
    brake: int  # 0..255

    # Wheel angular speed (rad/s, signed) and tire radius (m)
    wheel_rps_fl: float
    wheel_rps_fr: float
    wheel_rps_rl: float
    wheel_rps_rr: float
    tire_radius_fl: float
    tire_radius_fr: float
    tire_radius_rl: float
    tire_radius_rr: float

    suspension_fl: float
    suspension_fr: float
    suspension_rl: float
    suspension_rr: float

    clutch: float
    clutch_engagement: float
    rpm_after_clutch: float
    transmission_top_speed: float
    gear_ratios: tuple[float, ...]

    car_id: int

    # --- Extended formats (None when the console sends plain packet A) ------
    # Packet B (heartbeat "B") and up:
    wheel_rotation: float | None = None  # steering wheel angle, radians
    sway: float | None = None  # lateral acceleration
    heave: float | None = None  # vertical acceleration
    surge: float | None = None  # longitudinal acceleration
    # Packet "~" and up:
    throttle_filtered: int | None = None  # 0..255, after assists
    brake_filtered: int | None = None  # 0..255, after assists
    torque_vectors: tuple[float, ...] | None = None  # per wheel; + drive, - brake/regen
    energy_recovery: float | None = None
    # Packet C only:
    surface_types: str | None = None  # per wheel FL FR RL RR: T/C/D/G/S/s
    lap_time_ms: int | None = None  # live current-lap timer
    wheel_steering_rad: tuple[float, float] | None = None  # front wheels, left/right
    wheelbase_m: float | None = None
    car_category: str | None = None  # e.g. "Gr3", "Gr4"

    @property
    def speed_kmh(self) -> float:
        return self.speed_mps * 3.6

    @property
    def is_on_track(self) -> bool:
        return bool(self.flags & SimulatorFlags.CAR_ON_TRACK)

    @property
    def is_paused(self) -> bool:
        return bool(self.flags & SimulatorFlags.PAUSED)

    @property
    def is_loading(self) -> bool:
        return bool(self.flags & SimulatorFlags.LOADING)

    @property
    def throttle_pct(self) -> float:
        return self.throttle / 2.55

    @property
    def brake_pct(self) -> float:
        return self.brake / 2.55

    @property
    def tire_slip_ratio(self) -> float:
        """Average tire surface speed / car speed. ~1.0 when gripping."""
        if self.speed_mps < 1.0:
            return 1.0
        return sum(self.wheel_slips) / 4.0

    @property
    def wheel_slips(self) -> tuple[float, float, float, float]:
        """Per-wheel tire surface speed / car speed (FL, FR, RL, RR).

        <1 under braking = locking; >1 on throttle = spinning.
        """
        if self.speed_mps < 1.0:
            return (1.0, 1.0, 1.0, 1.0)
        return (
            abs(self.wheel_rps_fl) * self.tire_radius_fl / self.speed_mps,
            abs(self.wheel_rps_fr) * self.tire_radius_fr / self.speed_mps,
            abs(self.wheel_rps_rl) * self.tire_radius_rl / self.speed_mps,
            abs(self.wheel_rps_rr) * self.tire_radius_rr / self.speed_mps,
        )

    @property
    def body_slip_deg(self) -> float | None:
        """Body slip angle: how far the nose points from where the car is
        going, in degrees. Positive is the nose to the RIGHT of travel — in
        a right-hander, turned further into the corner than the car is
        moving. That is ISO 8855's sign, and the sign of a right-hander
        everywhere else in the app.

        Measured in the car's own frame: the velocity is turned by the
        inverse of the car's orientation, and the angle is between its two
        horizontal parts there. The car's nose is its local -Z and its
        right-hand side +X. On the map the same angle is the heading of the
        nose less the heading of travel, but only on level ground; in the
        car's frame banking and gradient do not read as slip.

        None when the packet carries no orientation. 0 below
        BODY_SLIP_MIN_SPEED: a car that is barely moving has no direction of
        travel to be measured against, and the recording has to say
        something on every tick.
        """
        w = self.rel_orientation_to_north
        x, y, z = -self.rotation_pitch, -self.rotation_yaw, -self.rotation_roll
        length = w * w + x * x + y * y + z * z
        if not math.isfinite(length) or abs(length - 1.0) > ORIENTATION_TOLERANCE:
            return None
        vx, vy, vz = self.velocity_x, self.velocity_y, self.velocity_z
        # v' = v + w t + u x t, with t = 2 u x v and u the inverse's x, y, z.
        tx = 2 * (y * vz - z * vy)
        ty = 2 * (z * vx - x * vz)
        tz = 2 * (x * vy - y * vx)
        right = vx + w * tx + (y * tz - z * ty)
        back = vz + w * tz + (x * ty - y * tx)
        if math.hypot(right, back) < BODY_SLIP_MIN_SPEED:
            return 0.0
        return math.degrees(math.atan2(-right, -back))

    @property
    def aids_bits(self) -> int:
        """Driver-aid state as an AidsBits mask."""
        bits = AidsBits.NONE
        if self.flags & SimulatorFlags.TCS_ACTIVE:
            bits |= AidsBits.TCS
        if self.flags & SimulatorFlags.ASM_ACTIVE:
            bits |= AidsBits.ASM
        if self.flags & SimulatorFlags.HANDBRAKE:
            bits |= AidsBits.HANDBRAKE
        if self.flags & SimulatorFlags.REV_LIMITER:
            bits |= AidsBits.REV_LIMITER
        return int(bits)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)
