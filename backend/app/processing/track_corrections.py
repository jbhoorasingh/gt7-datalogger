"""Corrections to a circuit's survey: what a person decided the evidence gets wrong.

A bundle is evidence, and evidence only ever adds up: a merge folds a new
survey into the old one cell by cell and removes nothing. That is the right
rule for laps and the wrong one for mistakes. A pit wall recorded as the
right-hand border is in the file for good — and deleting it from the file
does not help, because every installation that ever surveyed the circuit
still holds the metre and the next merge puts it straight back.

So a correction is never made to the evidence. The shared repo keeps it
beside the bundle, in `corrections/<slug>.json`, and applies it to what is
*derived* from the evidence: the geometry its merge job compiles and the
sync service publishes. This module is the app's reading of that file, so
the road the app draws and judges laps against is the road the website
shows rather than the one with the pit wall still on it.

Two things it can say, and one it can add:

  * **`exclude`** — areas of ground, each a polygon in world metres with the
    sides it applies to, inside which border records are not compiled. An
    area and not a list of cells, because a survey jitters: next week's pit
    wall lands in the cells next door. `y` bounds an area to one road level
    where a circuit crosses over itself; a record with no elevation is on
    every level. `only_drawn` limits an area to records nobody drove — how
    a drawn bridge is taken back without hiding the real border once
    somebody surveys it.
  * **`compile.smooth_borders`** — this circuit's own answer to whether its
    borders are smoothed. `null` follows the compiler's default.
  * **`draw`** — border records somebody DREW: a bridge across a gap, a
    kerb nobody drove. The same shape as a bundle's records, every vote
    under a `drawn-` source, compiled with the evidence as though they were
    in it. A surveyed record in the same metre wins: a drawn record never
    replaces a driven one. They live here and not in the bundle because a
    bundle's validator takes a source id to be hex and refuses one carrying
    `drawn-524eff6e` whole.

How they get here: a **pull** from the shared repo fetches the circuit's
corrections with its bundle (the index names the file beside each bundle
that has one) and keeps them at `data/track-bundles/corrections/<slug>.json`;
the pack's import script puts them through the same endpoint. They are the
repo's decision, not evidence, so they are never merged — the repo's file
replaces the last one, and a pull of a circuit the repo no longer corrects
removes it — never written into the bundle, and never uploaded: the tracks
adapter sends the bundle document and nothing else. `track_compile.for_track`
applies them at compile time and recompiles when the file changes, exactly
as it does when the bundle does.

The format is `gt7-datalogger-track-corrections` v1, defined by the data
repo's `tools/corrections.py`. This is the second implementation of it and
is held to the first: the limits, the rules and the geometry tests below
are that module's, and a document the repo's validator accepts must be
accepted here.
"""

from __future__ import annotations

import copy
import json
import logging
import math
from pathlib import Path
from typing import Any

from app.processing import track_bundle
from app.processing.track_bundle import BundleError, edge_key, same_level

log = logging.getLogger(__name__)

FORMAT = "gt7-datalogger-track-corrections"
VERSION = 1
DIRECTORY = "corrections"  # under track-bundles/

SIDES = ("L", "R")
DRAWN_SOURCE_PREFIX = "drawn-"

# The data repo's limits: generous for a circuit and small enough that a file
# of them is still something a person can review in a pull request.
MAX_AREAS = 200
MIN_POLYGON_POINTS = 3
MAX_POLYGON_POINTS = 200
MAX_REASON = 280
MAX_NAME = 80
MAX_DRAWN = 15_000
MANUAL_KINDS = track_bundle.MANUAL_KINDS  # what somebody can say lies beyond a border
DRAWN_KEYS = ("x", "z", "y", "hx", "hz", "side", "kind", "votes", "run", "tw")
# A circuit is a few kilometres across. A coordinate out here is a typo or a
# unit mistake, and an area that size would hide a whole survey.
MAX_COORDINATE_M = 50_000.0

HEADER_KEYS = ("format", "version", "official_id", "track", "compile")
AREA_KEYS = ("id", "sides", "polygon", "y", "only_drawn", "reason", "by", "at")

# --- the document -------------------------------------------------------------


def is_empty(doc: dict[str, Any] | None) -> bool:
    """Whether the document says anything at all. One that does not need not exist."""
    if not doc:
        return True
    return (not doc.get("exclude") and not doc.get("draw")
            and (doc.get("compile") or {}).get("smooth_borders") is None)


