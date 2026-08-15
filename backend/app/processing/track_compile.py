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
  - the road surface as a quad strip between consecutive paired samples
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
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.processing import track_bundle

log = logging.getLogger(__name__)

COMPILED_FORMAT = "gt7-datalogger-track-compiled"
COMPILED_VERSION = 1
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

_CACHE: dict[tuple[str, int, int], dict[str, Any]] = {}
_CACHE_MAX = 8


def _norm(x: float, z: float) -> tuple[float, float]:
    d = math.hypot(x, z) or 1.0
    return x / d, z / d


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

    def near(self, x: float, z: float, radius: float):
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


def _endpoint_dir(chain: list[int], pts: list[dict[str, Any]], at_start: bool):
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
                    if d > GAP_JOIN_M:
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


def _simplify(vertices: list[list[float | None]], tol: float) -> list[list[float | None]]:
    """Douglas-Peucker on [x, z, y] vertices (y ignored for the metric)."""
    if len(vertices) < 3:
        return vertices
    ax, az = vertices[0][0], vertices[0][1]
    bx, bz = vertices[-1][0], vertices[-1][1]
    dx, dz = bx - ax, bz - az
    seg = math.hypot(dx, dz)
    worst, worst_d = 0, -1.0
    for i in range(1, len(vertices) - 1):
        px, pz = vertices[i][0], vertices[i][1]
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


class SideAssembly:
    """One side's border, ordered: the main chain plus leftover fragments."""

    def __init__(self, pts: list[dict[str, Any]]) -> None:
        self.pts = pts
        self.chains = stitch(chain_side(pts), pts) if pts else []
        self.chains.sort(key=lambda c: self._length(c), reverse=True)
        self.closed: bool = False
        if self.chains:
            main = self.chains[0]
            a, b = pts[main[0]], pts[main[-1]]
            gap = _seg_len(a, b)
            if gap <= GAP_JOIN_M and len(main) >= 20:
                da = _endpoint_dir(main, pts, at_start=False)
                db = _endpoint_dir(main, pts, at_start=True)
                if da is not None and db is not None and -(
                    da[0] * db[0] + da[1] * db[1]
                ) >= 0.2:
                    self.closed = True

    def _length(self, chain: list[int]) -> float:
        return sum(
            _seg_len(self.pts[a], self.pts[b]) for a, b in zip(chain, chain[1:], strict=False)
        )

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
            main = self.chains[0]
            closure = _seg_len(self.pts[main[0]], self.pts[main[-1]])
            if closure > SURVEYED_MAX_SPACING_M:
                gap += closure
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
        for chain in self.chains:
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
) -> list[dict[str, Any]]:
    """Samples every `step` metres along surveyed (non-gap) spans of every
    chain, each with position, tangent and interpolated elevation."""
    out: list[dict[str, Any]] = []
    pts = assembly.pts
    for chain in assembly.chains:
        carry = 0.0
        for a, b in zip(chain, chain[1:], strict=False):
            pa, pb = pts[a], pts[b]
            d = _seg_len(pa, pb)
            if d > SURVEYED_MAX_SPACING_M:
                carry = 0.0  # gap: restart sampling on the far side
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
    return out


class _SegmentIndex:
    """Spatial hash of one side's surveyed polyline segments, for
    nearest-point-on-curve queries."""

    def __init__(self, assembly: SideAssembly) -> None:
        pts = assembly.pts
        self.segs: list[tuple[dict[str, Any], dict[str, Any]]] = []
        self.map: dict[tuple[int, int], list[int]] = {}
        for chain in assembly.chains:
            for a, b in zip(chain, chain[1:], strict=False):
                pa, pb = pts[a], pts[b]
                if _seg_len(pa, pb) > SURVEYED_MAX_SPACING_M:
                    continue
                idx = len(self.segs)
                self.segs.append((pa, pb))
                mx = (pa["x"] + pb["x"]) / 2
                mz = (pa["z"] + pb["z"]) / 2
                key = (math.floor(mx / _GRID_CELL_M), math.floor(mz / _GRID_CELL_M))
                self.map.setdefault(key, []).append(idx)

    def nearest(self, x: float, z: float) -> tuple[float, dict[str, Any]] | None:
        """(distance, interpolated point) of the closest segment point."""
        cx, cz = math.floor(x / _GRID_CELL_M), math.floor(z / _GRID_CELL_M)
        best: tuple[float, dict[str, Any]] | None = None
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
                    if best is None or d < best[0]:
                        ya, yb = pa.get("y"), pb.get("y")
                        y = ya + (yb - ya) * t if ya is not None and yb is not None else (
                            ya if ya is not None else yb
                        )
                        best = (d, {"x": px, "z": pz, "y": y})
        return best


def centerline_and_road(
    left: SideAssembly, right: SideAssembly
) -> tuple[list[list[list[float | None]]], list[list[float]]]:
    """The centerline and the road surface between the two border curves.

    Left border samples are paired ACROSS to the nearest point on the right
    border curve — point-to-curve, so the two sides' cells do not need to sit
    opposite each other, only to both exist on that stretch. Where the right
    curve is missing (or implausibly far/near, or not actually across the
    road) the centerline simply breaks: unpaired stretches produce nothing.

    Returns (centerline_runs, road_quads, paired_ratio). Centerline runs are
    polylines of [x, z, y, w] — w the measured road width there; y null when
    neither border knows its elevation. Quads are [8 floats], the same
    drawing shape track_outline emits, spanning border to border. The ratio
    is the share of left-border samples that found the road across from them.
    """
    samples = _resample(left, CL_STEP_M)
    index = _SegmentIndex(right)
    runs: list[list[list[float | None]]] = []
    quads: list[list[float]] = []
    run: list[list[float | None]] = []
    prev: dict[str, Any] | None = None
    paired = 0

    for s in samples:
        # The road lies to the LEFT border's right-normal.
        rnx, rnz = s["tz"], -s["tx"]
        hit = index.nearest(s["x"], s["z"])
        pair = None
        if hit is not None:
            d, p = hit
            if ROAD_WIDTH_MIN_M <= d <= ROAD_WIDTH_MAX_M:
                ux, uz = _norm(p["x"] - s["x"], p["z"] - s["z"])
                if abs(ux * rnx + uz * rnz) >= ACROSS_MIN_DOT:
                    pair = (d, p)
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
        prev = {"sx": s["x"], "sz": s["z"], "px": p["x"], "pz": p["z"]}
    if len(run) >= 2:
        runs.append(run)
    return runs, quads, (paired / len(samples) if samples else 0.0)


def compile_bundle(doc: dict[str, Any]) -> dict[str, Any]:
    """One bundle document -> its compiled vector geometry."""
    from app.processing import track_outline  # finish_line; avoid cycle at import

    edges = doc["edges"]
    # Every kind is a border record — "wall" or "runoff" says what lies BEYOND
    # the edge, not that the edge isn't one (#49) — so every kind takes part
    # in the ordering. Walls are additionally kept as their own drawing layer,
    # exactly as track_outline separates them.
    left = SideAssembly([e for e in edges if e["side"] == "L"])
    right = SideAssembly([e for e in edges if e["side"] == "R"])
    centerline, road, paired = centerline_and_road(left, right)
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
        "finish": track_outline.finish_line(doc["finish_crossings"]),
        "coverage": coverage,
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
