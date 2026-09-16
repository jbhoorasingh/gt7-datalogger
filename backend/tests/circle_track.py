"""A circular circuit for tests that need laps with real positions.

The start/finish line is at angle 0 and laps run anticlockwise. A lap can be
driven on any radius (a line wider or tighter than another lap's), at any
angular pace, and can start anywhere (a grid ahead of or behind the line).
"""

from __future__ import annotations

import math
from collections.abc import Iterator

from app.models import SimulatorFlags, TelemetryPacket
from app.telemetry.packet import build_packet, parse_packet

ON_TRACK = int(SimulatorFlags.CAR_ON_TRACK)
RADIUS = 300.0
TICK = 1 / 60


def at_angle(theta: float, radius: float = RADIUS) -> tuple[float, float, float]:
    return (radius * math.cos(theta), 0.0, radius * math.sin(theta))


class Driver:
    """Streams packets for consecutive laps, keeping one packet counter."""

    def __init__(self, car_id: int = 7) -> None:
        self.pid = 0
        self.car_id = car_id

    def packet(
        self,
        lap: int,
        theta: float,
        radius: float,
        speed: float,
        last_lap_ms: int = -1,
        gap: int = 1,
        **kw: object,
    ) -> TelemetryPacket:
        self.pid += gap
        return parse_packet(
            build_packet(
                packet_id=self.pid,
                current_lap=lap,
                position=at_angle(theta, radius),
                speed_mps=speed,
                last_lap_time_ms=last_lap_ms,
                flags=ON_TRACK,
                car_id=self.car_id,
                **kw,  # type: ignore[arg-type]
            )
        )

    def lap(
        self,
        lap: int,
        *,
        radius: float = RADIUS,
        omega: float = 50.0 / RADIUS,
        start: float = 0.0,
        end: float = 2 * math.pi,
        first_gap: int = 1,
        last_lap_ms: int = -1,
    ) -> Iterator[TelemetryPacket]:
        """One lap's packets from angle `start` up to (not over) `end`, at
        angular speed `omega`. The first comes `first_gap` frames after the
        previous packet, half that gap past `start` — the counter step that
        begins the lap — and every packet reports `last_lap_ms`, as GT7
        keeps reporting the previous lap's time."""
        speed = omega * radius
        theta = start + omega * TICK * first_gap / 2
        gap = first_gap
        while theta < end:
            yield self.packet(lap, theta, radius, speed, last_lap_ms, gap=gap)
            gap = 1
            theta += omega * TICK

    def cross(
        self, lap: int, last_lap_ms: int, *, radius: float = RADIUS, omega: float = 50.0 / RADIUS
    ) -> TelemetryPacket:
        """Just the first packet of `lap`: crossing the line completes the
        lap before it."""
        return next(self.lap(lap, radius=radius, omega=omega, last_lap_ms=last_lap_ms))
