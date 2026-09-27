"""Binary layout of the GT7 telemetry packets.

Four formats exist, selected by the heartbeat character sent to the console:
"A" (296 bytes, base), "B" (316, adds steering/motion floats), "~" (344,
adds filtered inputs + torque vectors), and "C" (368, since game v1.68,
adds surface types, a live lap timer, and car geometry). Each format is a
strict superset of the previous one, so parsing keys off the packet length.
"""

from __future__ import annotations

import struct

from app.models import TelemetryPacket

PACKET_SIZE_A = 296
PACKET_SIZE_B = 316
PACKET_SIZE_TILDE = 344
PACKET_SIZE_C = 368
PACKET_SIZE = PACKET_SIZE_A  # base layout, kept for existing callers
HEARTBEAT_FORMATS = ("A", "B", "~", "C")
MAGIC = 0x47375330

# struct format for the full packet, little-endian.
# Offsets follow the community-documented Simulator Interface layout.
_HEAD = struct.Struct(
    "<i"  # 0x00 magic
    "3f"  # 0x04 position
    "3f"  # 0x10 velocity
    "3f"  # 0x1C rotation: x, y, z of the orientation quaternion
    "f"  # 0x28 relative orientation to north: the quaternion's w
    "3f"  # 0x2C angular velocity
    "f"  # 0x38 body height
    "f"  # 0x3C engine rpm
    "4s"  # 0x40 iv (opaque)
    "f"  # 0x44 fuel level
    "f"  # 0x48 fuel capacity
    "f"  # 0x4C speed m/s
    "f"  # 0x50 boost (bar + 1)
    "f"  # 0x54 oil pressure
    "f"  # 0x58 water temp
    "f"  # 0x5C oil temp
    "4f"  # 0x60 tire temps FL FR RL RR
    "i"  # 0x70 packet id
    "h"  # 0x74 current lap
    "h"  # 0x76 total laps
    "i"  # 0x78 best lap time ms
    "i"  # 0x7C last lap time ms
    "i"  # 0x80 day progression ms
    "h"  # 0x84 race position
    "h"  # 0x86 total positions
    "H"  # 0x88 rpm alert min
    "H"  # 0x8A rpm alert max
    "h"  # 0x8C calculated max speed
    "H"  # 0x8E flags
    "B"  # 0x90 gears (low nibble current, high nibble suggested)
    "B"  # 0x91 throttle
    "B"  # 0x92 brake
    "B"  # 0x93 padding
    "4f"  # 0x94 road plane
    "4f"  # 0xA4 wheel rps FL FR RL RR
    "4f"  # 0xB4 tire radius FL FR RL RR
    "4f"  # 0xC4 suspension height FL FR RL RR
    "8f"  # 0xD4 reserved
    "f"  # 0xF4 clutch
    "f"  # 0xF8 clutch engagement
    "f"  # 0xFC rpm after clutch
    "f"  # 0x100 transmission top speed
    "8f"  # 0x104 gear ratios
    "i"  # 0x124 car id
)

assert _HEAD.size == PACKET_SIZE_A

# Packet "B" extension at 0x128: wheel rotation, filler, sway, heave, surge.
_EXT_B = struct.Struct("<5f")
# Packet "~" extension at 0x13C: filtered throttle/brake, 2 unknown bytes,
# 4 torque-vector floats, energy recovery, 1 unknown float.
_EXT_TILDE = struct.Struct("<4B4f2f")
# Packet "C" extension at 0x158: per-wheel surface chars, live lap time,
# front wheel steering angles, wheelbase, car category string.
_EXT_C = struct.Struct("<4si2ff4s")

assert PACKET_SIZE_A + _EXT_B.size == PACKET_SIZE_B
assert PACKET_SIZE_B + _EXT_TILDE.size == PACKET_SIZE_TILDE
assert PACKET_SIZE_TILDE + _EXT_C.size == PACKET_SIZE_C