def _finite(value: Any) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def _text(value: Any, where: str, limit: int, *, required: bool = False) -> str:
    if value is None and not required:
        return ""
    if not isinstance(value, str):
        raise BundleError(f"{where} must be text")
    if required and not value.strip():
        raise BundleError(f"{where} must not be empty")
    if len(value) > limit:
        raise BundleError(f"{where} is longer than {limit} characters")
    if any(ord(ch) < 32 for ch in value):
        raise BundleError(f"{where} must not contain control characters")
    return value


def _drawn(raw: Any, index: int) -> dict[str, Any]:
    where = f"draw[{index}]"
    if not isinstance(raw, dict):
        raise BundleError(f"{where} must be an object")
    unknown = sorted(set(raw) - set(DRAWN_KEYS))
    if unknown:
        raise BundleError(f"{where} has unknown key(s): {', '.join(unknown)}")
    for key in ("x", "z", "hx", "hz"):
        if not _finite(raw.get(key)):
            raise BundleError(f"{where}.{key} must be a number")
    if abs(raw["x"]) > MAX_COORDINATE_M or abs(raw["z"]) > MAX_COORDINATE_M:
        raise BundleError(f"{where} is not on any circuit")
    for key in ("y", "tw"):
        if raw.get(key) is not None and not _finite(raw[key]):
            raise BundleError(f"{where}.{key} must be a number or null")
    if raw.get("side") not in SIDES:
        raise BundleError(f"{where}.side must be L or R")
    kind = raw.get("kind")
    if kind not in MANUAL_KINDS:
        raise BundleError(
            f"{where}.kind must be one of {', '.join(MANUAL_KINDS)}: "
            "nobody can infer a metre nobody drove"
        )
    run = raw.get("run")
    if not isinstance(run, int) or isinstance(run, bool) or run < 1:
        raise BundleError(f"{where}.run must be a whole number of at least 1")
    votes = raw.get("votes")
    if not isinstance(votes, dict) or set(votes) != {kind}:
        raise BundleError(f"{where}.votes must vote for its own kind and no other")
    by_source = votes[kind]
    if not isinstance(by_source, dict) or not by_source:
        raise BundleError(f"{where}.votes names nobody")
    for source, entry in by_source.items():
        # The whole reason this list exists. A record under anybody's
        # installation id is a lap, and laps are not written here.
        if not isinstance(source, str) or not source.startswith(DRAWN_SOURCE_PREFIX):
            raise BundleError(
                f"{where} is filed under {source!r}: only a {DRAWN_SOURCE_PREFIX} "
                "source may be drawn"
            )
        if (not isinstance(entry, list) or len(entry) != 2
                or not all(isinstance(v, int) and not isinstance(v, bool) and v >= 0
                           for v in entry)):
            raise BundleError(f"{where}.votes[{kind!r}][{source!r}] must be [count, run]")
    return {key: raw.get(key) for key in DRAWN_KEYS}


def _area(raw: Any, index: int) -> dict[str, Any]:
    where = f"exclude[{index}]"
    if not isinstance(raw, dict):
        raise BundleError(f"{where} must be an object")
    unknown = sorted(set(raw) - set(AREA_KEYS))
    if unknown:
        raise BundleError(f"{where} has unknown key(s): {', '.join(unknown)}")

    area_id = _text(raw.get("id"), f"{where}.id", 40, required=True)

    sides = raw.get("sides")
    if not isinstance(sides, list) or not sides:
        raise BundleError(f"{where}.sides must list at least one of L, R")
    if any(side not in SIDES for side in sides) or len(set(sides)) != len(sides):
        raise BundleError(f"{where}.sides may only hold L and R, once each")

    polygon = raw.get("polygon")
    if (not isinstance(polygon, list)
            or not (MIN_POLYGON_POINTS <= len(polygon) <= MAX_POLYGON_POINTS)):
        raise BundleError(
            f"{where}.polygon must have {MIN_POLYGON_POINTS} to {MAX_POLYGON_POINTS} points"
        )
    points: list[list[float]] = []
    for k, point in enumerate(polygon):
        if (not isinstance(point, list) or len(point) != 2
                or not all(_finite(v) and abs(v) <= MAX_COORDINATE_M for v in point)):
            raise BundleError(f"{where}.polygon[{k}] must be [x, z] in metres")
        points.append([float(point[0]), float(point[1])])
    if abs(polygon_area(points)) < 1e-6:
        raise BundleError(f"{where}.polygon encloses no ground")

    levels = raw.get("y")
    if levels is not None:
        if (not isinstance(levels, list) or len(levels) != 2
                or not all(_finite(v) for v in levels) or levels[0] > levels[1]):
            raise BundleError(
                f"{where}.y must be null or [lowest, highest] elevation in metres"
            )
        levels = [float(levels[0]), float(levels[1])]

    only_drawn = raw.get("only_drawn", False)
    if not isinstance(only_drawn, bool):
        raise BundleError(f"{where}.only_drawn must be true or false")

    return {
        "id": area_id,
        "sides": [side for side in SIDES if side in sides],
        "polygon": points,
        "y": levels,
        "only_drawn": only_drawn,
        # The reason is required: an area with none is a hole in the map that
        # nobody can later judge whether to keep.
        "reason": _text(raw.get("reason"), f"{where}.reason", MAX_REASON, required=True),
        "by": _text(raw.get("by"), f"{where}.by", MAX_NAME),
        "at": _text(raw.get("at"), f"{where}.at", 40),
    }


