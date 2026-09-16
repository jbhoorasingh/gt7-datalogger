"""Lining laps up by where they were on track, not how far they had gone.

A lap's `dist` is integrated from its own speed, so two laps reach the same
metre mark at different places: a wider line is a longer lap, and a slide or
a run of dropped frames moves the car further than its speed says. Over 141
real lap pairs, "the same distance" was a median 2.9 m apart on track and
64 m at p99 — so a time delta taken at equal distance compared the laps at
different corners, and map dots meant to sit side by side did not.

The fix is one shared axis: the REFERENCE lap's own distance. Every other lap
is walked along the reference's driven path and each of its samples is given
the reference distance of the point it was level with (the perpendicular
projection onto that path). A lap whose `dist` is replaced by that is lined
up with the reference by place, and everything downstream that already works
on `dist` — resampling, the delta, the corner report, peak markers, event
bands — lines up with it for free.

Tracking is sequential and local: each sample is searched for only a little
behind and ahead of the last one, so a circuit that crosses itself (Suzuka)
or runs back alongside itself cannot capture the car on the wrong branch.
When the local search has plainly lost the car — GT7 reset it onto the track,
sometimes kilometres away, in one tick — the whole path is searched instead.
The axis handed out never runs backwards (a spin, or a rewind, holds it until
the car is past where it had been).

A lap that cannot be tracked with confidence keeps its own distance, which is
what every comparison used before: better an honest old axis than a wrong
new one.
"""

from __future__ import annotations

import math
from typing import Any

from app.processing.analysis import Samples, _interp, resample_by_distance

# Spacing of the reference path the laps are projected onto. The projection
# is onto the straight segments between these points, so in the tightest
# hairpin (radius ~10 m) a 2.5 m chord is 8 cm off the arc.
PATH_STEP_M = 2.5
# How far behind and ahead of the last located point a sample is searched
# for — ahead also by twice the distance the lap itself says it covered since,
# which covers dropped frames at any speed. At 60 Hz a car moves under 2 m a
# tick, so this is several ticks of slack either way.
BEHIND_M = 5.0
AHEAD_M = 10.0
# A local best further off the path than this has lost the car (a reset, a
# rewind) — unless the whole-path search finds nothing better. An excursion
# into a run-off area can be this far off and still be where it is, so the
# whole-path answer must also be at least twice as close to be believed.
REACQUIRE_M = 50.0
# Where the first sample may be found: around the start of the path (the lap
# began at the line, or near it), and this far off it at most.
START_WINDOW_M = 60.0
START_LATERAL_M = 25.0
# The first and last segments are extended to place a sample before the
# reference's first point or past its last — a lap that began a few metres
# behind the line, or ran on past the reference's final sample. No further.
EXTEND_MAX_M = 150.0
# A lap is aligned only if at most this share of its samples were off the
# path by more than REACQUIRE_M (the pit lane counts as on it: it runs
# alongside the circuit).
MAX_OFF_PATH_SHARE = 0.1
# Reference segments this many times longer than the distance they span are
# resets in the reference itself, not road: never projected onto.
TELEPORT_RATIO = 10.0


class ReferencePath:
    """The reference lap's driven line, parametrised by its own distance."""

    def __init__(self, samples: Samples) -> None:
        grid = resample_by_distance(samples, PATH_STEP_M, ("pos_x", "pos_z"))
        self.s: list[float] = grid["dist"]
        self.x: list[float] = grid.get("pos_x", [])
        self.z: list[float] = grid.get("pos_z", [])
        n = min(len(self.s), len(self.x), len(self.z))
        self.segments = max(0, n - 1)
        self.length = self.s[n - 1] if n else 0.0
        # Per-segment geometry, computed once: projection runs for every
        # sample of every compared lap. A segment is None when there is
        # nothing to project onto — no length, or a reset in the reference.
        self._geometry: list[tuple[float, float, float, float, float] | None] = []
        for i in range(self.segments):
            vx, vz = self.x[i + 1] - self.x[i], self.z[i + 1] - self.z[i]
            len2 = vx * vx + vz * vz
            ds = self.s[i + 1] - self.s[i]
            if len2 < 1e-9 or math.sqrt(len2) > TELEPORT_RATIO * max(ds, 1.0):
                self._geometry.append(None)
                continue
            # (dx, dz, |d|², how far f may run past the segment's ends, ds)
            self._geometry.append((vx, vz, len2, EXTEND_MAX_M / math.sqrt(len2), ds))

    def project(
        self, x: float, z: float, lo: float = -math.inf, hi: float = math.inf
    ) -> tuple[float, float]:
        """(distance along the path, metres off it) of the nearest point on
        the path between distances `lo` and `hi`."""
        if self.segments == 0:
            return 0.0, math.inf
        first = 0 if lo == -math.inf else max(0, int(lo / PATH_STEP_M) - 1)
        last = (
            self.segments - 1
            if hi == math.inf
            else min(self.segments - 1, int(hi / PATH_STEP_M) + 1)
        )
        xs, zs, ss, geometry = self.x, self.z, self.s, self._geometry
        end = self.segments - 1
        best_s, best_d2 = 0.0, math.inf
        for i in range(first, last + 1):
            g = geometry[i]
            if g is None:
                continue
            vx, vz, len2, reach, ds = g
            px, pz = x - xs[i], z - zs[i]
            f = (px * vx + pz * vz) / len2
            # Only the ends of the path extend past their segment, and not
            # without limit.
            if f < 0.0:
                f = max(f, -reach) if i == 0 else 0.0
            elif f > 1.0:
                f = min(f, 1.0 + reach) if i == end else 1.0
            dx, dz = f * vx - px, f * vz - pz
            d2 = dx * dx + dz * dz
            if d2 < best_d2:
                best_d2 = d2
                best_s = ss[i] + f * ds
        return best_s, math.sqrt(best_d2)


