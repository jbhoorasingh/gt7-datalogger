"""Judge car positions against the SURVEYED road edges (#41).

The per-tick surface flags (processing/surface.py) say what the wheels touch;
they miss the excursion that stays paved — a tarmac runoff is off-track by
the rules and "T" to the packet. The compiled survey knows where the road's
edges actually ARE: track_compile's road quads tile the surface between the
two border curves, and a car whose position leaves them has left the road,
whatever it drove onto.

The two verdicts stay separately reported (off_track_count vs
off_survey_count): each can fire without the other, and folding them into
one number would hide which rule the lap broke.

Honesty rule, inherited from the survey: unsurveyed ground must NEVER read
as an excursion. A point far from any quad is "unknown", a partially
surveyed circuit judges only the stretches it knows, and a lap mostly over
unsurveyed ground refuses a verdict (-1).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from app.processing import track_compile
from app.processing.surface import OFF_TRACK_MIN_TICKS

# The judged point is the car's CENTRE; the border is the limit, and a centre
# within a metre of it (half a car's width) still has wheels on the road.
EDGE_MARGIN_M = 1.0
# Beyond the edge but within this of the road: the road here is known and the
# car is not on it — "off". Further out nothing says the survey ever saw this
# ground — "unknown".
NEAR_ROAD_M = 30.0
# Spatial hash pitch. Quads are ~4 m long and a query reaches NEAR_ROAD_M, so
# a point's cell plus its neighbours covers every candidate.
CELL_M = 40.0
# Only a survey that resolved most of its boundary to road may judge laps: a
# handful of quads under a full circuit would leave most samples "unknown"
# and the verdict worthless either way.
MIN_ROAD_PCT = 50.0


def _inside_convex(x: float, z: float, q: Sequence[float]) -> bool:
    """Point-in-convex-quad ([x1,z1,...,x4,z4]), tolerant of either winding:
    the point must sit on one consistent side of every non-degenerate edge."""
    sign = 0
    for k in range(4):
        ax, az = q[2 * k], q[2 * k + 1]
        bx, bz = q[(2 * k + 2) % 8], q[(2 * k + 3) % 8]
        cross = (bx - ax) * (z - az) - (bz - az) * (x - ax)
        if abs(cross) < 1e-9:
            continue  # on (or along) this edge — the others decide
        s = 1 if cross > 0 else -1
        if sign == 0:
            sign = s
        elif s != sign:
            return False
    return True


def _segment_distance(
    x: float, z: float, ax: float, az: float, bx: float, bz: float
) -> float:
    dx, dz = bx - ax, bz - az
    seg2 = dx * dx + dz * dz
    t = 0.0 if seg2 < 1e-12 else max(0.0, min(1.0, ((x - ax) * dx + (z - az) * dz) / seg2))
    return math.hypot(ax + dx * t - x, az + dz * t - z)


class RoadJudge:
    """Point-vs-surveyed-road classifier over one compiled track document."""

    def __init__(self, compiled: dict[str, Any]) -> None:
        self._quads: list[tuple[float, ...]] = [tuple(q) for q in compiled["road"]]
        self._bbox: list[tuple[float, float, float, float]] = []
        self._cells: dict[tuple[int, int], list[int]] = {}
        for i, q in enumerate(self._quads):
            xs, zs = q[0::2], q[1::2]
            bb = (min(xs), min(zs), max(xs), max(zs))
            self._bbox.append(bb)
            for gx in range(math.floor(bb[0] / CELL_M), math.floor(bb[2] / CELL_M) + 1):
                for gz in range(math.floor(bb[1] / CELL_M), math.floor(bb[3] / CELL_M) + 1):
                    self._cells.setdefault((gx, gz), []).append(i)

    def classify(self, x: float, z: float) -> str:
        """"on" | "off" | "unknown" for one point (see module docstring)."""
        return self._classify(x, z, -1)[0]

    def _classify(self, x: float, z: float, hint: int) -> tuple[str, int]:
        """Verdict plus the quad that carried it, offered back as `hint`: at
        60 Hz consecutive samples almost always resolve to the same quad."""
        near = -1
        if 0 <= hint < len(self._quads):
            d = self._quad_distance(x, z, hint)
            if d <= EDGE_MARGIN_M:
                return "on", hint
            if d <= NEAR_ROAD_M:
                near = hint
        gx0 = math.floor((x - NEAR_ROAD_M) / CELL_M)
        gx1 = math.floor((x + NEAR_ROAD_M) / CELL_M)
        gz0 = math.floor((z - NEAR_ROAD_M) / CELL_M)
        gz1 = math.floor((z + NEAR_ROAD_M) / CELL_M)
        seen: set[int] = set()  # a quad's bbox can register in several cells
        for gx in range(gx0, gx1 + 1):
            for gz in range(gz0, gz1 + 1):
                for i in self._cells.get((gx, gz), ()):
                    if i == hint or i in seen:
                        continue
                    seen.add(i)
                    # cheap bbox lower bound before the exact edge distances
                    bb = self._bbox[i]
                    dx = max(bb[0] - x, 0.0, x - bb[2])
                    dz = max(bb[1] - z, 0.0, z - bb[3])
                    if dx * dx + dz * dz > NEAR_ROAD_M * NEAR_ROAD_M:
                        continue
                    d = self._quad_distance(x, z, i)
                    if d <= EDGE_MARGIN_M:
                        return "on", i
                    if d <= NEAR_ROAD_M and near < 0:
                        near = i
        return ("off", near) if near >= 0 else ("unknown", -1)

    def _quad_distance(self, x: float, z: float, i: int) -> float:
        q = self._quads[i]
        if _inside_convex(x, z, q):
            return 0.0
        return min(
            _segment_distance(
                x, z, q[2 * k], q[2 * k + 1], q[(2 * k + 2) % 8], q[(2 * k + 3) % 8]
            )
            for k in range(4)
        )

    def excursions(self, pos_x: Sequence[float], pos_z: Sequence[float]) -> int:
        """Count sustained runs beyond the surveyed edge in one lap's trace.

        Same shape as surface.off_track_excursions: a run must hold for
        OFF_TRACK_MIN_TICKS to count. "unknown" breaks a run WITHOUT counting
        it — leaving the surveyed map is not proof of leaving the road — and
        a lap where fewer than half the samples classify at all gets -1: not
        enough surveyed road under it to judge.
        """
        n = len(pos_x)
        if n == 0:
            return -1
        known = 0
        count = 0
        run = 0
        hint = -1
        for x, z in zip(pos_x, pos_z, strict=True):
            verdict, hint = self._classify(x, z, hint)
            if verdict == "unknown":
                run = 0
                continue
            known += 1
            if verdict == "off":
                run += 1
                if run == OFF_TRACK_MIN_TICKS:
                    count += 1
            else:
                run = 0
        if 2 * known < n:
            return -1
        return count


def judge_for_track(data_dir: Path, track: str) -> RoadJudge | None:
    """The judge for a circuit, or None when its survey cannot support one.

    Gated on coverage["road_pct"] >= MIN_ROAD_PCT. Blocking (recompiles a
    stale bundle, then hashes the quads): callers run it off the event loop,
    as they do for track_compile itself.
    """
    compiled = track_compile.for_track(data_dir, track)
    if compiled is None or compiled["coverage"]["road_pct"] < MIN_ROAD_PCT:
        return None
    if not compiled["road"]:
        return None
    return RoadJudge(compiled)
