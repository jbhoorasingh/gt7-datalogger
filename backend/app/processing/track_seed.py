"""The shipped seed of track signatures (#58).

Identification had a bootstrapping hole. Both of its fingerprints needed data
only a user could produce: a stored signature exists once somebody names a
circuit, and a survey bundle exists once somebody drives every metre of one.
So a fresh install identified nothing, every session stayed unnamed, and the
outline, corner labels and category bests hanging off the circuit name stayed
empty — with no hint that naming one circuit would light all of it up.

The seed closes that hole with geometry computed offline: length plus the
bounding box of the racing line, per configuration, for circuits somebody has
already driven and published. It is generated in gt7-datalogger-track-data,
which owns the data because correcting it is a row edit and not a code change,
and vendored here so a first packet on an offline machine still resolves.

**A seed is weaker evidence than a name a person typed**, and the table records
which is which (`TrackRow.provenance`). Two consequences, both in
`Repository.find_track`: a user row wins outright, and two seed rows matching
the same lap produce no name at all rather than whichever the database happened
to return first. That second rule is not hypothetical — a bounding box cannot
separate Lago Maggiore Full Course from Suzuka, nor Road Atlanta from Watkins
Glen, and the generator marks those rows for exactly this reason.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from app.processing.track_bundle import MAX_TRACK_NAME, BundleError, _number, _text

log = logging.getLogger(__name__)

SEED_FORMAT = "gt7-datalogger-track-signatures"
SEED_VERSION = 1
# The catalog knows 121 configurations; a seed listing hundreds more is not a
# seed for this game.
MAX_SEED_ROWS = 500
# The Nordschleife is 20 km; at a point every 20 m that is ~1,000. Ten times
# that is room for a finer step without being a channel for a huge document.
MAX_PATH_POINTS = 10_000
PROVENANCES = ("survey", "capture")


@dataclass(slots=True)
class SeedRow:
    """One configuration's signature. Shaped to satisfy `tracks.TrackLike`."""

    official_id: str
    name: str
    length_m: float
    min_x: float
    max_x: float
    min_z: float
    max_z: float
    provenance: str
    # The racing line, thinned to roughly a point every 20 m and kept in
    # DRIVING ORDER. Order is the whole point: a bounding box cannot tell a
    # layout from its reverse — same box, same length — and the direction a
    # lap runs along this path is what separates them.
    path: tuple[tuple[float, float], ...] = ()
    # The reverse configuration of the same layout, when GT7 has one. Carried
    # here rather than looked up, so naming a reverse lap needs no catalog
    # read on the identification path.
    reverse_id: str = ""
    reverse_name: str = ""


def parse(raw: Any) -> list[SeedRow]:
    """The signatures a seed document offers, rebuilt from checked values.

    Validated rather than trusted even though it ships inside the build: the
    same reader is what a future refresh from the shared repository would use,
    and a document that has been over a network is somebody else's machine.
    """
    if not isinstance(raw, dict) or raw.get("format") != SEED_FORMAT:
        raise BundleError(f"not a {SEED_FORMAT} document")
    version = raw.get("version")
    if not isinstance(version, int) or version < 1:
        raise BundleError("missing or invalid seed version")
    if version > SEED_VERSION:
        raise BundleError(
            f"seed is format v{version}; this build reads v{SEED_VERSION} — upgrade first"
        )
    signatures = raw.get("signatures")
    if not isinstance(signatures, list):
        raise BundleError("signatures must be a list")
    if len(signatures) > MAX_SEED_ROWS:
        raise BundleError(f"seed lists more than {MAX_SEED_ROWS} signatures")

    out: list[SeedRow] = []
    seen: set[str] = set()
    for i, entry in enumerate(signatures):
        where = f"signature {i}"
        if not isinstance(entry, dict):
            raise BundleError(f"{where} is not an object")
        official_id = _text(entry.get("official_id", ""), f"{where}: official_id", 32).strip()
        name = _text(
            entry.get("official_name", ""), f"{where}: official_name", MAX_TRACK_NAME
        ).strip()
        if not official_id or not name:
            raise BundleError(f"{where}: official_id and official_name are required")
        if official_id in seen:
            raise BundleError(f"{where}: duplicate official_id {official_id!r}")
        seen.add(official_id)
        provenance = _text(entry.get("provenance", ""), f"{where}: provenance", 16).strip()
        if provenance not in PROVENANCES:
            raise BundleError(
                f"{where}: provenance is {provenance!r}, not one of {PROVENANCES}"
            )
        box = {f: _number(entry.get(f), f"{where}: {f}") for f in
               ("length_m", "min_x", "max_x", "min_z", "max_z")}
        if box["length_m"] <= 0:
            raise BundleError(f"{where}: length_m must be positive")
        if box["min_x"] > box["max_x"] or box["min_z"] > box["max_z"]:
            raise BundleError(f"{where}: bounding box is inside out")
        path = _path(entry.get("path"), where)
        twin = entry.get("reverse") or {}
        if not isinstance(twin, dict):
            raise BundleError(f"{where}: reverse is not an object")
        reverse_id = _text(twin.get("official_id", ""), f"{where}: reverse.official_id", 32).strip()
        reverse_name = _text(
            twin.get("official_name", ""), f"{where}: reverse.official_name", MAX_TRACK_NAME
        ).strip()
        # A twin named but not identified (or the other way round) cannot be
        # written to a session, and half a name is worse than none.
        if bool(reverse_id) != bool(reverse_name):
            raise BundleError(f"{where}: reverse needs both official_id and official_name")
        out.append(SeedRow(
            official_id=official_id, name=name, provenance=provenance, path=path,
            reverse_id=reverse_id, reverse_name=reverse_name, **box,
        ))
    return out


def _path(raw: Any, where: str) -> tuple[tuple[float, float], ...]:
    """The thinned racing line, as (x, z) pairs in driving order.

    Absent is allowed and means "no direction evidence for this circuit" —
    the app then declines rather than guessing which way round a lap went,
    exactly as it does for a signature that matches two circuits.
    """
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise BundleError(f"{where}: path is not a list")
    if len(raw) > MAX_PATH_POINTS:
        raise BundleError(f"{where}: path has more than {MAX_PATH_POINTS} points")
    out: list[tuple[float, float]] = []
    for i, point in enumerate(raw):
        if not isinstance(point, list | tuple) or len(point) != 2:
            raise BundleError(f"{where}: path[{i}] is not an [x, z] pair")
        out.append((
            _number(point[0], f"{where}: path[{i}].x"),
            _number(point[1], f"{where}: path[{i}].z"),
        ))
    return tuple(out)


def digest(path: Path) -> str:
    """Content hash of the seed file, used as its version marker.

    A hash rather than a version field because the question at startup is
    "is the database holding what this file says", and a generated file whose
    contents changed is a different answer even when nobody bumped a number.
    """
    return sha256(path.read_bytes()).hexdigest()[:16]


def load(path: Path) -> list[SeedRow]:
    """Parse the seed at `path`. Returns [] when there is nothing to read.

    A missing or broken seed must never stop the app: identification without
    it is exactly the behaviour of every build before this one.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        log.info("no track signature seed at %s; identification starts empty", path)
        return []
    except (OSError, ValueError) as exc:
        log.warning("track signature seed at %s is unreadable: %s", path, exc)
        return []
    try:
        return parse(raw)
    except BundleError as exc:
        log.warning("track signature seed at %s is invalid: %s", path, exc)
        return []