def parse_packet(plain: bytes) -> TelemetryPacket:
    """Parse a decrypted packet (any of the A/B/~/C sizes) into the model."""
    if len(plain) < PACKET_SIZE_A:
        raise ValueError(f"packet too short: {len(plain)} bytes")
    v = _HEAD.unpack_from(plain)
    (
        _magic,
        pos_x, pos_y, pos_z,
        vel_x, vel_y, vel_z,
        rot_pitch, rot_yaw, rot_roll,
        rel_north,
        ang_x, ang_y, ang_z,
        body_height,
        rpm,
        _iv,
        fuel_level, fuel_capacity,
        speed_mps, boost,
        oil_pressure, water_temp, oil_temp,
        tt_fl, tt_fr, tt_rl, tt_rr,
        packet_id,
        current_lap, total_laps,
        best_lap, last_lap,
        day_progression,
        race_pos, total_pos,
        rpm_min, rpm_max,
        calc_max_speed,
        flags,
        gear_bits, throttle, brake, _pad,
        _rp0, _rp1, _rp2, _rp3,
        w_fl, w_fr, w_rl, w_rr,
        tr_fl, tr_fr, tr_rl, tr_rr,
        sus_fl, sus_fr, sus_rl, sus_rr,
        *rest,
    ) = v
    # rest = 8 reserved floats, clutch, clutch engagement, rpm after clutch,
    #        top speed, 8 gear ratios, car id
    clutch = rest[8]
    clutch_engagement = rest[9]
    rpm_after_clutch = rest[10]
    top_speed = rest[11]
    gear_ratios = tuple(rest[12:20])
    car_id = rest[20]

    wheel_rotation = sway = heave = surge = None
    throttle_f: int | None = None
    brake_f: int | None = None
    torque_vectors: tuple[float, ...] | None = None
    energy_recovery: float | None = None
    surface_types: str | None = None
    lap_time_ms: int | None = None
    wheel_steering: tuple[float, float] | None = None
    wheelbase: float | None = None
    car_category: str | None = None
    if len(plain) >= PACKET_SIZE_B:
        wheel_rotation, _filler, sway, heave, surge = _EXT_B.unpack_from(
            plain, PACKET_SIZE_A
        )
    if len(plain) >= PACKET_SIZE_TILDE:
        (
            throttle_f, brake_f, _u1, _u2,
            tv0, tv1, tv2, tv3,
            energy_recovery, _u3,
        ) = _EXT_TILDE.unpack_from(plain, PACKET_SIZE_B)
        torque_vectors = (tv0, tv1, tv2, tv3)
    if len(plain) >= PACKET_SIZE_C:
        surface, lap_time_ms, steer_l, steer_r, wheelbase, category = (
            _EXT_C.unpack_from(plain, PACKET_SIZE_TILDE)
        )
        surface_types = surface.decode("ascii", errors="replace")
        wheel_steering = (steer_l, steer_r)
        car_category = category.split(b"\x00", 1)[0].decode("ascii", errors="replace")

    return TelemetryPacket(
        packet_id=packet_id,
        position_x=pos_x, position_y=pos_y, position_z=pos_z,
        velocity_x=vel_x, velocity_y=vel_y, velocity_z=vel_z,
        rotation_pitch=rot_pitch, rotation_yaw=rot_yaw, rotation_roll=rot_roll,
        rel_orientation_to_north=rel_north,
        angular_velocity_x=ang_x, angular_velocity_y=ang_y, angular_velocity_z=ang_z,
        body_height=body_height,
        engine_rpm=rpm,
        fuel_level=fuel_level, fuel_capacity=fuel_capacity,
        speed_mps=speed_mps,
        boost=boost - 1.0,
        oil_pressure=oil_pressure, water_temp=water_temp, oil_temp=oil_temp,
        tire_temp_fl=tt_fl, tire_temp_fr=tt_fr, tire_temp_rl=tt_rl, tire_temp_rr=tt_rr,
        current_lap=current_lap, total_laps=total_laps,
        best_lap_time_ms=best_lap, last_lap_time_ms=last_lap,
        day_progression_ms=day_progression,
        race_position=race_pos, total_positions=total_pos,
        rpm_alert_min=float(rpm_min), rpm_alert_max=float(rpm_max),
        calculated_max_speed=calc_max_speed,
        flags=flags,
        current_gear=gear_bits & 0x0F,
        suggested_gear=gear_bits >> 4,
        throttle=throttle, brake=brake,
        wheel_rps_fl=w_fl, wheel_rps_fr=w_fr, wheel_rps_rl=w_rl, wheel_rps_rr=w_rr,
        tire_radius_fl=tr_fl, tire_radius_fr=tr_fr, tire_radius_rl=tr_rl, tire_radius_rr=tr_rr,
        suspension_fl=sus_fl, suspension_fr=sus_fr, suspension_rl=sus_rl, suspension_rr=sus_rr,
        clutch=clutch, clutch_engagement=clutch_engagement, rpm_after_clutch=rpm_after_clutch,
        transmission_top_speed=top_speed,
        gear_ratios=gear_ratios,
        car_id=car_id,
        wheel_rotation=wheel_rotation, sway=sway, heave=heave, surge=surge,
        throttle_filtered=throttle_f, brake_filtered=brake_f,
        torque_vectors=torque_vectors, energy_recovery=energy_recovery,
        surface_types=surface_types, lap_time_ms=lap_time_ms,
        wheel_steering_rad=wheel_steering, wheelbase_m=wheelbase,
        car_category=car_category,
    )


