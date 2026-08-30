"""Per-track survey bundles: track knowledge that outlives a run.

A survey run is ephemeral (restarts, new sessions), but the track isn't —
its perimeters and finish line don't move. Every run's border evidence is
merged into one JSON document per circuit under data/track-bundles/, and
every new run on that circuit starts from it: the map opens with everything
ever mapped and improves from there.

Evidence is accumulated on a 1 m grid keyed by (cell, side) — NOT by kind.
A metre of border is one fact, and the kinds observed there are *votes* on
what that fact is, so re-driving mapped ground can correct the record
instead of only extending it. Format v1 keyed on kind as well, which meant
contradictions were stored side by side rather than resolved: in the
author's Lago Maggiore Centre bundle 892 of 4634 cells (19%) held two or
more kinds, and at 112 of them a hand-marked run-off limit sat on top of an
auto/straddle point that silently kept the metre in the road fill (the
consumer drops "runoff" points, but the co-located twin survives the
filter). Voting resolves those to one answer per metre.

Votes are counted PER RUN PER SOURCE: `votes[kind][source] = [count, last_run]`,
and a merge increments only when the incoming evidence comes from a later
run *of that same source* than the one already counted. The run stamp makes
saving idempotent — the ~60 s autosave writes the same run's evidence
repeatedly, and without it a long session would inflate its own votes by a
factor of however many times it happened to autosave. The source stamp is
what makes the counts mean anything ACROSS installations (#47): run ordinals
are local, so my run 7 and your run 7 are unrelated facts, and a merge keyed
on the ordinal alone would double-count one and drop the other depending on
which ordinals happened to collide. With the source carried per vote, two
people's bundles combine by taking each source's own highest run — the
counts stay a census of independent observations, which is the only reading
under which "manual tier, then majority" resolves anything.

Points carry the provenance needed to second-guess them later: `run` (the
run ordinal that first evidenced the cell) and `tw` (the axle track width
in use when it was laid). `tw` matters because straddle points — 52% of
Centre, 88% of East End — are placed at ±tw/2 from the car centre, so they
carry the whole width-estimate error; recording it keeps the option of
correcting their lateral offset offline once a better width is known.
Position itself stays first-seen, which keeps files stable (small diffs
once these live in their own repo: the format is self-describing and
versioned precisely so bundles can be exported there and imported at build
time later, like app/data/tracks.json).

The document also carries **authored** track knowledge (v4): corners and
sections labelled by hand after the survey. Authored data outranks derived
data outright — `detect_corners()` re-infers corners from every lap's
curvature, so its numbering can differ between two laps of one session,
which makes cross-lap comparison rest on sand (#48). Corners live here
rather than in the DB because they are a property of the circuit, they
should travel with export/import, and they are a large part of what makes a
shared bundle worth pulling.

Track width calibration is deliberately NOT in the bundle: it is a property
of the car being driven, not of the circuit.

The full format is documented in docs/reference/track-bundle-format.md.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

BUNDLE_FORMAT = "gt7-datalogger-track-bundle"
BUNDLE_VERSION = 4
BUNDLE_DIR = "track-bundles"
GRID_M = 1.0  # dedup cell: one record per metre per side
MAX_POINTS = 50_000
MAX_FINISH_CROSSINGS = 20
MAX_CORNERS = 100  # Nordschleife is 73 named corners; 100 is head-room
MAX_SECTIONS = 40
MAX_LABEL = 60  # corner/section name
MAX_TRACK_NAME = 80  # matches the API's track-name cap

# Where an installation's identity lives. One id per data dir, generated on
# first use and never shown to anyone: it exists to keep two people's run
# ordinals apart, not to identify a person.
SOURCE_FILE = "source-id.json"
SOURCE_ID_CHARS = 12
_SOURCE_RE = re.compile(r"^[0-9a-f]{6,32}$")
_source_cache: dict[Path, str] = {}

# Resolution order. Manual marks beat automatic inference outright rather
# than by majority, because the two are not measuring the same thing: the
# surface chars are BLIND to walls and paved run-off (both read as plain
# tarmac), so an auto/straddle point at a hand-marked metre is not evidence
# against the mark — it is evidence that the char stream could not see it.
# Majority still applies within each tier, which is also the recovery path
# for a mis-mark: marking the metre correctly twice outvotes one stale wall.
MANUAL_KINDS = ("wall", "runoff", "edge")  # driver-declared
AUTO_KINDS = ("auto", "straddle")  # inferred; "auto" is transition-derived
KIND_ORDER = MANUAL_KINDS + AUTO_KINDS


def slugify(track: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", track.lower()).strip("-") or "unnamed"


def bundle_path(data_dir: Path, track: str) -> Path:
    return data_dir / BUNDLE_DIR / f"{slugify(track)}.json"


def source_id(data_dir: Path) -> str:
    """This installation's id, created on first use.

    Written once and then read from disk forever: regenerating it would make
    every vote this installation has already cast look like it came from a
    stranger, so the file is treated as data, not as a cache.
    """
    cached = _source_cache.get(data_dir)
    if cached is not None:
        return cached
    path = data_dir / SOURCE_FILE
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        value = str(doc.get("source_id", ""))
        if _SOURCE_RE.match(value):
            _source_cache[data_dir] = value
            return value
        log.warning("%s holds no usable source id — generating a new one", path)
    except (OSError, ValueError):
        pass
    value = secrets.token_hex(SOURCE_ID_CHARS // 2)
    data_dir.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps({"source_id": value, "created_at": datetime.now(UTC).isoformat()}),
        encoding="utf-8",
    )
    tmp.replace(path)
    _source_cache[data_dir] = value
    log.info("installation source id created: %s", value)
    return value


def is_source_id(value: Any) -> bool:
    """Whether a string is a well-formed installation id."""
    return isinstance(value, str) and bool(_SOURCE_RE.match(value))


def edge_key(e: dict[str, Any]) -> tuple[int, int, str]:
    """One record per metre per side — kind is voted on, not part of identity."""
    return (round(e["x"] / GRID_M), round(e["z"] / GRID_M), e["side"])


Votes = dict[str, dict[str, list[int]]]


def vote_count(votes: Votes, kind: str) -> int:
    """Independent observations of `kind` at this metre, across all sources."""
    return sum(entry[0] for entry in votes.get(kind, {}).values())


def vote_totals(votes: Votes) -> dict[str, int]:
    return {kind: vote_count(votes, kind) for kind in votes}


def watermarks(edges: list[dict[str, Any]]) -> dict[str, int]:
    """The highest run each source has actually cast a vote at.

    `meta.source_runs` is only advanced when a run ENDS (`count_run`), while
    the ~60 s autosave writes that run's votes as it goes. A run killed before
    it stops therefore leaves votes stamped run N behind a counter that still
    says N-1 — and the next run would then reuse N, at which point
    `_merge_votes` reads its re-observations of that ground as already counted
    and drops them. Which is precisely the evidence the autosave existed to
    protect. The votes are the record; the counter follows them.
    """
    seen: dict[str, int] = {}
    for edge in edges:
        for sources in edge.get("votes", {}).values():
            for source, entry in sources.items():
                if entry[1] > seen.get(source, 0):
                    seen[source] = entry[1]
    return seen


def reconcile_runs(
    source_runs: dict[str, int], edges: list[dict[str, Any]]
) -> dict[str, int]:
    """Raise the per-source run counters to the evidence on disk."""
    out = dict(source_runs)
    for source, run in watermarks(edges).items():
        if run > out.get(source, 0):
            out[source] = run
    return dict(sorted(out.items()))


def resolve_kind(votes: Votes) -> str:
    """The kind a cell's votes settle on: manual tier first, then majority."""
    for tier in (MANUAL_KINDS, AUTO_KINDS):
        present = [k for k in tier if vote_count(votes, k)]
        if present:
            return max(present, key=lambda k: (vote_count(votes, k), -KIND_ORDER.index(k)))
    return "auto"  # unreachable for well-formed records; never crash a load


