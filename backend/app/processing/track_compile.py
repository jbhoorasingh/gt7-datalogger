"""Compile a survey bundle's border evidence into ordered vector geometry (#38, #40).

The bundle stores borders as an unordered cloud of voted 1 m cells. That is
the right shape for accumulating evidence, and the wrong shape for everything
downstream: drawing a road, measuring how much of the boundary is actually
surveyed, or asking whether a lap stayed inside it. All three need the cells
put in ORDER.

Ordering is reconstructed, never assumed. Each side's cells are walked into
chains — nearest plausible next cell, gated on travel direction and on the
recorded heading being tangent to the walk — and chains are stitched
end-to-end where their endpoints face each other. On real bundles this pulls
96-99% of a side's cells into a handful of polylines, usually one per side
that closes into a loop (measured on the author's four surveyed circuits; the
stitch gates were calibrated there too).

Where a circuit crosses over itself the two levels' cells interleave in
plan (#96). Every step here is gated on elevation where it is known: the walk
and the stitch refuse a next cell the road could not have climbed to, and the
across-the-road pairing prefers the sample's own level. A bundle without
elevation compiles exactly as it did before.

Honesty rule, inherited from track_outline: never draw a confident wrong
loop. A stitch across more than SURVEYED_MAX_SPACING_M is kept as ordering
information but flagged as a GAP span: it is excluded from the drawn borders,
excluded from the centerline, and counted against coverage. A partially
surveyed circuit compiles into exactly the fragments that were driven.

What comes out per bundle:
  - border polylines per side, split at gap spans, with elevation where known
  - the centerline: left border samples paired ACROSS to the right border
    curve (point-to-curve, which is what the old point-to-point pairing in
    track_outline.road_quads could not do — on real bundles the two sides'
    cells almost never sit directly opposite each other, so it paired 1-4% of
    points; pairing against the interpolated curve instead reaches whatever
    the sparser side covers), each sample carrying road width and elevation
  - the road surface as a quad strip between consecutive paired samples, each
    quad with its elevation envelope, so a judge can tell a bridge deck from
    the road beneath it (#96)
  - per-side coverage measured against the boundary itself: surveyed metres
    over total boundary metres, the denominator including every flagged gap
    and, on a closed loop, the closure. This is #38's metric — the driven
    trail no longer appears in it.

The compiled document is derived data: recomputed from the bundle whenever
the bundle file changes (auto-recompile, #40), persisted beside the bundles
under track-bundles/compiled/, and never exported or imported — an imported
bundle brings evidence, and this module rebuilds the geometry from it.
"""

from __future__ import annotations

import json
import logging
import math
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.processing import track_bundle

log = logging.getLogger(__name__)

COMPILED_FORMAT = "gt7-datalogger-track-compiled"
# 2: per-quad elevation envelopes (`road_y`) and level-gated ordering (#96).
# 3: smoothed borders, and the `smoothing` key that says whether they are.
# Bumping it is what recompiles every stored document once — the bundle files
# themselves did not change, so the identity check alone would keep serving
# geometry that walks across levels.
COMPILED_VERSION = 3
COMPILED_DIR = "compiled"  # under track-bundles/

# --- chain walking ------------------------------------------------------------
# One border cell per metre per side when surveyed continuously; on the
# author's bundles 100% of cells have a same-side neighbour within 4 m, so a
# 5 m step never breaks a surveyed stretch but cannot leap the ~10 m to the
# opposite side of the road.
STEP_M = 5.0
DIR_MIN_DOT = 0.35  # next cell must be at least this forward of the walk
TANGENT_MIN_DOT = 0.5  # its recorded heading must be near-tangent (sign-free)
MIN_CHAIN_PTS = 5  # shorter fragments are noise, not border

# --- chain stitching ----------------------------------------------------------
# Endpoint joins are graded: a short hop may curve, a long leap must be a
# near-straight continuation, and nothing joins beyond 40 m. The grades come
# from the real failure cases: survey holes of 15-35 m with continuation
# agreement of 0.97-1.00 (join them), against a curved junction at 33 m with
# agreement 0.20 that genuinely should stay split.
GAP_JOIN_M = 40.0
_JOIN_GATES = ((25.0, 0.75), (8.0, 0.6), (0.0, 0.2))  # (above metres, min dot)

# A span longer than this is a gap in the survey, not a stride between two
# adjacent cells: flagged, undrawn, counted against coverage.
SURVEYED_MAX_SPACING_M = 6.0

# --- centerline ---------------------------------------------------------------
CL_STEP_M = 4.0  # centerline sample spacing along the left border
# Cross-section plausibility, same figures as track_outline / surveyGeometry.
ROAD_WIDTH_MIN_M = 3.0
ROAD_WIDTH_MAX_M = 40.0
ACROSS_MIN_DOT = 0.7  # L->R direction vs the left sample's right-normal (~45°)

_GRID_CELL_M = ROAD_WIDTH_MAX_M

# Simplification tolerance for stored border polylines. Half the bundle's own
# grid pitch: below the noise floor of the evidence, so it only sheds
# collinear points.
SIMPLIFY_TOL_M = 0.5