def build_packet(
    *,
    packet_id: int = 0,
    position: tuple[float, float, float] = (0.0, 0.0, 0.0),
    velocity: tuple[float, float, float] = (0.0, 0.0, 0.0),
    # The car's orientation as GT7 sends it: a unit quaternion's x, y, z and
    # then its w. All zeros is "no orientation", which is what a packet built
    # without one has always carried.
    orientation: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0),
    angular_velocity: tuple[float, float, float] = (0.0, 0.0, 0.0),
    body_height: float = 0.1,
    engine_rpm: float = 0.0,
    iv: int = 0xDEADCAFE,
    fuel_level: float = 100.0,
    fuel_capacity: float = 100.0,
    speed_mps: float = 0.0,
    boost: float = 0.0,
    tire_temps: tuple[float, float, float, float] = (60.0, 60.0, 60.0, 60.0),
    current_lap: int = 0,
    total_laps: int = 0,
    best_lap_time_ms: int = -1,
    last_lap_time_ms: int = -1,
    day_progression_ms: int = 0,
    race_position: int = 1,
    total_positions: int = 1,
    flags: int = 0,
    current_gear: int = 1,
    suggested_gear: int = 15,
    throttle: int = 0,
    brake: int = 0,
    wheel_rps: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0),
    tire_radius: tuple[float, float, float, float] = (0.33, 0.33, 0.33, 0.33),
    suspension: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0),
    oil_pressure: float = 0.0,
    water_temp: float = 85.0,
    oil_temp: float = 90.0,
    gear_ratios: tuple[float, ...] = (),
    transmission_top_speed: float = 300.0,
    car_id: int = 0,
    fmt: str = "A",
    wheel_rotation: float = 0.0,
    sway: float = 0.0,
    heave: float = 0.0,
    surge: float = 0.0,
    throttle_filtered: int = 0,
    brake_filtered: int = 0,
    torque_vectors: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0),
    energy_recovery: float = 0.0,
    surface_types: str = "TTTT",
    lap_time_ms: int = 0,
    wheel_steering_rad: tuple[float, float] = (0.0, 0.0),
    wheelbase_m: float = 2.5,
    car_category: str = "",
) -> bytes:
    """Build a plaintext packet (simulator / test fixture) in any format."""
    if fmt not in HEARTBEAT_FORMATS:
        raise ValueError(f"unknown packet format {fmt!r}")
    ratios = (tuple(gear_ratios) + (0.0,) * 8)[:8]
    head = _HEAD.pack(
        MAGIC,
        *position,
        *velocity,
        *orientation,  # "rotation" x, y, z and "rel north" w
        *angular_velocity,
        body_height,
        engine_rpm,
        struct.pack("<I", iv),
        fuel_level, fuel_capacity,
        speed_mps, boost + 1.0,
        oil_pressure, water_temp, oil_temp,
        *tire_temps,
        packet_id,
        current_lap, total_laps,
        best_lap_time_ms, last_lap_time_ms,
        day_progression_ms,
        race_position, total_positions,
        1000, 9000,  # rpm alerts
        300,  # calc max speed
        flags,
        (suggested_gear << 4) | (current_gear & 0x0F),
        throttle, brake, 0,
        0.0, 0.0, 0.0, 0.0,  # road plane
        *wheel_rps,
        *tire_radius,
        *suspension,
        *([0.0] * 8),  # reserved
        0.0, 1.0, 0.0,  # clutch, engagement, rpm after clutch
        transmission_top_speed,
        *ratios,
        car_id,
    )
    if fmt == "A":
        return head
    out = head + _EXT_B.pack(wheel_rotation, 0.0, sway, heave, surge)
    if fmt == "B":
        return out
    out += _EXT_TILDE.pack(
        throttle_filtered, brake_filtered, 0, 0, *torque_vectors, energy_recovery, 0.0
    )
    if fmt == "~":
        return out
    return out + _EXT_C.pack(
        surface_types.encode("ascii")[:4].ljust(4),
        lap_time_ms,
        *wheel_steering_rad,
        wheelbase_m,
        car_category.encode("ascii")[:4].ljust(4, b"\x00"),
    )