def cast_vote(e: dict[str, Any], kind: str, run: int, source: str) -> None:
    """Add one run's vote for `kind`, idempotently within that run and source."""
    bucket = e["votes"].setdefault(kind, {})
    prior = bucket.get(source)
    if prior is None:
        bucket[source] = [1, run]
    elif run > prior[1]:
        bucket[source] = [prior[0] + 1, run]
    e["kind"] = resolve_kind(e["votes"])


def new_edge(
    x: float, z: float, hx: float, hz: float, side: str,
    kind: str, run: int, source: str, tw: float | None, y: float | None = None,
) -> dict[str, Any]:
    e: dict[str, Any] = {
        "x": round(x, 3), "z": round(z, 3),
        # Elevation. GT7 broadcasts position on all three axes, so this is
        # free, and a border without it can only ever describe a flat track.
        # Null on records laid before v3; re-driving that metre fills it in
        # (see merge_edges), so it is recoverable rather than lost.
        "y": round(y, 3) if y is not None else None,
        "hx": round(hx, 5), "hz": round(hz, 5),
        "side": side, "kind": kind,
        "votes": {}, "run": run,
        "tw": round(tw, 3) if tw is not None else None,
    }
    cast_vote(e, kind, run, source)
    return e


def _merge_votes(cur: Votes, incoming: Votes) -> None:
    """Combine two records' votes, per kind and per source.

    Within one source the highest run ordinal it has been counted at is the
    watermark: evidence at or below it has already been counted, and evidence
    above it is one more independent observation. The count taken is the
    larger of "one more than what we held" and "what the incoming document
    claims", which is what lets the three callers all be right at once —
    an autosave re-merging its own accumulated record (claims the full
    count), a JSONL rebuild merging fresh single-vote records (claims 1), and
    an imported bundle carrying a stranger's whole history (claims all of it).
    """
    for kind, sources in incoming.items():
        bucket = cur.setdefault(kind, {})
        for source, entry in sources.items():
            count, last_run = entry[0], entry[1]
            prior = bucket.get(source)
            if prior is None:
                bucket[source] = [count, last_run]
            elif last_run > prior[1]:
                bucket[source] = [max(prior[0] + 1, count), last_run]


