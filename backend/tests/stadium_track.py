"""A stadium-shaped circuit for tests that need laps with corners in them.

Two 400 m straights joined by two 180-degree corners of 60 m radius, both
right-handers as the app counts them (a rising atan2(z, x) heading). The
start/finish line is in the middle of a straight, so neither corner wraps.
A lap brakes for each corner from a chosen distance before it, and the car
can be given a body slip to carry through the corners.
"""

from __future__ import annotations

import math
from collections.abc import Iterator

from app.models import SimulatorFlags, TelemetryPacket
from app.telemetry.packet import build_packet, parse_packet

ON_TRACK = int(SimulatorFlags.CAR_ON_TRACK)
TICK = 1 / 60
STRAIGHT = 400.0
RADIUS = 60.0
ARC = math.pi * RADIUS
LENGTH = 2 * STRAIGHT + 2 * ARC
# Where each corner begins and ends, metres from the line.
CORNERS = ((STRAIGHT / 2, STRAIGHT / 2 + ARC), (1.5 * STRAIGHT + ARC, 1.5 * STRAIGHT + 2 * ARC))
FAST, SLOW = 40.0, 22.0  # m/s


def place(s: float) -> tuple[float, float, float]:
    """(x, z, heading) this many metres from the line."""
    s %= LENGTH
    half = STRAIGHT / 2
    if s < half:
        return s, 0.0, 0.0
    s -= half
    if s < ARC:
        a = s / RADIUS
        return half + RADIUS * math.sin(a), RADIUS * (1 - math.cos(a)), a
    s -= ARC
    if s < STRAIGHT:
        return half - s, 2 * RADIUS, math.pi
    s -= STRAIGHT
    if s < ARC:
        a = s / RADIUS
        return -half - RADIUS * math.sin(a), RADIUS * (1 + math.cos(a)), math.pi + a
    s -= ARC
    return -half + s, 0.0, 0.0


def orientation(nose_heading: float) -> tuple[float, float, float, float]:
    """A level car's orientation, as GT7 sends it, with its nose along
    `nose_heading` (the car's nose is its local -Z)."""
    turn = math.atan2(-math.cos(nose_heading), -math.sin(nose_heading))
    return (0.0, math.sin(turn / 2), 0.0, math.cos(turn / 2))


class Driver:
    """Streams packets for consecutive laps, keeping one packet counter."""

    def __init__(self, car_id: int = 7) -> None:
        self.pid = 0
        self.car_id = car_id
        self._s = 0.0
        self._last_ms = -1

    def lap(
        self,
        lap: int,
        *,
        brake_before_m: float = 80.0,
        slip_deg: float | None = 3.0,
        tyre_temp: float = 80.0,
        fuel: float = 50.0,
    ) -> Iterator[TelemetryPacket]:
        """One lap from the line. `slip_deg` is carried through both corners,
        nose into them; None sends no orientation at all."""
        ticks = 0
        while self._s < lap * LENGTH:
            s = self._s % LENGTH
            braking = cornering = False
            speed = FAST
            for entry, exit_ in CORNERS:
                if entry - brake_before_m <= s < entry:
                    braking = True
                    speed = FAST - (FAST - SLOW) * (s - (entry - brake_before_m)) / brake_before_m
                elif entry <= s < exit_:
                    cornering = True
                    speed = SLOW
                elif exit_ <= s < exit_ + 100.0:
                    speed = SLOW + (FAST - SLOW) * (s - exit_) / 100.0
            x, z, heading = place(s)
            slip = math.radians(slip_deg or 0.0) if cornering else 0.0
            self.pid += 1
            yield parse_packet(
                build_packet(
                    packet_id=self.pid,
                    current_lap=lap,
                    last_lap_time_ms=self._last_ms,
                    position=(x, 0.0, z),
                    velocity=(speed * math.cos(heading), 0.0, speed * math.sin(heading)),
                    orientation=(
                        orientation(heading + slip) if slip_deg is not None else (0.0,) * 4
                    ),
                    angular_velocity=(0.0, speed / RADIUS if cornering else 0.0, 0.0),
                    speed_mps=speed,
                    throttle=0 if braking else 255,
                    brake=255 if braking else 0,
                    tire_temps=(tyre_temp, tyre_temp + 2, tyre_temp + 6, tyre_temp + 8),
                    fuel_level=fuel - 2.0 * (s / LENGTH),
                    flags=ON_TRACK,
                    car_id=self.car_id,
                )
            )
            self._s += speed * TICK
            ticks += 1
        self._last_ms = round(ticks * TICK * 1000)

    def line(self, lap: int, fuel: float = 50.0) -> TelemetryPacket:
        """The packet that completes the lap before `lap`."""
        self.pid += 1
        x, z, heading = place(self._s)
        return parse_packet(
            build_packet(
                packet_id=self.pid,
                current_lap=lap,
                last_lap_time_ms=self._last_ms,
                position=(x, 0.0, z),
                velocity=(FAST * math.cos(heading), 0.0, FAST * math.sin(heading)),
                orientation=orientation(heading),
                speed_mps=FAST,
                throttle=255,
                fuel_level=fuel,
                flags=ON_TRACK,
                car_id=self.car_id,
            )
        )
