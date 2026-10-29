"""Native sector timing support for Race Engineer."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from app.processing.sectors import Boundary, boundary_points


def validated_sector_boundaries(
    authored: list[dict[str, Any]],
) -> list[Boundary] | None:
    """Return complete authored sector boundaries, never fallback thirds.

    Any non-empty, sequential set of authored boundaries is supported.
    Invalid or incomplete configurations disable sector announcements.
    """
    if not authored:
        return None

    for expected_number, raw in enumerate(authored, start=1):
        if not isinstance(raw, dict):
            return None

        try:
            number = int(raw["n"])
            end = raw["end"]
            x = float(end["x"])
            z = float(end["z"])
        except (KeyError, TypeError, ValueError, OverflowError):
            return None

        if number != expected_number:
            return None

        if not math.isfinite(x) or not math.isfinite(z):
            return None

    points, is_authored = boundary_points(authored, None)

    if not is_authored or len(points) != len(authored):
        return None

    positions = [(point["x"], point["z"]) for point in points]
    if len(set(positions)) != len(positions):
        return None

    return points


# Maximum distance from an authored boundary for a live crossing.
LIVE_BOUNDARY_TOLERANCE_M = 5.0

# Reject large jumps caused by discontinuous telemetry or teleportation.
MAX_LIVE_SAMPLE_DISTANCE_M = 30.0

# Never interpolate across a telemetry interruption longer than one second.
MAX_LIVE_SAMPLE_INTERVAL_S = 1.0


def interpolated_boundary_crossing(
    previous: tuple[float, float, float],
    current: tuple[float, float, float],
    boundary: Boundary,
) -> float | None:
    """Estimate when the car passed an authored boundary.

    Each sample is (position_x, position_z, lap_elapsed_seconds).
    The boundary is a point, so this estimates closest passage
    rather than crossing an oriented timing line.
    """
    x0, z0, t0 = previous
    x1, z1, t1 = current

    values = (x0, z0, t0, x1, z1, t1)
    if not all(math.isfinite(value) for value in values):
        return None

    if t1 <= t0 or t0 < 0:
        return None

    if t1 - t0 > MAX_LIVE_SAMPLE_INTERVAL_S:
        return None

    dx = x1 - x0
    dz = z1 - z0
    distance_squared = dx * dx + dz * dz

    if not 0 < distance_squared <= MAX_LIVE_SAMPLE_DISTANCE_M**2:
        return None

    bx = boundary["x"]
    bz = boundary["z"]

    # Projection of the boundary onto the car's movement segment.
    fraction = ((bx - x0) * dx + (bz - z0) * dz) / distance_squared

    if not 0.0 <= fraction <= 1.0:
        return None

    nearest_x = x0 + fraction * dx
    nearest_z = z0 + fraction * dz

    gap_squared = (nearest_x - bx) ** 2 + (nearest_z - bz) ** 2

    if gap_squared > LIVE_BOUNDARY_TOLERANCE_M**2:
        return None

    return t0 + fraction * (t1 - t0)


@dataclass(frozen=True, slots=True)
class SectorCrossing:
    """A completed sector detected from live telemetry."""

    sector: int
    time_ms: float
    cumulative_ms: float


class LiveSectorTracker:
    """Track authored sector crossings in order, once per lap."""

    def __init__(self) -> None:
        self._boundaries: list[Boundary] = []
        self._directions: list[tuple[float, float]] = []
        self._lap: int | None = None
        self._previous: tuple[float, float, float] | None = None
        self._next_sector = 1
        self._last_crossing_ms = 0.0

    def configure(
        self,
        boundaries: list[Boundary],
        *,
        directions: list[tuple[float, float]] | None = None,
    ) -> None:
        """Configure authored boundaries and reference travel directions."""
        self._boundaries = list(boundaries)
        self._directions = []

        if boundaries and directions is not None and len(directions) == len(boundaries):
            valid = True
            for dx, dz in directions:
                magnitude = math.hypot(dx, dz)
                if not math.isfinite(magnitude) or magnitude < 0.01:
                    valid = False
                    break

            if valid:
                self._directions = [
                    (dx / math.hypot(dx, dz), dz / math.hypot(dx, dz))
                    for dx, dz in directions
                ]

        self.reset()

    def reset(self) -> None:
        """Clear timing state, retaining configured boundaries."""
        self._lap = None
        self._previous = None
        self._next_sector = 1
        self._last_crossing_ms = 0.0

    def interrupt(self) -> None:
        """Break position continuity while preserving sector progress."""
        self._previous = None

    def observe(
        self,
        lap: int,
        sample: tuple[float, float, float],
    ) -> SectorCrossing | None:
        """Process one live sample and return a newly completed sector."""
        if lap != self._lap:
            self.reset()
            self._lap = lap

        if (
            lap <= 0
            or not self._boundaries
            or len(self._directions) != len(self._boundaries)
        ):
            return None

        previous = self._previous
        self._previous = sample

        if previous is None or self._next_sector > len(self._boundaries):
            return None

        boundary = self._boundaries[self._next_sector - 1]
        direction = self._directions[self._next_sector - 1]

        movement_x = sample[0] - previous[0]
        movement_z = sample[1] - previous[1]
        movement_length = math.hypot(movement_x, movement_z)

        if not math.isfinite(movement_length) or movement_length < 0.01:
            return None

        alignment = (
            movement_x * direction[0] + movement_z * direction[1]
        ) / movement_length

        # Reject reverse and substantially sideways movement.
        if alignment < 0.5:
            return None

        crossing_s = interpolated_boundary_crossing(
            previous,
            sample,
            boundary,
        )

        if crossing_s is None:
            return None

        cumulative_ms = crossing_s * 1000.0
        sector_ms = cumulative_ms - self._last_crossing_ms

        if sector_ms <= 0:
            return None

        result = SectorCrossing(
            sector=self._next_sector,
            time_ms=sector_ms,
            cumulative_ms=cumulative_ms,
        )

        self._last_crossing_ms = cumulative_ms
        self._next_sector += 1

        return result

    def complete(self, lap: int, lap_time_ms: float) -> SectorCrossing | None:
        """Complete the final sector only after all authored boundaries were crossed."""
        if (
            lap <= 0
            or lap != self._lap
            or not self._boundaries
            or len(self._directions) != len(self._boundaries)
            or self._next_sector != len(self._boundaries) + 1
            or not math.isfinite(lap_time_ms)
            or lap_time_ms <= self._last_crossing_ms
        ):
            return None

        final = SectorCrossing(
            sector=self._next_sector,
            time_ms=lap_time_ms - self._last_crossing_ms,
            cumulative_ms=lap_time_ms,
        )
        self._next_sector += 1
        return final


def reference_sector_splits(
    samples: dict[str, list[float]],
    boundaries: list[Boundary],
    directions: list[tuple[float, float]],
    time_ms: float,
) -> list[float] | None:
    """The reference lap's sector times, timed by the rule live laps are.

    The reference is replayed through a LiveSectorTracker, so each boundary
    is crossed where interpolated_boundary_crossing puts it, between two
    samples. processing.sectors.splits_ms reads the clock at the NEAREST
    sample instead, up to half a frame away (more across dropped frames),
    and a lap driven exactly like the reference would then hear a sector
    gained or lost that was never there. None when the replay misses a
    boundary, which a live lap on the same line would miss too.
    """
    xs = samples.get("pos_x") or []
    zs = samples.get("pos_z") or []
    ts = samples.get("t") or []
    tracker = LiveSectorTracker()
    tracker.configure(boundaries, directions=directions)
    splits: list[float] = []
    for sample in zip(xs, zs, ts, strict=False):
        crossing = tracker.observe(1, sample)
        if crossing is not None:
            splits.append(crossing.time_ms)
    final = tracker.complete(1, time_ms)
    if final is None:
        return None
    return [*splits, final.time_ms]


def reference_sector_directions(
    samples: dict[str, list[float]],
    boundaries: list[Boundary],
) -> list[tuple[float, float]] | None:
    """Derive forward travel directions at authored sector boundaries."""
    from app.processing.analysis import (
        CORNER_ANCHOR_MAX_M,
        _lap_path,
        _nearest_index,
    )

    if not boundaries:
        return None

    distances, xs, zs, _ = _lap_path(samples)

    if len(distances) < 8:
        return None

    directions: list[tuple[float, float]] = []
    boundary_distances: list[float] = []

    for boundary in boundaries:
        index, gap = _nearest_index(
            xs,
            zs,
            boundary["x"],
            boundary["z"],
        )

        if gap > CORNER_ANCHOR_MAX_M:
            return None

        if index <= 0 or index >= len(xs) - 1:
            return None

        # Use neighbouring samples to estimate the local track tangent.
        dx = xs[index + 1] - xs[index - 1]
        dz = zs[index + 1] - zs[index - 1]

        magnitude = math.hypot(dx, dz)

        if not math.isfinite(magnitude) or magnitude < 0.01:
            return None

        directions.append((dx / magnitude, dz / magnitude))
        boundary_distances.append(distances[index])

    # Boundaries must occur in the correct order on the reference lap.
    if any(
        later <= earlier
        for earlier, later in zip(
            boundary_distances, boundary_distances[1:], strict=False
        )
    ):
        return None

    return directions