def merge_edges(
    existing: list[dict[str, Any]], new: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Union on the dedup grid, combining votes; existing geometry wins.

    Records in `existing` are mutated (callers pass a freshly loaded document
    or the run's own list); records copied out of `new` are never aliased, so
    a live run may keep mutating its own list after a save.
    """
    merged = list(existing)
    index = {edge_key(e): e for e in existing}
    for e in new:
        key = edge_key(e)
        cur = index.get(key)
        if cur is None:
            if len(merged) >= MAX_POINTS:
                continue
            copy = {
                **e,
                "votes": {
                    kind: {src: list(entry) for src, entry in sources.items()}
                    for kind, sources in e["votes"].items()
                },
            }
            index[key] = copy
            merged.append(copy)
            continue
        # Elevation backfill: a metre first mapped before v3 has no `y`, and
        # the next pass over it supplies one. Geometry otherwise stays
        # first-seen, but a null is not a measurement to defend.
        if cur.get("y") is None and e.get("y") is not None:
            cur["y"] = e["y"]
        _merge_votes(cur["votes"], e["votes"])
        cur["kind"] = resolve_kind(cur["votes"])
    return merged


def merge_finish(
    existing: list[dict[str, float]], new: list[dict[str, float]]
) -> list[dict[str, float]]:
    merged = list(existing)
    seen = {(round(c["x"]), round(c["z"])) for c in existing}
    for c in new:
        key = (round(c["x"]), round(c["z"]))
        if key in seen:
            continue
        seen.add(key)
        merged.append(c)
    return merged[-MAX_FINISH_CROSSINGS:]


def _upgrade_v1(edges: list[dict[str, Any]], source: str) -> list[dict[str, Any]]:
    """v1 stored one point per (cell, side, KIND) with no provenance.

    Co-located kinds become votes on one record — which is where the 112
    defeated run-off marks get their answer back. Everything v1 recorded was
    laid before runs were stamped, so it all votes as run 0 (the next run
    outranks it) with unknown width.
    """
    out: list[dict[str, Any]] = []
    index: dict[tuple[int, int, str], dict[str, Any]] = {}
    for old in edges:
        key = edge_key(old)
        cur = index.get(key)
        if cur is None:
            cur = {
                "x": old["x"], "z": old["z"], "y": None,
                "hx": old["hx"], "hz": old["hz"],
                "side": old["side"], "kind": old["kind"],
                "votes": {}, "run": 0, "tw": None,
            }
            index[key] = cur
            out.append(cur)
        cast_vote(cur, old["kind"], 0, source)
    return out


def _add_elevation_field(edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """v2 -> v3: records predate elevation capture; a re-drive fills them."""
    for e in edges:
        e.setdefault("y", None)
    return edges


def _attribute_votes(edges: list[dict[str, Any]], source: str) -> list[dict[str, Any]]:
    """v3 -> v4: `[count, last_run]` was implicitly this installation's.

    Which it was — a v3 file could only ever have been written by the machine
    it is sitting on, since there was no way to merge anyone else's. Naming
    that source is therefore lossless, and it is what lets the file take part
    in a cross-machine merge afterwards.
    """
    for e in edges:
        e["votes"] = {
            kind: {source: list(entry)}
            for kind, entry in e.get("votes", {}).items()
        }
        e["kind"] = resolve_kind(e["votes"])
    return edges


def _upgrade(doc: dict[str, Any], version: int, source: str, label: str) -> dict[str, Any]:
    """Bring an older document up to BUNDLE_VERSION, in memory."""
    before = len(doc["edges"])
    if version < 2:
        doc["edges"] = _upgrade_v1(doc["edges"], source)
    if version < 3:
        doc["edges"] = _add_elevation_field(doc["edges"])
    if version < 4:
        if version >= 2:  # v1 already cast its votes in the v4 shape
            doc["edges"] = _attribute_votes(doc["edges"], source)
        meta = doc["meta"]
        meta["source_runs"] = {source: int(meta.get("runs", 0) or 0)}
    doc.setdefault("corners", [])
    doc.setdefault("sections", [])
    doc["meta"].setdefault("official", None)
    doc["version"] = BUNDLE_VERSION
    log.info("upgraded track bundle %s v%s -> v%d (%d records -> %d voted cells)",
             label, version, BUNDLE_VERSION, before, len(doc["edges"]))
    return doc


def load(data_dir: Path, track: str) -> dict[str, Any] | None:
    return load_slug(data_dir, slugify(track))


def load_slug(data_dir: Path, slug: str) -> dict[str, Any] | None:
    path = data_dir / BUNDLE_DIR / f"{slug}.json"
    if not path.exists():
        return None
    try:
        doc: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        if doc.get("format") != BUNDLE_FORMAT:
            return None
        version = doc.get("version", 1)
        if version > BUNDLE_VERSION:
            # Written by a newer build: refuse rather than silently drop the
            # fields we don't know about and save the loss back over it.
            log.warning("track bundle %s is format v%s, this build reads v%d — "
                        "ignoring it", path.name, version, BUNDLE_VERSION)
            return None
        if version < BUNDLE_VERSION:
            doc = _upgrade(doc, version, source_id(data_dir), path.name)
        return doc
    except (ValueError, KeyError, TypeError, OSError) as exc:
        log.warning("unreadable track bundle %s: %s", path, exc)
        return None


def _write(data_dir: Path, track: str, doc: dict[str, Any]) -> dict[str, Any]:
    path = bundle_path(data_dir, track)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)  # atomic: a crash never leaves a half-written bundle
    return {**doc["meta"], "points": len(doc["edges"])}


def _document(
    track: str,
    edges: list[dict[str, Any]],
    finish: list[dict[str, float]],
    source_runs: dict[str, int],
    corners: list[dict[str, Any]],
    sections: list[dict[str, Any]],
    official: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "format": BUNDLE_FORMAT,
        "version": BUNDLE_VERSION,
        "meta": {
            "track": track,
            # Total independent runs behind this document. The per-source
            # breakdown is the authoritative one — `runs` is its sum, kept
            # because every consumer that ever printed a run count wants one
            # number, and because it says at a glance whether a bundle is
            # one person's work or several people's.
            "runs": sum(source_runs.values()),
            "source_runs": dict(sorted(source_runs.items())),
            "updated_at": datetime.now(UTC).isoformat(),
            # Which official GT7 layout this is, once a human has confirmed
            # it. Never inferred silently: GT7 broadcasts no track id and
            # tracks.json carries no world coordinates, so the match is a
            # suggestion the user accepts (#46).
            "official": official,
        },
        "edges": edges,
        "finish_crossings": finish,
        "corners": corners,
        "sections": sections,
    }


def save(
    data_dir: Path,
    track: str,
    edges: list[dict[str, Any]],
    finish_crossings: list[dict[str, float]],
    count_run: bool,
    source: str | None = None,
) -> dict[str, Any]:
    """Merge a run's evidence into the circuit's bundle; returns its meta.

    `source` is whose run this is, defaulting to this installation. Replaying
    a log recorded elsewhere passes the id it was recorded under, so the run
    is counted against the machine that drove it and not the one replaying —
    otherwise the votes say one thing and the run counter another.
    """
    existing = load(data_dir, track)
    source = source or source_id(data_dir)
    merged_edges = merge_edges(existing["edges"] if existing else [], edges)
    merged_finish = merge_finish(
        existing["finish_crossings"] if existing else [], finish_crossings
    )
    source_runs = dict(existing["meta"]["source_runs"]) if existing else {}
    if count_run:
        source_runs[source] = source_runs.get(source, 0) + 1
    # Deliberately NOT reconciled against the votes here: an autosave is not a
    # finished run, and counting one would make `runs` climb mid-session. The
    # lag that leaves after a crash is handled where it actually does damage —
    # in the choice of the NEXT run's ordinal (see next_run and watermarks).
    doc = _document(
        track, merged_edges, merged_finish, source_runs,
        # Authored knowledge is never touched by a survey run: a driver
        # labelling corners and a driver re-driving the borders are two jobs,
        # and the second must not be able to undo the first.
        corners=existing["corners"] if existing else [],
        sections=existing["sections"] if existing else [],
        official=existing["meta"]["official"] if existing else None,
    )
    meta = _write(data_dir, track, doc)
    log.info("track bundle saved: %s (%d points, %d runs)",
             bundle_path(data_dir, track).name, len(merged_edges), meta["runs"])
    return meta


def next_run(data_dir: Path, track: str) -> int:
    """The ordinal a run starting now will hold on this circuit's bundle.

    Per source: another installation's run 9 says nothing about what this
    installation should call its next run.
    """
    doc = load(data_dir, track)
    if doc is None:
        return 1
    source = source_id(data_dir)
    counted: int = doc["meta"]["source_runs"].get(source, 0)
    # Defensively against the evidence too, not just the counter: a bundle
    # written by a build that predates reconcile_runs — or one repaired by
    # hand — can still hold votes above it, and reusing that ordinal would
    # make this run's agreement invisible.
    return max(counted, watermarks(doc["edges"]).get(source, 0)) + 1


def stats(doc: dict[str, Any]) -> dict[str, Any]:
    """Everything the management view grades a bundle on (#46)."""
    edges = doc["edges"]
    with_y = sum(1 for e in edges if e.get("y") is not None)
    manual = sum(1 for e in edges if e["kind"] in MANUAL_KINDS)
    return {
        "points": len(edges),
        # Elevation only fills in by re-driving: a bundle first built before
        # v3 sits near 0% until its metres are driven again, and that is a
        # fact about the data the view has to be able to say out loud.
        "elevation_points": with_y,
        "elevation_pct": round(100 * with_y / len(edges), 1) if edges else 0.0,
        "manual_points": manual,
        "finish_crossings": len(doc["finish_crossings"]),
        "corners": len(doc["corners"]),
        "sections": len(doc["sections"]),
        "sources": len(doc["meta"]["source_runs"]),
    }


def list_bundles(data_dir: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    directory = data_dir / BUNDLE_DIR
    if not directory.is_dir():
        return out
    for path in sorted(directory.glob("*.json")):
        # Through load(), so older files report their post-upgrade cell count
        # rather than a point count the app would never show again.
        doc = load_slug(data_dir, path.stem)
        if doc is None:
            continue
        out.append({**doc["meta"], "slug": path.stem, **stats(doc)})
    return out


# --- authored corners and sections (#48) --------------------------------------


def set_authored(
    data_dir: Path,
    track: str,
    corners: list[dict[str, Any]] | None = None,
    sections: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Replace the hand-labelled corners/sections of an existing bundle.

    Returns None when there is no bundle for `track`: corners are anchored to
    world positions on a surveyed map, so authoring them for a circuit that
    was never surveyed would produce coordinates with nothing to check them
    against.
    """
    doc = load(data_dir, track)
    if doc is None:
        return None
    if corners is not None:
        doc["corners"] = renumber(corners)
    if sections is not None:
        doc["sections"] = renumber(sections)
    doc["meta"]["updated_at"] = datetime.now(UTC).isoformat()
    _write(data_dir, doc["meta"]["track"], doc)
    return doc


def renumber(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Corner 1 is the first after the start line — the client orders them."""
    return [{**item, "n": i + 1} for i, item in enumerate(items)]


def set_official(data_dir: Path, track: str, official: dict[str, Any] | None) -> bool:
    doc = load(data_dir, track)
    if doc is None:
        return False
    doc["meta"]["official"] = official
    doc["meta"]["updated_at"] = datetime.now(UTC).isoformat()
    _write(data_dir, doc["meta"]["track"], doc)
    return True


# --- import / merge / housekeeping (#47) --------------------------------------


class BundleError(ValueError):
    """A document that must not be merged, with the reason a human needs."""


def _number(value: Any, field: str, limit: float = 1e7) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BundleError(f"{field} must be a number")
    number = float(value)
    if not math.isfinite(number):
        # json.loads happily parses NaN/Infinity literals, and one of them
        # anywhere in a border poisons every bounding box drawn from it.
        raise BundleError(f"{field} must be finite")
    if abs(number) > limit:
        raise BundleError(f"{field} is out of range")
    return number


def _integer(value: Any, field: str, limit: float = 1e7) -> int:
    """A whole number, refused rather than truncated.

    `int(1.9)` silently becoming 1 is the kind of quiet repair that makes a
    malformed document look like a well-formed one.
    """
    number = _number(value, field, limit)
    if number != int(number):
        raise BundleError(f"{field} must be a whole number")
    return int(number)


def _text(value: Any, field: str, limit: int) -> str:
    if not isinstance(value, str):
        raise BundleError(f"{field} must be a string")
    if len(value) > limit:
        raise BundleError(f"{field} is longer than {limit} characters")
    return value


def _validate_votes(raw: Any, where: str) -> Votes:
    if not isinstance(raw, dict) or not raw:
        raise BundleError(f"{where}: votes must be a non-empty object")
    votes: Votes = {}
    for kind, sources in raw.items():
        if kind not in KIND_ORDER:
            raise BundleError(f"{where}: unknown vote kind {kind!r}")
        if not isinstance(sources, dict) or not sources:
            raise BundleError(f"{where}: votes.{kind} must be a non-empty object")
        bucket: dict[str, list[int]] = {}
        for source, entry in sources.items():
            if not _SOURCE_RE.match(str(source)):
                raise BundleError(f"{where}: {source!r} is not a source id")
            if not isinstance(entry, list) or len(entry) != 2:
                raise BundleError(f"{where}: votes.{kind}.{source} must be [count, run]")
            count = _integer(entry[0], f"{where}: vote count", limit=1e6)
            run = _integer(entry[1], f"{where}: vote run", limit=1e6)
            # A source casts at most ONE vote per kind per run, so a count
            # above its own run ordinal is not evidence — it is a claim no
            # amount of driving could produce. Left unchecked, a single
            # imported record saying [1000000, 1] outvotes every hand mark at
            # that metre, in a tier system whose whole premise is that the
            # counts are a census. (Run 0 is the v1 upgrade's stamp, which
            # legitimately carries one vote.)
            if count < 1 or run < 0 or count > max(run, 1):
                raise BundleError(
                    f"{where}: votes.{kind}.{source} = [{count}, {run}] is not a "
                    "possible vote — count must be 1..run"
                )
            bucket[str(source)] = [count, run]
        votes[kind] = bucket
    return votes


def _validate_legacy_votes(raw: Any, where: str) -> dict[str, list[int]]:
    """v2/v3 votes: `[count, last_run]` with no source. `_upgrade` names one."""
    if not isinstance(raw, dict) or not raw:
        raise BundleError(f"{where}: votes must be a non-empty object")
    votes: dict[str, list[int]] = {}
    for kind, entry in raw.items():
        if kind not in KIND_ORDER:
            raise BundleError(f"{where}: unknown vote kind {kind!r}")
        if not isinstance(entry, list) or len(entry) != 2:
            raise BundleError(f"{where}: votes.{kind} must be [count, run]")
        count = int(_number(entry[0], f"{where}: vote count", limit=1e6))
        run = int(_number(entry[1], f"{where}: vote run", limit=1e6))
        if count < 1 or run < 0:
            raise BundleError(f"{where}: votes.{kind} is out of range")
        votes[kind] = [count, run]
    return votes


def _validate_edge(raw: Any, index: int, version: int) -> dict[str, Any]:
    """One border record, rebuilt from checked values in its own version's shape.

    The vote shape is the thing that changed across versions, so it is
    validated per version and left for `_upgrade` to convert — checking a v2
    file against the v4 shape would reject every bundle anyone exported
    before this build, which is the opposite of what import is for.
    """
    where = f"edge {index}"
    if not isinstance(raw, dict):
        raise BundleError(f"{where} is not an object")
    if raw.get("side") not in ("L", "R"):
        raise BundleError(f"{where}: side must be 'L' or 'R'")
    y = raw.get("y")
    tw = raw.get("tw")
    edge: dict[str, Any] = {
        "x": _number(raw.get("x"), f"{where}: x"),
        "z": _number(raw.get("z"), f"{where}: z"),
        "y": None if y is None else _number(y, f"{where}: y"),
        "hx": _number(raw.get("hx", 0.0), f"{where}: hx", limit=1.0001),
        "hz": _number(raw.get("hz", 0.0), f"{where}: hz", limit=1.0001),
        "side": raw["side"],
        "run": int(_number(raw.get("run", 0), f"{where}: run", limit=1e6)),
        "tw": None if tw is None else _number(tw, f"{where}: tw", limit=10.0),
    }
    if version < 2:
        # v1 had no votes at all: the kind IS the record, and the upgrade
        # turns co-located kinds into votes on one metre.
        if raw.get("kind") not in KIND_ORDER:
            raise BundleError(f"{where}: unknown kind {raw.get('kind')!r}")
        edge["kind"] = raw["kind"]
    elif version < 4:
        edge["votes"] = _validate_legacy_votes(raw.get("votes"), where)
        edge["kind"] = raw.get("kind") if raw.get("kind") in KIND_ORDER else "auto"
    else:
        votes = _validate_votes(raw.get("votes"), where)
        edge["votes"] = votes
        edge["kind"] = resolve_kind(votes)  # recomputed, never trusted from the file
    return edge


def _validate_finish(raw: Any, index: int) -> dict[str, float]:
    where = f"finish crossing {index}"
    if not isinstance(raw, dict):
        raise BundleError(f"{where} is not an object")
    return {
        "x": _number(raw.get("x"), f"{where}: x"),
        "z": _number(raw.get("z"), f"{where}: z"),
        "hx": _number(raw.get("hx", 0.0), f"{where}: hx", limit=1.0001),
        "hz": _number(raw.get("hz", 0.0), f"{where}: hz", limit=1.0001),
        "lap": _number(raw.get("lap", 0), f"{where}: lap", limit=1e6),
    }


def _point(raw: Any, field: str) -> dict[str, float]:
    if not isinstance(raw, dict):
        raise BundleError(f"{field} must be an {{x, z}} point")
    return {"x": _number(raw.get("x"), f"{field}.x"), "z": _number(raw.get("z"), f"{field}.z")}


def validate_corner(raw: Any, index: int) -> dict[str, Any]:
    where = f"corner {index}"
    if not isinstance(raw, dict):
        raise BundleError(f"{where} is not an object")
    direction = raw.get("direction")
    if direction not in (None, "", "L", "R"):
        raise BundleError(f"{where}: direction must be 'L', 'R' or null")
    corner: dict[str, Any] = {
        "n": int(_number(raw.get("n", index + 1), f"{where}: n", limit=MAX_CORNERS)),
        "name": _text(raw.get("name", ""), f"{where}: name", MAX_LABEL),
        "direction": direction or None,
        # Anchored to a POSITION, not a lap distance: distance depends on the
        # racing line taken, so a corner pinned at 1,240 m on one lap is
        # somewhere else on the next (#48).
        "apex": _point(raw.get("apex"), f"{where}: apex"),
        "entry": None if raw.get("entry") is None else _point(raw["entry"], f"{where}: entry"),
        "exit": None if raw.get("exit") is None else _point(raw["exit"], f"{where}: exit"),
        "note": _text(raw.get("note", ""), f"{where}: note", MAX_LABEL * 4),
    }
    return corner


def validate_section(raw: Any, index: int) -> dict[str, Any]:
    where = f"section {index}"
    if not isinstance(raw, dict):
        raise BundleError(f"{where} is not an object")
    return {
        "n": int(_number(raw.get("n", index + 1), f"{where}: n", limit=MAX_SECTIONS)),
        "name": _text(raw.get("name", ""), f"{where}: name", MAX_LABEL),
        "start": _point(raw.get("start"), f"{where}: start"),
        "end": _point(raw.get("end"), f"{where}: end"),
    }


def validate_official(raw: Any) -> dict[str, Any] | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise BundleError("meta.official must be an object or null")
    return {
        "track": _text(raw.get("track", ""), "official.track", MAX_TRACK_NAME),
        "layout": _text(raw.get("layout", ""), "official.layout", MAX_TRACK_NAME),
        "official_id": _text(raw.get("official_id", ""), "official.official_id", 32),
        "official_name": _text(
            raw.get("official_name", ""), "official.official_name", MAX_TRACK_NAME * 2
        ),
        "turns": int(_number(raw.get("turns", 0), "official.turns", limit=1000)),
        "length_m": _number(raw.get("length_m", 0), "official.length_m", limit=1e6),
        "reverse": bool(raw.get("reverse", False)),
    }


def validate_document(raw: Any) -> dict[str, Any]:
    """Parse an untrusted bundle document into one that is safe to merge.

    An imported bundle writes into the same store the app surveys into, so
    nothing here is taken on trust: every field is rebuilt from checked
    values and unknown keys are dropped, rather than the document being
    waved through once its `format` string looks right (#47). Older versions
    are accepted and upgraded — refusing a friend's v2 file would be a
    strange way to make bundles shareable.
    """
    if not isinstance(raw, dict):
        raise BundleError("not a bundle document")
    if raw.get("format") != BUNDLE_FORMAT:
        raise BundleError(f"not a {BUNDLE_FORMAT} document")
    version = raw.get("version")
    if not isinstance(version, int) or version < 1:
        raise BundleError("missing or invalid format version")
    if version > BUNDLE_VERSION:
        raise BundleError(
            f"bundle is format v{version}; this build reads v{BUNDLE_VERSION} — upgrade first"
        )
    meta = raw.get("meta")
    if not isinstance(meta, dict):
        raise BundleError("missing meta")
    track = _text(meta.get("track", ""), "meta.track", MAX_TRACK_NAME).strip()
    if not track:
        raise BundleError("meta.track is empty — a bundle has to say which circuit it is")
    edges_raw = raw.get("edges")
    if not isinstance(edges_raw, list):
        raise BundleError("edges must be a list")
    if len(edges_raw) > MAX_POINTS:
        raise BundleError(f"edges: {len(edges_raw)} points exceeds the {MAX_POINTS} cap")
    finish_raw = raw.get("finish_crossings") or []
    if not isinstance(finish_raw, list):
        raise BundleError("finish_crossings must be a list")
    if len(finish_raw) > MAX_FINISH_CROSSINGS:
        raise BundleError("too many finish crossings")
    corners_raw = raw.get("corners") or []
    sections_raw = raw.get("sections") or []
    if not isinstance(corners_raw, list) or len(corners_raw) > MAX_CORNERS:
        raise BundleError(f"corners must be a list of at most {MAX_CORNERS}")
    if not isinstance(sections_raw, list) or len(sections_raw) > MAX_SECTIONS:
        raise BundleError(f"sections must be a list of at most {MAX_SECTIONS}")

    doc: dict[str, Any] = {
        "format": BUNDLE_FORMAT,
        "version": version,
        "meta": {
            "track": track,
            "runs": int(_number(meta.get("runs", 0) or 0, "meta.runs", limit=1e6)),
            "updated_at": _text(meta.get("updated_at", ""), "meta.updated_at", 64),
            "official": validate_official(meta.get("official")),
        },
        "edges": [_validate_edge(e, i, version) for i, e in enumerate(edges_raw)],
        "finish_crossings": [_validate_finish(c, i) for i, c in enumerate(finish_raw)],
        "corners": renumber([validate_corner(c, i) for i, c in enumerate(corners_raw)]),
        "sections": renumber([validate_section(s, i) for i, s in enumerate(sections_raw)]),
    }
    if version >= 4:
        runs_raw = meta.get("source_runs")
        if not isinstance(runs_raw, dict):
            raise BundleError("meta.source_runs missing on a v4 bundle")
        source_runs: dict[str, int] = {}
        for source, count in runs_raw.items():
            if not _SOURCE_RE.match(str(source)):
                raise BundleError(f"meta.source_runs: {source!r} is not a source id")
            runs = _integer(count, f"meta.source_runs.{source}", limit=1e6)
            if runs < 0:
                raise BundleError(f"meta.source_runs.{source} cannot be negative")
            source_runs[str(source)] = runs
        # The counters are declared authoritative, so they have to cover the
        # evidence: a source that voted at run 9 but is absent here — or
        # listed at 3 — would hand its next run an ordinal whose votes are
        # already present, and that run's agreement would merge in as nothing.
        doc["meta"]["source_runs"] = reconcile_runs(source_runs, doc["edges"])
        doc["meta"]["runs"] = sum(doc["meta"]["source_runs"].values())
    else:
        # Pre-v4 evidence is anonymous, and it is NOT ours: attributing it to
        # this installation would make the sender's runs collide with our own
        # ordinals on the very next survey. It gets an id derived from the
        # document instead, which is stable across re-imports of the same file.
        doc = _upgrade(doc, version, _foreign_source(doc), "imported bundle")
    return doc


def _foreign_source(doc: dict[str, Any]) -> str:
    """A stable synthetic source id for a pre-v4 bundle from elsewhere.

    Derived from the track name and the document's own contents, so
    re-importing the same file twice merges idempotently instead of counting
    the stranger's evidence a second time under a fresh id.

    Every record feeds the hash, not a leading sample: two people's bundles of
    one circuit are distinguished by the metres they each happened to map, and
    truncating the seed is exactly what would let two of them collide into a
    single "source" whose overlapping votes are then dropped as duplicates.
    Hashing 5,000 records costs a few milliseconds, once, per import.
    """
    digest = hashlib.sha256()
    digest.update(doc["meta"]["track"].encode("utf-8", "replace"))
    digest.update(str(doc["meta"]["runs"]).encode("ascii"))
    for edge in doc["edges"]:
        digest.update(f"|{edge['x']},{edge['z']},{edge['side']},{edge['kind']}"
                      .encode("ascii", "replace"))
    return digest.hexdigest()[:SOURCE_ID_CHARS]


def merge_document(
    data_dir: Path, doc: dict[str, Any], track: str | None = None
) -> dict[str, Any]:
    """Merge a validated document into this installation's bundle for a track.

    `track` overrides the document's own label, which is how a near-miss name
    ("Lago Maggiore - East" vs "- East End") is collapsed onto the right
    circuit instead of living on as a second bundle (#46).
    """
    name = (track or doc["meta"]["track"]).strip()
    if not name:
        raise BundleError("no track to merge into")
    existing = load(data_dir, name)
    before = len(existing["edges"]) if existing else 0
    edges = merge_edges(existing["edges"] if existing else [], doc["edges"])
    finish = merge_finish(
        existing["finish_crossings"] if existing else [], doc["finish_crossings"]
    )
    source_runs = dict(existing["meta"]["source_runs"]) if existing else {}
    for source, count in doc["meta"]["source_runs"].items():
        # Each source's own highest run count wins. Adding them would count
        # the same runs again every time the same bundle is re-imported.
        source_runs[source] = max(source_runs.get(source, 0), count)
    source_runs = reconcile_runs(source_runs, edges)
    # Authored data is never overwritten by an import: someone else's corner
    # numbering replacing yours silently is the one outcome nobody wants back.
    corners = existing["corners"] if existing and existing["corners"] else doc["corners"]
    sections = existing["sections"] if existing and existing["sections"] else doc["sections"]
    official = (existing["meta"]["official"] if existing else None) or doc["meta"]["official"]
    merged = _document(name, edges, finish, source_runs, corners, sections, official)
    meta = _write(data_dir, name, merged)
    log.info("merged bundle into %r: %d -> %d cells, %d sources",
             name, before, len(edges), len(source_runs))
    return {
        **meta,
        "slug": slugify(name),
        "added_points": len(edges) - before,
        "corners_kept": bool(existing and existing["corners"] and doc["corners"]),
        **stats(merged),
    }


def delete(data_dir: Path, slug: str) -> bool:
    path = data_dir / BUNDLE_DIR / f"{slug}.json"
    if not path.exists():
        return False
    path.unlink()
    log.info("deleted track bundle %s", path.name)
    return True


def rename(data_dir: Path, slug: str, new_track: str) -> dict[str, Any]:
    """Re-label a bundle, merging into the target when one already exists."""
    doc = load_slug(data_dir, slug)
    if doc is None:
        raise BundleError("no bundle for this track")
    name = new_track.strip()
    if not name:
        raise BundleError("track name cannot be blank")
    result = merge_document(data_dir, doc, track=name)
    if slugify(name) != slug:
        delete(data_dir, slug)
    return result