# --- border smoothing ---------------------------------------------------------
# A record sits on a 1 m grid, and an `edge` record and a `straddle` record of
# the same kerb disagree by up to a metre about where it is, so an ordered
# border steps sideways wherever the kind changes: on Tsukuba, every one of
# the fifteen lateral steps over 0.7 m is at a change of kind. Drawn as-is
# that is a border with nicks in it, and a road whose edge moves a metre in a
# metre.
#
# The obvious cure is the wrong one. A moving average pulls every curve
# towards its inside: measured against corners whose true border is known, a
# ±8 m window cuts 2.0 m off a 3 m kerb and 1.4 m off a 10 m hairpin — more
# than track_limits' whole edge margin, on exactly the corners where laps are
# judged. Taubin's λ|μ pass shrinks and then inflates by slightly more, which
# cancels the pull: it stays within 0.35 m of the truth on all of those —
# closer than the unsmoothed grid on every corner of 6 m radius and up, and
# within 5 cm of it on the 3 m kerb — and moves the area Tsukuba's left border
# encloses by 2 m² in 75 491. Over the 24 circuits published when this was
# written it took 3 103 kinks to 19, changed no gap span, and moved coverage
# by 0.3 of a point at most (a zigzag is longer than the line through it, so
# surveyed metres fall a little against gaps that do not).
#
# These figures are the contract. `tools/track_editor/track-editor-core.mjs`
# in the track-data repository carries the same routine for the editor, and
# both are held to one set of vectors; change one, change both.
SMOOTH_BORDERS = True  # what compile_bundle does when nobody says otherwise
SMOOTH_ITERATIONS = 10
SMOOTH_LAMBDA = 0.5
SMOOTH_MU = -0.53
# No vertex ends further than this from the evidence, whatever the passes
# wanted. Under the bundle's grid pitch and under track_limits.EDGE_MARGIN_M,
# so smoothing can never be the reason a lap reads as off the road.
SMOOTH_CAP_M = 0.75
_SMOOTH_MIN_SPACING_M = 1e-3  # two records on one spot must not own the average

# --- road levels (#96) --------------------------------------------------------
# Two cells are on the same road when their elevations agree to within what
# the road could climb between them: the bundle's own level rule outright
# (LEVEL_SEP_M, which covers every step of the walk), or noise plus a grade
# over the distance for the longer stitches, so a steep hill still joins
# across a survey hole. The steepest surveyed border (Mount Panorama) climbs
# 0.33 m/m at the 99th percentile between neighbouring cells, lateral scatter
# included; 0.35 keeps every real stretch and still refuses a deck 5 m up at
# any distance under ~11 m.
MAX_GRADE = 0.35
LEVEL_NOISE_M = 1.0
# Pairing the left border across to the right tries the nearest point on the
# sample's own level first and the nearest regardless of level second: a
# preference with a fallback rather than a gate, because banking puts the
# two borders of ONE road 5.5 m apart at Daytona, where the far border is on
# "another level" by the bundle's rule and must still be found. Measured on
# the collected bundles this reproduces the previous pairing to within two
# quads (a stretch of Lago Maggiore Centre where the nearest border point
# fails the width gate and the nearest same-level one passes); what the
# preference changes is what happens at a crossover.

_CACHE: dict[tuple[str, int, int], dict[str, Any]] = {}
_CACHE_MAX = 8


def _norm(x: float, z: float) -> tuple[float, float]:
    d = math.hypot(x, z) or 1.0
    return x / d, z / d


def _same_level(a: dict[str, Any], b: dict[str, Any], d: float) -> bool:
    """Could the road run from cell `a` to cell `b`, `d` metres apart, without
    leaving its level? True whenever either elevation is unknown."""
    ya, yb = a.get("y"), b.get("y")
    if ya is None or yb is None:
        return True
    return abs(float(ya) - float(yb)) <= max(
        track_bundle.LEVEL_SEP_M, LEVEL_NOISE_M + MAX_GRADE * d
    )


