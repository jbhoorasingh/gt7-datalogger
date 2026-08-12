"""Track auto-identification from lap geometry.

Two fingerprints, tried in that order:

**A stored signature** — lap length plus the bounding box of the racing line,
written when a human names a circuit. GT7 world coordinates are fixed per
track, so it matches future sessions regardless of car or lap time. It is
tried first because a name a person typed outranks anything inferred.

**A survey bundle** — the surveyed road itself (#41). This exists because the
signature path has a bootstrapping hole that made the whole feature useless
in practice: a signature only exists once somebody has named a circuit, so a
driver who surveys three tracks and never uses "name track…" gets no
identification at all, and everything hanging off the circuit name — the
outline under the race line, category bests, corner labels — silently stays
empty. Having surveyed a track and having named it were two separate facts,
and nothing joined them.

A bundle is also a strictly better fingerprint than a bounding box: it is the
road, not a rectangle around it. Matching asks the only question that
matters — *did this lap drive on this surveyed tarmac?* — by counting how
many of the lap's positions have border evidence beside them. On the author's
own recordings the correct circuit scores 100 % and the runners-up 50 % or
less, including between two configurations of the same venue that share a
bounding box entirely.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from app.processing import track_bundle

log = logging.getLogger(__name__)

LENGTH_TOLERANCE = 0.04  # 4 % lap-length difference (racing line varies)
CENTER_TOLERANCE_M = 120.0
EXTENT_TOLERANCE = 0.20

# --- bundle matching ----------------------------------------------------------

# Occupancy grid for border evidence. Coarse on purpose: the question is "was
# the car on this road", and a cell wide enough to span a carriageway answers
# it whichever line through the corner the driver took.
BUNDLE_CELL_M = 20.0
# Lap positions tested. A lap is thousands of ticks and the answer does not
# improve past a few hundred well-spread points.
BUNDLE_SAMPLES = 600
# Share of those points that must sit on surveyed road. Calibrated against 321
# of the author's recorded sessions scored over three bundles: the result is
# sharply bimodal — 13 sessions at 100 %, 5 more at 0.65-0.85 (all of them on
# the circuit with the thinnest survey, 406 border records), then NOTHING until
# 0.33, below which sit the 302 sessions driven at circuits with no bundle at
# all. The cut goes in the middle of that empty band: twice the noise floor,
# and still low enough that a half-finished survey identifies its own circuit.
BUNDLE_MIN_COVERAGE = 0.60
# Shortest lap worth identifying a session from. Deliberately not the
# lap-detection minimum: that one exists to reject phantom laps, while this one
# only asks whether there is enough driven geometry to score — a couple of
# seconds of it is plenty, and picking the shortest usable lap is what keeps a
# whole-history backfill from reading every sample blob on disk.
IDENTIFY_MIN_TICKS = 120
# ...and it must beat the runner-up by this much. Two configurations of one
# venue share tarmac, so the loser is never at zero; what separates them is
# the margin, and a thin one means the evidence does not actually distinguish
# them. Refusing to guess is the right answer there.
BUNDLE_MIN_MARGIN = 0.25


@dataclass(slots=True)
class TrackSignature:
    length_m: float
    min_x: float
    max_x: float
    min_z: float
    max_z: float


class TrackLike(Protocol):
    length_m: float
    min_x: float
    max_x: float
    min_z: float
    max_z: float


def signature_from_samples(samples: dict[str, list[float]]) -> TrackSignature | None:
    if not samples.get("dist") or not samples.get("pos_x"):
        return None
    return TrackSignature(
        length_m=samples["dist"][-1],
        min_x=min(samples["pos_x"]),
        max_x=max(samples["pos_x"]),
        min_z=min(samples["pos_z"]),
        max_z=max(samples["pos_z"]),
    )


def matches(sig: TrackSignature, track: TrackLike) -> bool:
    if track.length_m <= 0 or sig.length_m <= 0:
        return False
    if abs(sig.length_m - track.length_m) / track.length_m > LENGTH_TOLERANCE:
        return False
    sig_cx = (sig.min_x + sig.max_x) / 2
    sig_cz = (sig.min_z + sig.max_z) / 2
    trk_cx = (track.min_x + track.max_x) / 2
    trk_cz = (track.min_z + track.max_z) / 2
    if abs(sig_cx - trk_cx) > CENTER_TOLERANCE_M or abs(sig_cz - trk_cz) > CENTER_TOLERANCE_M:
        return False
    for sig_ext, trk_ext in (
        (sig.max_x - sig.min_x, track.max_x - track.min_x),
        (sig.max_z - sig.min_z, track.max_z - track.min_z),
    ):
        if trk_ext > 0 and abs(sig_ext - trk_ext) / trk_ext > EXTENT_TOLERANCE:
            return False
    return True


# --- matching a lap against the survey bundles (#41) ---------------------------


@dataclass(slots=True)
class Fingerprint:
    """One circuit's surveyed road as an occupancy grid."""

    track: str
    cells: frozenset[tuple[int, int]]
    points: int  # border records behind it, for reporting


