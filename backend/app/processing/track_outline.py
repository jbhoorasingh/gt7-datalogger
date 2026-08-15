"""The surveyed road, compiled into something a lap can be drawn on (#51).

A racing line only means something against the road it was driven on: whether
the apex was clipped, how much kerb was used, whether there was tarmac left on
the exit. The survey bundles hold that road — but as up to 50,000 voted border
cells, which is several megabytes, and the Analysis view must not download a
circuit's whole survey history to draw a map behind one lap.

So the road is compiled here instead: border cells are paired across the
carriageway into quads, borders are reduced to short direction-carrying
segments, and the finish line is resolved from the recorded crossings. What
comes out is a few hundred kilobytes of geometry with no votes, no provenance
and no elevation — everything the map draws and nothing it doesn't.

The pairing rule is the Survey view's, moved server-side (it lives in
`frontend/src/lib/surveyGeometry.ts` as `roadQuads`, where it runs on a single
run's evidence as it accumulates). It is deliberately LOCAL: a left border
point pairs with the right border point directly across from it, judged by the
travel direction each was recorded under. Nothing here reconstructs a
centerline or an ordering, which is what lets a partially surveyed circuit
draw the parts it knows and leave the rest blank rather than drawing a
confident wrong loop through them.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Any

from app.processing import track_bundle

log = logging.getLogger(__name__)

# Cross-section plausibility, mirroring frontend/src/lib/surveyGeometry.ts.
ROAD_WIDTH_MIN_M = 3.0
ROAD_WIDTH_MAX_M = 40.0
ACROSS_MIN_DOT = 0.85  # L->R direction vs the left point's right-normal (~32°)
HEADING_MIN_DOT = 0.7  # both contacts recorded travelling the same way
QUAD_HALF_LENGTH_M = 2.5  # along-track extent of one filled span
CELL_M = ROAD_WIDTH_MAX_M  # spatial-hash cell = the whole search radius

# Half-length of the drawn start/finish line, and of a border tick.
FINISH_HALF_M = 12.0
TICK_HALF_M = 0.9

# Compiled outlines, keyed by bundle file identity (path + mtime + size) so a
# re-survey or an import invalidates it without anyone having to remember to.
# Small: the cost being avoided is parsing a multi-megabyte document and
# pairing thousands of cells, per Analysis page load.
_CACHE: dict[tuple[str, int, int], dict[str, Any]] = {}
_CACHE_MAX = 8


def _round_pairs(values: list[float]) -> list[float]:
    return [round(v, 1) for v in values]


def road_quads(edges: list[dict[str, Any]]) -> list[list[float]]:
    """Filled road spans wherever a left border cell faces a right one.

    Every kind contributes, "runoff" included: those marks are track edges
    that happen to have pavement beyond them, and excluding them drew no road
    at all through the corners someone took the trouble to survey.
    """
    rights: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for e in edges:
        if e["side"] != "R":
            continue
        key = (math.floor(e["x"] / CELL_M), math.floor(e["z"] / CELL_M))
        rights.setdefault(key, []).append(e)

    quads: list[list[float]] = []
    for left in edges:
        if left["side"] != "L":
            continue
        # Right-normal of the left point's heading: the direction the road
        # should lie in from here.
        rnx, rnz = left["hz"], -left["hx"]
        best: dict[str, Any] | None = None
        best_dist = ROAD_WIDTH_MAX_M
        cx = math.floor(left["x"] / CELL_M)
        cz = math.floor(left["z"] / CELL_M)
        for gx in (cx - 1, cx, cx + 1):
            for gz in (cz - 1, cz, cz + 1):
                for right in rights.get((gx, gz), ()):
                    dx = right["x"] - left["x"]
                    dz = right["z"] - left["z"]
                    dist = math.hypot(dx, dz)
                    if dist < ROAD_WIDTH_MIN_M or dist >= best_dist:
                        continue
                    if (dx * rnx + dz * rnz) / dist < ACROSS_MIN_DOT:
                        continue
                    if left["hx"] * right["hx"] + left["hz"] * right["hz"] < HEADING_MIN_DOT:
                        continue
                    best, best_dist = right, dist
        if best is None:
            continue
        ax = left["hx"] + best["hx"]
        az = left["hz"] + best["hz"]
        alen = math.hypot(ax, az) or 1.0
        ax = ax / alen * QUAD_HALF_LENGTH_M
        az = az / alen * QUAD_HALF_LENGTH_M
        quads.append(_round_pairs([
            left["x"] - ax, left["z"] - az,
            left["x"] + ax, left["z"] + az,
            best["x"] + ax, best["z"] + az,
            best["x"] - ax, best["z"] - az,
        ]))
    return quads


def _tick(e: dict[str, Any]) -> list[float]:
    """One border cell as a short segment [x1, z1, x2, z2] along travel."""
    hx = e["hx"] * TICK_HALF_M
    hz = e["hz"] * TICK_HALF_M
    return _round_pairs([e["x"] - hx, e["z"] - hz, e["x"] + hx, e["z"] + hz])


def finish_line(crossings: list[dict[str, float]]) -> list[float] | None:
    """The start/finish line across the road, from recorded lap rollovers.

    Averaged over every crossing the bundle holds: GT7 increments the lap
    counter exactly on the line, but the car crosses it at a different point
    of the road each lap, so one crossing places the line and several agree on
    where the middle of it is.
    """
    if not crossings:
        return None
    n = len(crossings)
    x = sum(c["x"] for c in crossings) / n
    z = sum(c["z"] for c in crossings) / n
    hx = sum(c["hx"] for c in crossings) / n
    hz = sum(c["hz"] for c in crossings) / n
    length = math.hypot(hx, hz)
    if length < 1e-6:
        return None
    # The line lies across the direction of travel.
    rx = hz / length * FINISH_HALF_M
    rz = -hx / length * FINISH_HALF_M
    return _round_pairs([x - rx, z - rz, x + rx, z + rz])


def build(doc: dict[str, Any]) -> dict[str, Any]:
    """Compile one bundle document into the map's drawing geometry."""
    edges = doc["edges"]
    walls = [e for e in edges if e["kind"] == "wall"]
    borders = [e for e in edges if e["kind"] != "wall"]
    return {
        "track": doc["meta"]["track"],
        "slug": track_bundle.slugify(doc["meta"]["track"]),
        "road": road_quads(edges),
        # Walls are drawn apart from the rest: hitting one ends a lap, and a
        # line that ran wide over a run-off limit reads very differently from
        # one that ran wide into a barrier.
        "edges": [_tick(e) for e in borders],
        "walls": [_tick(e) for e in walls],
        "finish": finish_line(doc["finish_crossings"]),
        "runs": doc["meta"]["runs"],
        "updated_at": doc["meta"]["updated_at"],
    }


EMPTY: dict[str, Any] = {
    "track": "",
    "slug": None,
    "road": [],
    "edges": [],
    "walls": [],
    "finish": None,
    "runs": 0,
    "updated_at": "",
    # Filled by the compiled pathway (#44); the same shape either way, so the
    # map never has to ask which compiler answered.
    "gaps": [],
    "coverage": None,
}


def for_track(data_dir: Path, track: str) -> dict[str, Any]:
    """A circuit's compiled outline, or EMPTY when it has never been surveyed.

    Blocking (parses the bundle on a cache miss): callers run it off the event
    loop.
    """
    if not track:
        return EMPTY
    path = track_bundle.bundle_path(data_dir, track)
    try:
        stat = path.stat()
    except OSError:
        return EMPTY
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    doc = track_bundle.load(data_dir, track)
    if doc is None:
        return EMPTY
    outline = build(doc)
    if len(_CACHE) >= _CACHE_MAX:
        _CACHE.clear()  # a handful of circuits; no need for true LRU accounting
    _CACHE[key] = outline
    log.info(
        "compiled track outline for %r: %d road spans, %d border ticks",
        outline["track"], len(outline["road"]), len(outline["edges"]) + len(outline["walls"]),
    )
    return outline