class PathTracker:
    """Follows one lap along a reference path, sample by sample."""

    def __init__(self, path: ReferencePath) -> None:
        self.path = path
        self.reset()

    def reset(self) -> None:
        self._at: float | None = None
        self._own = 0.0

    @property
    def started(self) -> bool:
        return self._at is not None

    def locate(self, x: float, z: float, own_dist: float) -> tuple[float, float] | None:
        """(reference distance, metres off the path) for the lap's next
        sample, or None when the lap could not be placed at its start.

        `own_dist` is the lap's own integrated distance at the sample: its
        growth since the last call says how far ahead to look, and at the
        start where to.
        """
        path = self.path
        if self._at is None:
            found = self._start(x, z, own_dist)
            if found is None:
                return None
            s, off = found
        else:
            advance = max(0.0, own_dist - self._own)
            s, off = path.project(
                x, z, self._at - BEHIND_M, self._at + AHEAD_M + 2 * advance
            )
            if off > REACQUIRE_M:
                s2, off2 = path.project(x, z)
                if off2 * 2 <= off:
                    s, off = s2, off2
        self._at = s
        self._own = own_dist
        return s, off

    def _start(self, x: float, z: float, own_dist: float) -> tuple[float, float] | None:
        path = self.path
        # Near where the lap's own distance says it is: the line at the start
        # of a lap, somewhere further on for a tracker picked up mid-lap.
        s, off = path.project(
            x, z, own_dist * 0.9 - START_WINDOW_M, own_dist * 1.1 + START_WINDOW_M
        )
        if off <= START_LATERAL_M:
            return s, off
        s, off = path.project(x, z)
        if off > START_LATERAL_M:
            return None
        # Found far from the start at the start of a lap — a grid ahead of
        # the line — is believable only in the first half of the path. In the
        # second it is the approach to the line, where the car cannot be at
        # the start of its lap without an axis that runs from minus something.
        if own_dist < START_WINDOW_M and s > path.length / 2:
            return None
        return s, off


def align_to_reference(samples: Samples, path: ReferencePath) -> Samples | None:
    """A copy of `samples` whose `dist` is the reference path's distance at
    each sample, or None when the lap cannot be lined up with confidence."""
    xs = samples.get("pos_x") or []
    zs = samples.get("pos_z") or []
    own = samples.get("dist") or []
    n = min(len(xs), len(zs), len(own))
    if n < 2 or path.segments == 0:
        return None
    tracker = PathTracker(path)
    aligned: list[float] = []
    furthest = -math.inf
    off_path = 0
    for k in range(n):
        found = tracker.locate(xs[k], zs[k], own[k])
        if found is None:
            return None
        s, off = found
        if off > REACQUIRE_M:
            off_path += 1
        furthest = max(furthest, s)
        aligned.append(round(furthest, 2))
    if off_path > MAX_OFF_PATH_SHARE * n:
        return None
    out = dict(samples)
    out["dist"] = aligned
    return out


def remap_events(
    events: list[dict[str, Any]], own_dist: list[float], aligned_dist: list[float]
) -> list[dict[str, Any]]:
    """The lap's events with every `*_dist` moved onto the aligned axis."""
    if not own_dist:
        return events
    out: list[dict[str, Any]] = []
    for event in events:
        moved = dict(event)
        for key, value in event.items():
            if key.endswith("_dist") and isinstance(value, int | float):
                moved[key] = round(_interp(own_dist, aligned_dist, float(value)), 1)
        out.append(moved)
    return out