def fingerprint(doc: dict[str, Any]) -> Fingerprint:
    return Fingerprint(
        track=doc["meta"]["track"],
        cells=frozenset(
            (math.floor(e["x"] / BUNDLE_CELL_M), math.floor(e["z"] / BUNDLE_CELL_M))
            for e in doc["edges"]
        ),
        points=len(doc["edges"]),
    )


def coverage(samples: dict[str, list[float]], print_: Fingerprint) -> float:
    """Share of the lap's positions with border evidence beside them.

    The 3x3 cell neighbourhood is checked rather than the exact cell, so a
    lap running down the middle of a wide road still counts as on it.
    """
    xs = samples.get("pos_x") or []
    zs = samples.get("pos_z") or []
    n = min(len(xs), len(zs))
    if n == 0 or not print_.cells:
        return 0.0
    step = max(1, n // BUNDLE_SAMPLES)
    tested = hit = 0
    for i in range(0, n, step):
        cx = math.floor(xs[i] / BUNDLE_CELL_M)
        cz = math.floor(zs[i] / BUNDLE_CELL_M)
        tested += 1
        if any(
            (cx + a, cz + b) in print_.cells for a in (-1, 0, 1) for b in (-1, 0, 1)
        ):
            hit += 1
    return hit / tested if tested else 0.0


def match_bundles(
    samples: dict[str, list[float]], prints: list[Fingerprint]
) -> tuple[str, float] | None:
    """Which surveyed circuit this lap was driven on, if the evidence is clear.

    Returns (track, coverage) or None. None covers both "no bundle describes
    this lap" and "two of them describe it about equally well" — the second is
    a real case (a venue's configurations overlap) and guessing between them
    would put the wrong name on a session silently.
    """
    scored = sorted(
        ((coverage(samples, p), p.track) for p in prints), reverse=True
    )
    if not scored:
        return None
    best, track = scored[0]
    runner_up = scored[1][0] if len(scored) > 1 else 0.0
    if best < BUNDLE_MIN_COVERAGE or best - runner_up < BUNDLE_MIN_MARGIN:
        return None
    return track, best


# Fingerprints are derived from bundles that are multiple megabytes each, so
# they are cached against the files' identity (path + mtime + size) — a
# re-survey, an import or a rename invalidates them without anyone having to
# remember to.
_PRINTS: dict[tuple[tuple[str, int, int], ...], list[Fingerprint]] = {}


def load_fingerprints(data_dir: Path) -> list[Fingerprint]:
    """Every surveyed circuit's fingerprint. Blocking: parses the bundles."""
    directory = data_dir / track_bundle.BUNDLE_DIR
    if not directory.is_dir():
        return []
    key: list[tuple[str, int, int]] = []
    for path in sorted(directory.glob("*.json")):
        try:
            stat = path.stat()
        except OSError:  # pragma: no cover - vanished between glob and stat
            continue
        key.append((path.name, stat.st_mtime_ns, stat.st_size))
    cached = _PRINTS.get(tuple(key))
    if cached is not None:
        return cached
    prints = []
    for name, _mtime, _size in key:
        doc = track_bundle.load_slug(data_dir, Path(name).stem)
        if doc is not None:
            prints.append(fingerprint(doc))
    _PRINTS.clear()  # one installation, one bundle set; no need for true LRU
    _PRINTS[tuple(key)] = prints
    log.info("track fingerprints built: %s", [p.track for p in prints])
    return prints


def identify_from_bundles(
    data_dir: Path, samples: dict[str, list[float]]
) -> tuple[str, float] | None:
    """Name the circuit a lap was driven on from the survey bundles."""
    return match_bundles(samples, load_fingerprints(data_dir))
