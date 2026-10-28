"""Sector boundaries and sector times (#22).

GT7 shows three sector splits on screen and broadcasts none of them, so the
app has to know where a circuit's sectors begin and end. A boundary is a
POSITION on the circuit, authored in the track bundle beside the corners
(`sectors[i].end`: where sector i + 1 ends), and resolved per lap to that
lap's own distance axis by finding where the lap passed nearest to it — the
same rule as a corner's apex, and for the same reason: a distance belongs to
the line one lap took, a place on the track does not.

A circuit with no authored sectors falls back to thirds of the lap, which is
what the S1/S2/S3 buttons always meant. Those boundaries are taken from one
reference lap as positions too, so that every lap of a session is split at
the same two places on the road rather than at a third of its own, slightly
different, length.

Pure functions over sample dicts; nothing here touches the database.
"""

from __future__ import annotations

from typing import Any, TypedDict

from app.processing.analysis import (
    CORNER_ANCHOR_MAX_M,
    Samples,
    _interp,
    _lap_path,
    _nearest_index,
)
from app.processing.laps import SALVAGE_TOLERANCE_MS, SALVAGE_TOLERANCE_RATIO

# Thirds: the fallback when the circuit has no authored sectors.
DEFAULT_SECTOR_COUNT = 3


class Boundary(TypedDict):
    """Where one sector ends, as a place on the circuit."""

    n: int
    name: str
    x: float
    z: float


def boundary_points(
    authored: list[dict[str, Any]], reference: Samples | None
) -> tuple[list[Boundary], bool]:
    """The circuit's sector boundaries as positions, and whether they are the
    circuit's own (`True`) or thirds of the reference lap (`False`).

    Authored boundaries come straight from the bundle. Without any, the
    reference lap supplies the fallback: the points a third and two thirds of
    the way round IT, so every lap is then split at the same two places.
    Empty when there is nothing to split by — no authored sectors and no
    reference lap to take thirds from.
    """
    points: list[Boundary] = []
    for index, raw in enumerate(authored):
        end = raw.get("end") if isinstance(raw, dict) else None
        try:
            x, z = float(end["x"]), float(end["z"])  # type: ignore[index]
        except (KeyError, TypeError, ValueError):
            continue
        points.append({
            "n": int(raw.get("n", index + 1)),
            "name": str(raw.get("name") or ""),
            "x": x,
            "z": z,
        })
    if points:
        return points, True
    if reference is None:
        return [], False
    d, xs, zs, _ = _lap_path(reference)
    if len(d) < 8 or d[-1] <= 0:
        return [], False
    length = d[-1]
    for k in range(1, DEFAULT_SECTOR_COUNT):
        at = length * k / DEFAULT_SECTOR_COUNT
        points.append({
            "n": k,
            "name": "",
            "x": round(_interp(d, xs, at), 1),
            "z": round(_interp(d, zs, at), 1),
        })
    return points, False


def project(samples: Samples, points: list[Boundary]) -> list[float] | None:
    """Each boundary's distance along THIS lap, in order — or None when the
    lap did not pass every boundary in that order, which is a lap that cut
    the track or drove a different layout, and has no sector times."""
    d, xs, zs, _ = _lap_path(samples)
    if len(d) < 8:
        return None
    out: list[float] = []
    for point in points:
        i, gap = _nearest_index(xs, zs, point["x"], point["z"])
        if gap > CORNER_ANCHOR_MAX_M:
            return None
        out.append(d[i])
    if any(b <= a for a, b in zip(out, out[1:], strict=False)):
        return None
    return out


def sectors_for_lap(
    samples: Samples, points: list[Boundary], authored: bool
) -> list[dict[str, Any]]:
    """The sectors as distance windows on one lap, for the Analysis view's lap
    strip: sector 1 from the line to the first boundary, the last from the
    last boundary to the line. Empty when the lap cannot be split."""
    if not points:
        return []
    bounds = project(samples, points)
    if bounds is None:
        return []
    d, _, _, _ = _lap_path(samples)
    edges = [0.0, *bounds, d[-1]]
    out: list[dict[str, Any]] = []
    for k in range(len(edges) - 1):
        out.append({
            "n": k + 1,
            "name": points[k]["name"] if k < len(points) else "",
            "start_dist": round(edges[k], 1),
            "end_dist": round(edges[k + 1], 1),
            "authored": authored,
        })
    return out


def splits_ms(
    samples: Samples, points: list[Boundary], time_ms: float
) -> list[float] | None:
    """The lap's time through each sector, in milliseconds, summing to the
    lap time. The clock at each boundary is read off the lap's own `t`
    channel at the boundary's distance; the last sector takes whatever is
    left of the lap time, so the splits add up to the figure GT7 showed.
    None when the lap has no sector times: no clock, no lap time, a clock
    that does not account for the lap time, or a boundary it never passed."""
    if not points or time_ms <= 0:
        return None
    bounds = project(samples, points)
    if bounds is None:
        return None
    dist = samples.get("dist") or []
    clock = samples.get("t") or []
    n = min(len(dist), len(clock))
    if n < 2:
        return None
    d, t = list(dist[:n]), list(clock[:n])
    # The splits share GT7's lap time out by the lap's own clock, so that
    # clock has to have run the whole lap. One that started where a
    # recording joined part way round, or froze through a stop the sampler
    # skipped, falls short of the lap time by what it missed, and every
    # boundary after the gap would read early by that much: the sector it
    # fell in short, the last sector long. Agreeing within the window a
    # salvaged lap has to meet to be believed (laps.SALVAGE_TOLERANCE_MS)
    # is the clock describing this lap.
    tolerance = max(SALVAGE_TOLERANCE_MS, time_ms * SALVAGE_TOLERANCE_RATIO)
    if abs(time_ms - t[-1] * 1000) > tolerance:
        return None
    # The stored clock is anchored on the line: t = 0 is the crossing and
    # the first sample sits half a frame past it (lap-file-format.md). Read
    # as it stands — measured from the first sample instead, S1 would lose
    # that half-frame to the last sector on every lap.
    marks = [_interp(d, t, b) * 1000 for b in bounds]
    if any(m <= 0 or m >= time_ms for m in marks):
        return None
    edges = [0.0, *marks, float(time_ms)]
    return [round(b - a, 1) for a, b in zip(edges, edges[1:], strict=False)]