def validate(raw: Any) -> dict[str, Any]:
    """The document, normalised, or a BundleError a person can act on."""
    if not isinstance(raw, dict):
        raise BundleError("a corrections document must be a JSON object")
    if raw.get("format") != FORMAT:
        raise BundleError(f"format must be {FORMAT}")
    if raw.get("version") != VERSION:
        raise BundleError(f"version must be {VERSION}")
    unknown = sorted(set(raw) - set(HEADER_KEYS) - {"exclude", "draw"})
    if unknown:
        raise BundleError(f"unknown key(s): {', '.join(unknown)}")

    official_id = _text(raw.get("official_id"), "official_id", 32, required=True)
    track = _text(raw.get("track"), "track", 200, required=True)

    compile_block = raw.get("compile", {"smooth_borders": None})
    if not isinstance(compile_block, dict) or set(compile_block) - {"smooth_borders"}:
        raise BundleError("compile may only hold smooth_borders")
    smooth = compile_block.get("smooth_borders")
    if smooth is not None and not isinstance(smooth, bool):
        # "off" is truthy. A circuit whose owner wrote it would go on being
        # smoothed, which is the opposite of what they asked for.
        raise BundleError("compile.smooth_borders must be true, false or null")

    areas_raw = raw.get("exclude", [])
    if not isinstance(areas_raw, list) or len(areas_raw) > MAX_AREAS:
        raise BundleError(f"exclude must be a list of at most {MAX_AREAS} areas")
    areas = [_area(area, index) for index, area in enumerate(areas_raw)]
    ids = [area["id"] for area in areas]
    if len(set(ids)) != len(ids):
        raise BundleError("every exclude area needs an id of its own")

    drawn_raw = raw.get("draw", [])
    if not isinstance(drawn_raw, list) or len(drawn_raw) > MAX_DRAWN:
        raise BundleError(f"draw must be a list of at most {MAX_DRAWN} records")
    drawn = [_drawn(record, index) for index, record in enumerate(drawn_raw)]
    cells = [edge_key(record) for record in drawn]
    if len(set(cells)) != len(cells):
        raise BundleError("draw holds two records for one metre of one side")

    return {
        "format": FORMAT,
        "version": VERSION,
        "official_id": official_id,
        "track": track,
        "compile": {"smooth_borders": smooth},
        "exclude": areas,
        "draw": sorted(drawn, key=lambda r: (r["x"], r["z"], r["side"])),
    }


def summary(doc: dict[str, Any] | None) -> dict[str, Any] | None:
    """What a document says, in three numbers — for a pull's reply and the
    Tracks view. None for no document."""
    if doc is None:
        return None
    return {
        "areas": len(doc.get("exclude") or []),
        "drawn": len(doc.get("draw") or []),
        "smooth_borders": smooth_override(doc),
    }


# --- what a correction does -----------------------------------------------------


def polygon_area(polygon: list[list[float]]) -> float:
    """Signed, by the shoelace rule. Zero is a line or a point."""
    total = 0.0
    for (ax, az), (bx, bz) in zip(polygon, polygon[1:] + polygon[:1], strict=True):
        total += ax * bz - bx * az
    return total / 2


def point_in_polygon(x: float, z: float, polygon: list[list[float]]) -> bool:
    """Ray crossing, the same test the repo's editor uses on screen."""
    inside = False
    j = len(polygon) - 1
    for i, (ax, az) in enumerate(polygon):
        bx, bz = polygon[j]
        if (az > z) != (bz > z):
            crossing = ax + ((z - az) / (bz - az)) * (bx - ax)
            if x < crossing:
                inside = not inside
        j = i
    return inside


def _drawn_only(edge: dict[str, Any]) -> bool:
    sources = {
        source for by_source in (edge.get("votes") or {}).values() for source in by_source
    }
    return bool(sources) and all(s.startswith(DRAWN_SOURCE_PREFIX) for s in sources)