class _Grid:
    """Spatial hash over point dicts, for radius queries."""

    def __init__(self, pts: list[dict[str, Any]], cell: float) -> None:
        self.pts = pts
        self.cell = cell
        self.map: dict[tuple[int, int], list[int]] = {}
        for i, p in enumerate(pts):
            self.map.setdefault(self._key(p["x"], p["z"]), []).append(i)

    def _key(self, x: float, z: float) -> tuple[int, int]:
        return (math.floor(x / self.cell), math.floor(z / self.cell))

    def near(self, x: float, z: float, radius: float) -> Iterator[tuple[int, float]]:
        cx, cz = self._key(x, z)
        r = int(radius // self.cell) + 1
        for gx in range(cx - r, cx + r + 1):
            for gz in range(cz - r, cz + r + 1):
                for i in self.map.get((gx, gz), ()):
                    p = self.pts[i]
                    d = math.hypot(p["x"] - x, p["z"] - z)
                    if d <= radius:
                        yield i, d


def chain_side(pts: list[dict[str, Any]]) -> list[list[int]]:
    """Walk one side's cells into ordered chains (lists of indices into pts).

    Greedy: from the current cell, step to the nearest unvisited cell that is
    forward of the walk direction and whose recorded heading is near-tangent
    to it. The heading gate is sign-free because one border carries evidence
    from runs driven in both directions. Scoring distance / forwardness keeps
    the walk moving along the border rather than hopping sideways within the
    1-2 m wide band that repeated surveys of one edge lay down.
    """
    n = len(pts)
    grid = _Grid(pts, STEP_M)
    visited = [False] * n

    def walk(seed: int, dx: float, dz: float) -> list[int]:
        out: list[int] = []
        cur = seed
        cdx, cdz = dx, dz
        while True:
            best = -1
            best_score = math.inf
            for j, d in grid.near(pts[cur]["x"], pts[cur]["z"], STEP_M):
                if visited[j] or j == cur:
                    continue
                ux, uz = _norm(pts[j]["x"] - pts[cur]["x"], pts[j]["z"] - pts[cur]["z"])
                fwd = ux * cdx + uz * cdz
                if fwd < DIR_MIN_DOT:
                    continue
                if abs(pts[j]["hx"] * cdx + pts[j]["hz"] * cdz) < TANGENT_MIN_DOT:
                    continue
                if not _same_level(pts[cur], pts[j], d):
                    continue  # the other level of a crossover, not the next metre
                score = d / max(fwd, 0.05)
                if score < best_score:
                    best, best_score = j, score
            if best < 0:
                return out
            ux, uz = _norm(pts[best]["x"] - pts[cur]["x"], pts[best]["z"] - pts[cur]["z"])
            # direction persistence: one noisy cell cannot turn the walk
            cdx, cdz = _norm(cdx * 0.5 + ux * 0.5, cdz * 0.5 + uz * 0.5)
            visited[best] = True
            out.append(best)
            cur = best

    chains: list[list[int]] = []
    # Deterministic seed order; the stitch pass makes the result largely
    # insensitive to where each walk happened to start.
    for seed in sorted(range(n), key=lambda i: (pts[i]["x"], pts[i]["z"])):
        if visited[seed]:
            continue
        visited[seed] = True
        hx, hz = pts[seed]["hx"], pts[seed]["hz"]
        if math.hypot(hx, hz) < 1e-6:
            hx, hz = 1.0, 0.0
        fwd = walk(seed, hx, hz)
        back = walk(seed, -hx, -hz)
        chains.append(list(reversed(back)) + [seed] + fwd)
    return [c for c in chains if len(c) >= MIN_CHAIN_PTS]


def _endpoint_dir(
    chain: list[int], pts: list[dict[str, Any]], at_start: bool
) -> tuple[float, float] | None:
    """Travel direction AT an endpoint, pointing out of the chain's end (or
    backward out of its start), averaged over the last few cells."""
    k = min(6, len(chain) - 1)
    if k < 1:
        return None
    a, b = (pts[chain[k]], pts[chain[0]]) if at_start else (pts[chain[-1 - k]], pts[chain[-1]])
    return _norm(b["x"] - a["x"], b["z"] - a["z"])


def stitch(chains: list[list[int]], pts: list[dict[str, Any]]) -> list[list[int]]:
    """Join chains end-to-end where their endpoints face each other."""
    changed = True
    while changed:
        changed = False
        chains.sort(key=len, reverse=True)
        for i in range(len(chains)):
            if changed:
                break
            for j in range(len(chains)):
                if i == j:
                    continue
                a = chains[i]
                for rev in (False, True):
                    b = list(reversed(chains[j])) if rev else chains[j]
                    pa, pb = pts[a[-1]], pts[b[0]]
                    d = math.hypot(pb["x"] - pa["x"], pb["z"] - pa["z"])
                    if d > GAP_JOIN_M or not _same_level(pa, pb, d):
                        continue
                    da = _endpoint_dir(a, pts, at_start=False)
                    db = _endpoint_dir(b, pts, at_start=True)
                    if da is None or db is None:
                        continue
                    # db points backward out of b's start; continuation is -db
                    need = next(dot for above, dot in _JOIN_GATES if d > above)
                    if -(da[0] * db[0] + da[1] * db[1]) < need:
                        continue
                    if d > 0.5:
                        ux, uz = _norm(pb["x"] - pa["x"], pb["z"] - pa["z"])
                        if ux * da[0] + uz * da[1] < 0.0:
                            continue  # b lies behind a's end: joining doubles back
                    chains[i] = a + b
                    del chains[j]
                    changed = True
                    break
                if changed:
                    break
    return chains


def _seg_len(a: dict[str, Any], b: dict[str, Any]) -> float:
    return math.hypot(b["x"] - a["x"], b["z"] - a["z"])


def _xz(vertex: list[float | None]) -> tuple[float, float]:
    """A vertex's plan position. Only index 2 (elevation) may be null."""
    x, z = vertex[0], vertex[1]
    assert x is not None and z is not None
    return x, z


def _simplify(vertices: list[list[float | None]], tol: float) -> list[list[float | None]]:
    """Douglas-Peucker on [x, z, y] vertices (y ignored for the metric)."""
    if len(vertices) < 3:
        return vertices
    ax, az = _xz(vertices[0])
    bx, bz = _xz(vertices[-1])
    dx, dz = bx - ax, bz - az
    seg = math.hypot(dx, dz)
    worst, worst_d = 0, -1.0
    for i in range(1, len(vertices) - 1):
        px, pz = _xz(vertices[i])
        if seg < 1e-9:
            d = math.hypot(px - ax, pz - az)
        else:
            d = abs((px - ax) * dz - (pz - az) * dx) / seg
        if d > worst_d:
            worst, worst_d = i, d
    if worst_d <= tol:
        return [vertices[0], vertices[-1]]
    left = _simplify(vertices[: worst + 1], tol)
    right = _simplify(vertices[worst:], tol)
    return left[:-1] + right


def smooth_run(
    points: list[tuple[float, float]],
    *,
    closed: bool = False,
    iterations: int = SMOOTH_ITERATIONS,
    lam: float = SMOOTH_LAMBDA,
    mu: float = SMOOTH_MU,
    cap: float = SMOOTH_CAP_M,
) -> list[tuple[float, float]]:
    """One ORDERED run of border positions, smoothed without shrinking it.

    Taubin's λ|μ: each pass moves a vertex a fraction of the way to where its
    two neighbours say it should be, first by λ (which smooths, and shrinks)
    and then by μ < -λ (which undoes the shrinking). "Where the neighbours
    say" is weighted by the inverse of each neighbour's original distance,
    which on a straight line is the vertex's own position however unevenly the
    records are spaced — so records do not creep along the border towards
    even spacing and spend the cap on a movement that changes nothing.

    An open run keeps both ends exactly where they were: an end is where the
    survey stopped, and what lies beyond it is not this run's to guess at.
    `closed` is a loop with no ends, smoothed round its seam.

    A pure function of its arguments, and the whole of the smoothing: the
    caller decides what a run is. It must never be handed positions from two
    sides of unsurveyed ground — smoothing drags both ends of a gap into it.
    """
    n = len(points)
    if n < 3 or iterations <= 0:
        return [(float(x), float(z)) for x, z in points]
    orig = [(float(x), float(z)) for x, z in points]

    def spacing(a: int, b: int) -> float:
        return max(
            math.hypot(orig[b][0] - orig[a][0], orig[b][1] - orig[a][1]),
            _SMOOTH_MIN_SPACING_M,
        )

    # (previous, next, weight of previous, weight of next) per moving vertex,
    # from the evidence and fixed for every pass.
    moving: list[tuple[int, int, int, float, float]] = []
    for i in (range(n) if closed else range(1, n - 1)):
        a, b = (i - 1) % n, (i + 1) % n
        moving.append((i, a, b, 1.0 / spacing(a, i), 1.0 / spacing(i, b)))

    cur = orig[:]
    for _ in range(iterations):
        for factor in (lam, mu):
            nxt = cur[:]
            for i, a, b, wa, wb in moving:
                tx = (cur[a][0] * wa + cur[b][0] * wb) / (wa + wb)
                tz = (cur[a][1] * wa + cur[b][1] * wb) / (wa + wb)
                nxt[i] = (
                    cur[i][0] + factor * (tx - cur[i][0]),
                    cur[i][1] + factor * (tz - cur[i][1]),
                )
            cur = nxt

    if cap >= 0:
        for i, *_ in moving:
            dx, dz = cur[i][0] - orig[i][0], cur[i][1] - orig[i][1]
            moved = math.hypot(dx, dz)
            if moved > cap:
                scale = cap / moved
                cur[i] = (orig[i][0] + dx * scale, orig[i][1] + dz * scale)
    return cur


class SideAssembly:
    """One side's border, ordered: the main chain plus leftover fragments.

    Ordering is always read off the evidence as recorded. With `smooth`, the
    positions everything downstream reads — polylines, coverage, the samples
    the centerline pairs from — are then the smoothed ones, so the drawn
    border, the road between the borders and the metres counted are one
    geometry and not two that nearly agree.
    """

    def __init__(self, pts: list[dict[str, Any]], *, smooth: bool = False) -> None:
        self.pts = pts
        self.smoothed = False
        self.chains = stitch(chain_side(pts), pts) if pts else []
        self.chains.sort(key=lambda c: self._length(c), reverse=True)
        self.closed: bool = False
        if self.chains:
            main = self.chains[0]
            a, b = pts[main[0]], pts[main[-1]]
            gap = _seg_len(a, b)
            if gap <= GAP_JOIN_M and len(main) >= 20 and _same_level(a, b, gap):
                da = _endpoint_dir(main, pts, at_start=False)
                db = _endpoint_dir(main, pts, at_start=True)
                if da is not None and db is not None and -(
                    da[0] * db[0] + da[1] * db[1]
                ) >= 0.2:
                    self.closed = True
        if smooth and self.chains:
            self._smooth()

    def _runs(self, ci: int) -> tuple[list[list[int]], bool]:
        """A chain's surveyed runs — its indices split wherever a span is a
        flagged gap — and whether the whole chain is one ring.

        On a loop whose seam is real border the last run carries on into the
        first, so they are returned joined, in driving order: the seam is a
        span like any other and gets no kink of its own for being where the
        walk happened to start.
        """
        chain = self.chains[ci]
        runs: list[list[int]] = [[chain[0]]]
        for a, b in zip(chain, chain[1:], strict=False):
            if _seg_len(self.pts[a], self.pts[b]) > SURVEYED_MAX_SPACING_M:
                runs.append([])
            runs[-1].append(b)
        if ci == 0 and self.closure_surveyed():
            if len(runs) == 1:
                return runs, True
            runs[0] = runs.pop() + runs[0]
        return runs, False

    def _smooth(self) -> None:
        """Replace `pts` with copies carrying smoothed plan positions.

        Copies, because the records are the bundle's own and the bundle is
        evidence: what was surveyed is not rewritten by what was drawn from
        it. Run by run, never across a gap. A run is left exactly as recorded
        if smoothing would stretch any of its spans past the gap threshold —
        the cap makes that all but impossible, and a border that gained a gap
        by being tidied would be reporting coverage the smoother invented.
        """
        pts = list(self.pts)
        for ci in range(len(self.chains)):
            runs, ring = self._runs(ci)
            for run in runs:
                if len(run) < 3:
                    continue
                new = smooth_run(
                    [(self.pts[i]["x"], self.pts[i]["z"]) for i in run], closed=ring
                )
                spans = list(zip(new, new[1:], strict=False))
                if ring:
                    spans.append((new[-1], new[0]))
                if any(
                    math.hypot(b[0] - a[0], b[1] - a[1]) > SURVEYED_MAX_SPACING_M
                    for a, b in spans
                ):
                    continue
                for i, (x, z) in zip(run, new, strict=True):
                    pts[i] = {**self.pts[i], "x": x, "z": z}
        self.pts = pts
        self.smoothed = True

    def _length(self, chain: list[int]) -> float:
        return sum(
            _seg_len(self.pts[a], self.pts[b]) for a, b in zip(chain, chain[1:], strict=False)
        )

    def closure_len(self) -> float:
        """End-to-start distance of the main chain (0.0 when not closed)."""
        if not self.closed or not self.chains:
            return 0.0
        main = self.chains[0]
        return _seg_len(self.pts[main[-1]], self.pts[main[0]])

    def closure_surveyed(self) -> bool:
        """Whether the loop's seam is real border, not a bridged gap."""
        return self.closed and self.closure_len() <= SURVEYED_MAX_SPACING_M

    def coverage(self) -> dict[str, Any]:
        """Surveyed metres against total boundary metres (#38).

        Total = every span the ordering knows about, gaps included, plus the
        closure gap of a closed loop. An open assembly can only speak for the
        boundary it has seen, and says so with closed=false.
        """
        surveyed = 0.0
        gap = 0.0
        for chain in self.chains:
            for a, b in zip(chain, chain[1:], strict=False):
                d = _seg_len(self.pts[a], self.pts[b])
                if d > SURVEYED_MAX_SPACING_M:
                    gap += d
                else:
                    surveyed += d
        if self.closed and self.chains:
            # The closure is a span like any other: surveyed when short,
            # a gap when the stitch had to bridge it.
            closure = self.closure_len()
            if closure > SURVEYED_MAX_SPACING_M:
                gap += closure
            else:
                surveyed += closure
        total = surveyed + gap
        return {
            "surveyed_m": round(surveyed, 1),
            "gap_m": round(gap, 1),
            "pct": round(100.0 * surveyed / total, 1) if total > 0 else 0.0,
            "closed": self.closed,
        }

    def polylines(self) -> list[list[list[float | None]]]:
        """Drawable border polylines: chains split at gap spans, simplified.

        Vertices are [x, z, y]; y is null where the metre predates elevation
        capture.
        """
        out: list[list[list[float | None]]] = []
        for ci, chain in enumerate(self.chains):
            run: list[list[float | None]] = []
            for k, i in enumerate(chain):
                p = self.pts[i]
                if run and _seg_len(self.pts[chain[k - 1]], p) > SURVEYED_MAX_SPACING_M:
                    if len(run) >= 2:
                        out.append(_simplify(run, SIMPLIFY_TOL_M))
                    run = []
                y = p.get("y")
                run.append([round(p["x"], 2), round(p["z"], 2),
                            round(y, 2) if y is not None else None])
            # A surveyed seam draws: the loop visibly closes instead of
            # stopping a stride short of where it started. The seam joins the
            # chain's END to its START — which, when gaps split the chain,
            # is the first fragment's head, not this last fragment's.
            if ci == 0 and self.closure_surveyed() and run:
                first = self.pts[chain[0]]
                fy = first.get("y")
                run.append([round(first["x"], 2), round(first["z"], 2),
                            round(fy, 2) if fy is not None else None])
            if len(run) >= 2:
                out.append(_simplify(run, SIMPLIFY_TOL_M))
        return out

    def gap_spans(self) -> list[list[float]]:
        """Flagged gaps as [x1, z1, x2, z2], the closure gap included."""
        out: list[list[float]] = []
        for chain in self.chains:
            for a, b in zip(chain, chain[1:], strict=False):
                pa, pb = self.pts[a], self.pts[b]
                if _seg_len(pa, pb) > SURVEYED_MAX_SPACING_M:
                    out.append([round(pa["x"], 1), round(pa["z"], 1),
                                round(pb["x"], 1), round(pb["z"], 1)])
        if self.closed and self.chains:
            main = self.chains[0]
            pa, pb = self.pts[main[-1]], self.pts[main[0]]
            if _seg_len(pa, pb) > SURVEYED_MAX_SPACING_M:
                out.append([round(pa["x"], 1), round(pa["z"], 1),
                            round(pb["x"], 1), round(pb["z"], 1)])
        return out


def _resample(
    assembly: SideAssembly, step: float
) -> list[dict[str, Any] | None]:
    """Samples every `step` metres along surveyed (non-gap) spans of every
    chain, each with position, tangent and interpolated elevation.

    A `None` marks a break — a flagged gap or a jump to another chain — so
    consumers never treat samples on opposite sides of unsurveyed ground as
    neighbours (two samples can sit under 2×step apart across a 6-8 m gap).
    On a closed loop whose seam is real border, sampling continues across
    the seam instead, so the loop has no artificial break at the chain seed.
    """
    out: list[dict[str, Any] | None] = []
    pts = assembly.pts
    for ci, chain in enumerate(assembly.chains):
        carry = 0.0
        pairs = list(zip(chain, chain[1:], strict=False))
        if ci == 0 and assembly.closure_surveyed() and len(chain) >= 2:
            pairs.append((chain[-1], chain[0]))
        for a, b in pairs:
            pa, pb = pts[a], pts[b]
            d = _seg_len(pa, pb)
            if d > SURVEYED_MAX_SPACING_M:
                carry = 0.0  # gap: restart sampling on the far side
                if out and out[-1] is not None:
                    out.append(None)
                continue
            if d < 1e-9:
                continue
            tx, tz = (pb["x"] - pa["x"]) / d, (pb["z"] - pa["z"]) / d
            pos = carry
            while pos < d:
                f = pos / d
                ya, yb = pa.get("y"), pb.get("y")
                y = ya + (yb - ya) * f if ya is not None and yb is not None else (
                    ya if ya is not None else yb
                )
                out.append({
                    "x": pa["x"] + (pb["x"] - pa["x"]) * f,
                    "z": pa["z"] + (pb["z"] - pa["z"]) * f,
                    "y": y,
                    "tx": tx, "tz": tz,
                })
                pos += step
            carry = pos - d
        if out and out[-1] is not None:
            out.append(None)  # chain boundary: the next chain is elsewhere
    return out


class _SegmentIndex:
    """Spatial hash of one side's surveyed polyline segments, for
    nearest-point-on-curve queries."""

    def __init__(self, assembly: SideAssembly) -> None:
        pts = assembly.pts
        self.segs: list[tuple[dict[str, Any], dict[str, Any]]] = []
        self.map: dict[tuple[int, int], list[int]] = {}
        for ci, chain in enumerate(assembly.chains):
            pairs = list(zip(chain, chain[1:], strict=False))
            # A surveyed loop seam is a segment like any other; without it the
            # opposite side cannot pair across this chain's seed point.
            if ci == 0 and assembly.closure_surveyed() and len(chain) >= 2:
                pairs.append((chain[-1], chain[0]))
            for a, b in pairs:
                pa, pb = pts[a], pts[b]
                if _seg_len(pa, pb) > SURVEYED_MAX_SPACING_M:
                    continue
                idx = len(self.segs)
                self.segs.append((pa, pb))
                mx = (pa["x"] + pb["x"]) / 2
                mz = (pa["z"] + pb["z"]) / 2
                key = (math.floor(mx / _GRID_CELL_M), math.floor(mz / _GRID_CELL_M))
                self.map.setdefault(key, []).append(idx)

    def nearest(
        self, x: float, z: float, y: float | None = None
    ) -> list[tuple[float, dict[str, Any]]]:
        """(distance, interpolated point) of the closest segment points, in
        the order the caller should try them: first the closest on the
        query's own road level — when its elevation is known and any segment
        shares it — then the closest regardless of level (#96). Under a
        crossover the other level's border can be the nearest thing in plan,
        and the road across from a sample is the one at its own height; on a
        banked road the far border may sit further up than the level rule
        allows, and the second candidate is what still finds it.
        """
        cx, cz = math.floor(x / _GRID_CELL_M), math.floor(z / _GRID_CELL_M)
        best: tuple[float, dict[str, Any]] | None = None
        level: tuple[float, dict[str, Any]] | None = None
        best_d = level_d = math.inf
        for gx in (cx - 1, cx, cx + 1):
            for gz in (cz - 1, cz, cz + 1):
                for idx in self.map.get((gx, gz), ()):
                    pa, pb = self.segs[idx]
                    dx, dz = pb["x"] - pa["x"], pb["z"] - pa["z"]
                    seg2 = dx * dx + dz * dz
                    t = 0.0 if seg2 < 1e-12 else max(
                        0.0, min(1.0, ((x - pa["x"]) * dx + (z - pa["z"]) * dz) / seg2)
                    )
                    px, pz = pa["x"] + dx * t, pa["z"] + dz * t
                    d = math.hypot(px - x, pz - z)
                    if d >= best_d and d >= level_d:
                        continue
                    ya, yb = pa.get("y"), pb.get("y")
                    py = ya + (yb - ya) * t if ya is not None and yb is not None else (
                        ya if ya is not None else yb
                    )
                    hit = (d, {"x": px, "z": pz, "y": py})
                    if d < best_d:
                        best, best_d = hit, d
                    if (
                        d < level_d and y is not None and py is not None
                        and abs(py - y) <= track_bundle.LEVEL_SEP_M
                    ):
                        level, level_d = hit, d
        out = [level] if level is not None else []
        if best is not None and best is not level:
            out.append(best)
        return out


def centerline_and_road(
    left: SideAssembly, right: SideAssembly
) -> tuple[
    list[list[list[float | None]]], list[list[float]], list[list[float] | None], float
]:
    """The centerline and the road surface between the two border curves.

    Left border samples are paired ACROSS to the nearest point on the right
    border curve — point-to-curve, so the two sides' cells do not need to sit
    opposite each other, only to both exist on that stretch. Where the right
    curve is missing (or implausibly far/near, or not actually across the
    road) the centerline simply breaks: unpaired stretches produce nothing.

    Returns (centerline_runs, road_quads, road_levels, paired_ratio).
    Centerline runs are polylines of [x, z, y, w] — w the measured road width
    there; y null when neither border knows its elevation. Quads are
    [8 floats], the same drawing shape track_outline emits, spanning border
    to border; road_levels holds, per quad, [lowest, highest] corner
    elevation, or None where a corner has no elevation (#96) — banking puts
    the two borders at different heights, so a quad's level is an envelope,
    not a number. The ratio is the share of left-border samples that found
    the road across from them.
    """
    samples = _resample(left, CL_STEP_M)
    index = _SegmentIndex(right)
    runs: list[list[list[float | None]]] = []
    quads: list[list[float]] = []
    levels: list[list[float] | None] = []
    run: list[list[float | None]] = []
    prev: dict[str, Any] | None = None
    paired = 0
    judged = 0

    for s in samples:
        if s is None:
            # A resample break: unsurveyed ground between this sample and the
            # last. Two samples can sit under 2×step apart across a small
            # flagged gap, so proximity alone must not bridge it with a quad.
            if len(run) >= 2:
                runs.append(run)
            run = []
            prev = None
            continue
        judged += 1
        # The road lies to the LEFT border's right-normal.
        rnx, rnz = s["tz"], -s["tx"]
        pair = None
        for d, p in index.nearest(s["x"], s["z"], s["y"]):
            if ROAD_WIDTH_MIN_M <= d <= ROAD_WIDTH_MAX_M:
                ux, uz = _norm(p["x"] - s["x"], p["z"] - s["z"])
                if abs(ux * rnx + uz * rnz) >= ACROSS_MIN_DOT:
                    pair = (d, p)
                    break
        if pair is None:
            if len(run) >= 2:
                runs.append(run)
            run = []
            prev = None
            continue
        d, p = pair
        paired += 1
        cy_parts = [v for v in (s["y"], p["y"]) if v is not None]
        cy = sum(cy_parts) / len(cy_parts) if cy_parts else None
        run.append([
            round((s["x"] + p["x"]) / 2, 2), round((s["z"] + p["z"]) / 2, 2),
            round(cy, 2) if cy is not None else None, round(d, 1),
        ])
        if prev is not None and math.hypot(
            s["x"] - prev["sx"], s["z"] - prev["sz"]
        ) <= 2 * CL_STEP_M:
            quads.append([
                round(prev["sx"], 1), round(prev["sz"], 1),
                round(s["x"], 1), round(s["z"], 1),
                round(p["x"], 1), round(p["z"], 1),
                round(prev["px"], 1), round(prev["pz"], 1),
            ])
            corners = [prev["sy"], s["y"], p["y"], prev["py"]]
            levels.append(
                [round(min(corners), 2), round(max(corners), 2)]
                if all(v is not None for v in corners) else None
            )
        prev = {"sx": s["x"], "sz": s["z"], "px": p["x"], "pz": p["z"],
                "sy": s["y"], "py": p["y"]}
    if len(run) >= 2:
        runs.append(run)
    return runs, quads, levels, (paired / judged if judged else 0.0)


def compile_bundle(doc: dict[str, Any], *, smooth: bool | None = None) -> dict[str, Any]:
    """One bundle document -> its compiled vector geometry.

    `smooth` is whether the borders are smoothed (see SMOOTH_BORDERS, which is
    what None means). It is an argument and not only a constant because the
    sync service's administrator can switch it off for the shared map, and
    the merge job passes their answer here; the document says which it got.
    """
    from app.processing import track_outline  # finish_line; avoid cycle at import

    smooth = SMOOTH_BORDERS if smooth is None else bool(smooth)

    edges = doc["edges"]
    # Every kind is a border record — "wall" or "runoff" says what lies BEYOND
    # the edge, not that the edge isn't one (#49) — so every kind takes part
    # in the ordering. Walls are additionally kept as their own drawing layer,
    # exactly as track_outline separates them.
    left = SideAssembly([e for e in edges if e["side"] == "L"], smooth=smooth)
    right = SideAssembly([e for e in edges if e["side"] == "R"], smooth=smooth)
    centerline, road, road_y, paired = centerline_and_road(left, right)
    coverage = {
        "L": left.coverage(),
        "R": right.coverage(),
        # How much of the road surface itself is resolved: the share of
        # surveyed left-border metres with the right border found across from
        # them. (Not a ratio of curve lengths — on a curve the centerline is
        # intrinsically shorter than the outer border, which would under-read
        # a perfectly surveyed circuit.)
        "road_pct": round(100.0 * paired, 1),
    }

    meta = doc["meta"]
    return {
        "format": COMPILED_FORMAT,
        "version": COMPILED_VERSION,
        "track": meta["track"],
        "slug": track_bundle.slugify(meta["track"]),
        "compiled_at": datetime.now(UTC).isoformat(),
        # Provenance (#40): what evidence this geometry was compiled from.
        "source": {
            "points": len(edges),
            "runs": meta["runs"],
            "sources": len(meta["source_runs"]),
            "bundle_updated_at": meta["updated_at"],
            "app_version": _app_version(),
        },
        "borders": {"L": left.polylines(), "R": right.polylines()},
        "gaps": {"L": left.gap_spans(), "R": right.gap_spans()},
        "walls": [
            _wall_tick(e) for e in edges if e["kind"] == "wall"
        ],
        "centerline": centerline,
        "road": road,
        "road_y": road_y,
        "finish": track_outline.finish_line(doc["finish_crossings"]),
        "coverage": coverage,
        # What was done to the recorded positions before any of the above was
        # derived from them, or None for the evidence exactly as surveyed.
        "smoothing": {
            "method": "taubin",
            "iterations": SMOOTH_ITERATIONS,
            "lambda": SMOOTH_LAMBDA,
            "mu": SMOOTH_MU,
            "cap_m": SMOOTH_CAP_M,
        } if smooth else None,
    }


def _wall_tick(e: dict[str, Any]) -> list[float]:
    hx, hz = e["hx"] * 0.9, e["hz"] * 0.9
    return [round(e["x"] - hx, 1), round(e["z"] - hz, 1),
            round(e["x"] + hx, 1), round(e["z"] + hz, 1)]


_VERSION: str | None = None


def _app_version() -> str:
    global _VERSION
    if _VERSION is None:
        try:
            import tomllib

            pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
            with open(pyproject, "rb") as fh:
                _VERSION = str(tomllib.load(fh)["project"]["version"])
        except (OSError, KeyError, ValueError):
            _VERSION = "unknown"
    return _VERSION


def _compiled_path(data_dir: Path, slug: str) -> Path:
    return data_dir / track_bundle.BUNDLE_DIR / COMPILED_DIR / f"{slug}.json"


def for_track(data_dir: Path, track: str) -> dict[str, Any] | None:
    """The compiled geometry for a circuit, recompiled when its bundle changed.

    Auto-recompile (#40) is by bundle file identity: any write to the bundle —
    a survey save, an import, a merge — makes the stored compile stale, and
    the next consumer rebuilds and re-persists it. Returns None when the
    circuit has no bundle. Blocking on a stale compile (parses the bundle and
    chains it, tens of ms on a full survey): callers run it off the event
    loop, as they do for track_outline.
    """
    if not track:
        return None
    slug = track_bundle.slugify(track)
    bundle = data_dir / track_bundle.BUNDLE_DIR / f"{slug}.json"
    try:
        stat = bundle.stat()
    except OSError:
        return None
    key = (str(bundle), stat.st_mtime_ns, stat.st_size)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached

    path = _compiled_path(data_dir, slug)
    compiled: dict[str, Any] | None = None
    try:
        on_disk = json.loads(path.read_text(encoding="utf-8"))
        meta_ok = (
            on_disk.get("format") == COMPILED_FORMAT
            and on_disk.get("version") == COMPILED_VERSION
            and on_disk.get("_bundle_identity") == [stat.st_mtime_ns, stat.st_size]
        )
        if meta_ok:
            compiled = on_disk
    except (OSError, ValueError):
        pass

    if compiled is None:
        doc = track_bundle.load_slug(data_dir, slug)
        if doc is None:
            return None
        compiled = compile_bundle(doc)
        compiled["_bundle_identity"] = [stat.st_mtime_ns, stat.st_size]
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(compiled, separators=(",", ":")), encoding="utf-8")
            tmp.replace(path)
        except OSError as exc:  # a read-only data dir still gets geometry
            log.warning("could not persist compiled track %s: %s", slug, exc)
        log.info(
            "compiled track %r: %d border polylines, %d centerline runs, "
            "%d road quads, coverage L %.0f%% R %.0f%%",
            compiled["track"],
            len(compiled["borders"]["L"]) + len(compiled["borders"]["R"]),
            len(compiled["centerline"]), len(compiled["road"]),
            compiled["coverage"]["L"]["pct"], compiled["coverage"]["R"]["pct"],
        )

    if len(_CACHE) >= _CACHE_MAX:
        _CACHE.clear()
    _CACHE[key] = compiled
    return compiled
