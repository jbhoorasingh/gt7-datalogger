"""Packet building, encryption round-trip, and field decoding."""

import pytest

from app.models import SimulatorFlags
from app.telemetry.crypto import decrypt_packet, encrypt_packet
from app.telemetry.packet import (
    PACKET_SIZE,
    PACKET_SIZE_B,
    PACKET_SIZE_C,
    PACKET_SIZE_TILDE,
    build_packet,
    parse_packet,
)


def make_plain(**kwargs) -> bytes:
    return build_packet(**kwargs)


def test_packet_size() -> None:
    assert len(make_plain()) == PACKET_SIZE
    assert len(make_plain(fmt="B")) == PACKET_SIZE_B
    assert len(make_plain(fmt="~")) == PACKET_SIZE_TILDE
    assert len(make_plain(fmt="C")) == PACKET_SIZE_C


def test_encrypt_decrypt_roundtrip() -> None:
    plain = make_plain(packet_id=1234, speed_mps=55.5, current_lap=3)
    wire = encrypt_packet(plain)
    assert wire != plain
    recovered = decrypt_packet(wire)
    assert recovered is not None
    # The 4 IV bytes at 0x40 are consumed by the nonce derivation and decrypt
    # to keystream garbage; every other byte must round-trip exactly.
    assert recovered[:0x40] == plain[:0x40]
    assert recovered[0x44:] == plain[0x44:]


def test_decrypt_rejects_garbage() -> None:
    assert decrypt_packet(b"\x00" * PACKET_SIZE) is None
    assert decrypt_packet(b"short") is None


def test_parse_core_fields() -> None:
    plain = make_plain(
        packet_id=42,
        speed_mps=50.0,
        engine_rpm=7200.0,
        current_lap=2,
        total_laps=10,
        best_lap_time_ms=95_432,
        last_lap_time_ms=96_001,
        fuel_level=42.5,
        throttle=255,
        brake=128,
        current_gear=4,
        suggested_gear=3,
        boost=0.8,
        flags=int(SimulatorFlags.CAR_ON_TRACK | SimulatorFlags.HAS_TURBO),
        car_id=3298,
    )
    p = parse_packet(plain)
    assert p.packet_id == 42
    assert p.speed_mps == 50.0
    assert abs(p.speed_kmh - 180.0) < 1e-6
    assert p.engine_rpm == 7200.0
    assert p.current_lap == 2
    assert p.total_laps == 10
    assert p.best_lap_time_ms == 95_432
    assert p.last_lap_time_ms == 96_001
    assert abs(p.fuel_level - 42.5) < 1e-6
    assert p.throttle == 255
    assert abs(p.throttle_pct - 100.0) < 0.1
    assert p.brake == 128
    assert p.current_gear == 4
    assert p.suggested_gear == 3
    assert abs(p.boost - 0.8) < 1e-6
    assert p.is_on_track
    assert not p.is_paused
    assert p.car_id == 3298


def test_tire_slip_ratio() -> None:
    # 30 m/s car speed, wheels spinning at surface speed 36 m/s -> ratio 1.2
    plain = make_plain(
        speed_mps=30.0,
        wheel_rps=(36 / 0.33,) * 4,
        tire_radius=(0.33,) * 4,
    )
    p = parse_packet(plain)
    assert abs(p.tire_slip_ratio - 1.2) < 0.01


def test_tire_slip_ratio_at_standstill() -> None:
    p = parse_packet(make_plain(speed_mps=0.0))
    assert p.tire_slip_ratio == 1.0


def test_packet_a_has_no_extended_fields() -> None:
    p = parse_packet(make_plain())
    assert p.wheel_rotation is None
    assert p.torque_vectors is None
    assert p.surface_types is None
    assert p.lap_time_ms is None


def test_parse_packet_b_extension() -> None:
    p = parse_packet(make_plain(fmt="B", wheel_rotation=0.5, sway=1.5, heave=-0.2, surge=2.0))
    assert p.wheel_rotation is not None and abs(p.wheel_rotation - 0.5) < 1e-6
    assert p.sway is not None and abs(p.sway - 1.5) < 1e-6
    assert p.heave is not None and abs(p.heave - -0.2) < 1e-6
    assert p.surge is not None and abs(p.surge - 2.0) < 1e-6
    assert p.surface_types is None  # tilde/C fields absent in B


def test_parse_packet_tilde_extension() -> None:
    p = parse_packet(
        make_plain(
            fmt="~",
            throttle_filtered=200,
            brake_filtered=10,
            torque_vectors=(0.1, 0.2, -0.3, -0.4),
            energy_recovery=5.5,
        )
    )
    assert p.throttle_filtered == 200
    assert p.brake_filtered == 10
    assert p.torque_vectors is not None
    assert abs(p.torque_vectors[2] - -0.3) < 1e-6
    assert p.energy_recovery is not None and abs(p.energy_recovery - 5.5) < 1e-6
    assert p.lap_time_ms is None  # C fields absent in ~


@pytest.mark.parametrize("fmt", ["~", "C"])
def test_tilde_round_trips_the_community_decoded_fields(fmt) -> None:
    """#150: the bytes at 0x13E/0x13F and the float at 0x154, unnamed until
    the community worked them out, come through parse_packet."""
    p = parse_packet(
        make_plain(
            fmt=fmt,
            powertrain_flags=9,
            energy_recovery_available=True,
            engine_torque_norm=-0.42,
            energy_recovery=1.25,
        )
    )
    assert p.powertrain_flags == 9
    assert p.energy_recovery_available is True
    assert p.engine_torque_norm is not None and abs(p.engine_torque_norm - -0.42) < 1e-6
    # Neighbours stay where they were.
    assert p.energy_recovery is not None and abs(p.energy_recovery - 1.25) < 1e-6
    off = parse_packet(make_plain(fmt=fmt))
    assert (off.powertrain_flags, off.energy_recovery_available, off.engine_torque_norm) == (
        0, False, 0.0,
    )


@pytest.mark.parametrize("fmt", ["A", "B"])
def test_narrower_formats_have_no_community_decoded_fields(fmt) -> None:
    p = parse_packet(make_plain(fmt=fmt))
    assert p.powertrain_flags is None
    assert p.energy_recovery_available is None
    assert p.engine_torque_norm is None


def test_parse_packet_c_extension() -> None:
    p = parse_packet(
        make_plain(
            fmt="C",
            surface_types="CTDG",
            lap_time_ms=42_123,
            wheel_steering_rad=(0.12, 0.11),
            wheelbase_m=2.65,
            car_category="GR3",
        )
    )
    assert p.surface_types == "CTDG"
    assert p.lap_time_ms == 42_123
    assert p.wheel_steering_rad is not None
    assert abs(p.wheel_steering_rad[0] - 0.12) < 1e-6
    assert p.wheelbase_m is not None and abs(p.wheelbase_m - 2.65) < 1e-6
    assert p.car_category == "GR3"


@pytest.mark.parametrize("fmt", ["A", "B", "~", "C"])
def test_encrypt_decrypt_roundtrip_all_formats(fmt: str) -> None:
    plain = make_plain(fmt=fmt, packet_id=99, speed_mps=33.3)
    recovered = decrypt_packet(encrypt_packet(plain))
    assert recovered is not None
    assert recovered[:0x40] == plain[:0x40]
    assert recovered[0x44:] == plain[0x44:]
    assert parse_packet(recovered).packet_id == 99