def excludes(area: dict[str, Any], edge: dict[str, Any]) -> bool:
    """Whether this area keeps this record out of the compiled geometry."""
    if edge.get("side") not in area["sides"]:
        return False
    levels = area.get("y")
    elevation = edge.get("y")
    # A record without an elevation is on every level: an area drawn round a
    # bridge must still catch the metre recorded before elevation existed.
    if (levels is not None and elevation is not None
            and not (levels[0] <= elevation <= levels[1])):
        return False
    if area.get("only_drawn") and not _drawn_only(edge):
        return False
    return point_in_polygon(edge["x"], edge["z"], area["polygon"])


def excluded(edges: list[dict[str, Any]], doc: dict[str, Any] | None) -> list[int]:
    """Indices of the records the corrections keep out, in order."""
    areas = (doc or {}).get("exclude") or []
    if not areas:
        return []
    return [i for i, edge in enumerate(edges) if any(excludes(area, edge) for area in areas)]


def apply(
    edges: list[dict[str, Any]], doc: dict[str, Any] | None
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """The records as the compiler should see them, and what changed.

    Never the bundle itself: what comes back is not a valid bundle to write
    anywhere — its votes name `drawn-` sources and its run counts no longer
    follow from its records — and is not meant to be. It exists to be
    compiled and thrown away. The counts are `excluded` (records an area kept
    out) and `drawn` (records added from `draw`).
    """
    drop = set(excluded(edges, doc))
    drawn = (doc or {}).get("draw") or []
    if not drop and not drawn:
        return edges, {"excluded": 0, "drawn": 0}
    kept = [edge for i, edge in enumerate(edges) if i not in drop]

    # What was drawn goes in with the evidence, unless an area keeps it out —
    # `only_drawn` is how a bridge is taken back — or somebody has since
    # driven that metre: a drawn record never replaces a driven one.
    surveyed: dict[tuple[int, int, str], list[dict[str, Any]]] = {}
    for edge in edges:
        surveyed.setdefault(edge_key(edge), []).append(edge)
    hidden = set(excluded(drawn, doc))
    added = 0
    for i, record in enumerate(drawn):
        if i in hidden:
            continue
        if any(same_level(record, edge) for edge in surveyed.get(edge_key(record), ())):
            continue
        kept.append(copy.deepcopy(record))
        added += 1
    return kept, {"excluded": len(drop), "drawn": added}


def smooth_override(doc: dict[str, Any] | None) -> bool | None:
    """This circuit's own answer about smoothing, or None to follow the default."""
    value = ((doc or {}).get("compile") or {}).get("smooth_borders")
    return value if isinstance(value, bool) else None


# --- on disk ------------------------------------------------------------------


def path(data_dir: Path, slug: str) -> Path:
    return data_dir / track_bundle.BUNDLE_DIR / DIRECTORY / f"{slug}.json"


def identity(data_dir: Path, slug: str) -> list[int] | None:
    """What `for_track` keys a compile on: the file's mtime and size, or None
    for a circuit with no corrections. Cheap, and read before the bundle's."""
    try:
        stat = path(data_dir, slug).stat()
    except OSError:
        return None
    return [stat.st_mtime_ns, stat.st_size]


def load(data_dir: Path, slug: str) -> dict[str, Any] | None:
    """The circuit's corrections, validated, or None when it has none.

    A file that will not validate is treated as absent and said so: the map
    goes on being drawn from the evidence, which is what it did before the
    file arrived, rather than not at all.
    """
    file = path(data_dir, slug)
    try:
        text = file.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        log.warning("unreadable corrections %s: %s", file, exc)
        return None
    try:
        return validate(json.loads(text))
    except (ValueError, BundleError) as exc:
        log.warning("corrections %s ignored: %s", file.name, exc)
        return None


def write(data_dir: Path, slug: str, doc: dict[str, Any]) -> Path:
    """Keep a validated document as the circuit's corrections, replacing any."""
    file = path(data_dir, slug)
    file.parent.mkdir(parents=True, exist_ok=True)
    tmp = file.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, separators=(",", ":")), encoding="utf-8")
    tmp.replace(file)
    return file


def remove(data_dir: Path, slug: str) -> bool:
    file = path(data_dir, slug)
    try:
        file.unlink()
    except FileNotFoundError:
        return False
    return True


def move(data_dir: Path, slug: str, new_slug: str) -> bool:
    """A renamed bundle keeps its corrections — unless the name it moved onto
    already has some, which are that circuit's and stay."""
    if new_slug == slug:
        return False
    source = path(data_dir, slug)
    target = path(data_dir, new_slug)
    if not source.exists():
        return False
    if target.exists():
        source.unlink()
        return False
    source.replace(target)
    return True
