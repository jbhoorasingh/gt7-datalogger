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

Votes are counted PER RUN, not per sample: `votes[kind] = [count, last_run]`,
and a merge increments only when the incoming evidence comes from a later
run than the one already counted. That makes saving idempotent — the ~60 s
autosave writes the same run's evidence repeatedly, and without the run
stamp a long session would inflate its own votes by a factor of however many
times it happened to autosave. (Known limitation, for #40's cross-user
merge: run ordinals are local to one installation, so merging two people's
bundles needs a source id before these counts can be trusted across them.)

Points carry the provenance needed to second-guess them later: `run` (the
run ordinal that first evidenced the cell) and `tw` (the axle track width
in use when it was laid). `tw` matters because straddle points — 52% of
Centre, 88% of East End — are placed at ±tw/2 from the car centre, so they
carry the whole width-estimate error; recording it keeps the option of
correcting their lateral offset offline once a better width is known.
Position itself stays first-seen, which keeps files stable (small diffs
once these live in their own repo: the format is self-describing and
versioned precisely so bundles can be exported there and imported at build
time later, like data/tracks.json).

Track width calibration is deliberately NOT in the bundle: it is a property
of the car being driven, not of the circuit.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

BUNDLE_FORMAT = "gt7-datalogger-track-bundle"
BUNDLE_VERSION = 2
BUNDLE_DIR = "track-bundles"
GRID_M = 1.0  # dedup cell: one record per metre per side
MAX_POINTS = 50_000
MAX_FINISH_CROSSINGS = 20

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


def edge_key(e: dict[str, Any]) -> tuple[int, int, str]:
    """One record per metre per side — kind is voted on, not part of identity."""
    return (round(e["x"] / GRID_M), round(e["z"] / GRID_M), e["side"])


def resolve_kind(votes: dict[str, list[int]]) -> str:
    """The kind a cell's votes settle on: manual tier first, then majority."""
    for tier in (MANUAL_KINDS, AUTO_KINDS):
        present = [k for k in tier if votes.get(k)]
        if present:
            return max(present, key=lambda k: (votes[k][0], -KIND_ORDER.index(k)))
    return "auto"  # unreachable for well-formed records; never crash a load


def cast_vote(e: dict[str, Any], kind: str, run: int) -> None:
    """Add one run's vote for `kind`, idempotently within that run."""
    votes = e["votes"]
    prior = votes.get(kind)
    if prior is None:
        votes[kind] = [1, run]
    elif run > prior[1]:
        votes[kind] = [prior[0] + 1, run]
    e["kind"] = resolve_kind(votes)


def new_edge(
    x: float, z: float, hx: float, hz: float, side: str,
    kind: str, run: int, tw: float | None,
) -> dict[str, Any]:
    e: dict[str, Any] = {
        "x": round(x, 3), "z": round(z, 3),
        "hx": round(hx, 5), "hz": round(hz, 5),
        "side": side, "kind": kind,
        "votes": {}, "run": run,
        "tw": round(tw, 3) if tw is not None else None,
    }
    cast_vote(e, kind, run)
    return e


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
            copy = {**e, "votes": {k: list(v) for k, v in e["votes"].items()}}
            index[key] = copy
            merged.append(copy)
            continue
        for kind, (count, last_run) in e["votes"].items():
            prior = cur["votes"].get(kind)
            if prior is None:
                cur["votes"][kind] = [count, last_run]
            elif last_run > prior[1]:
                # One increment per run, whatever the incoming count claims:
                # the two lists are the same cell counted by different paths.
                cur["votes"][kind] = [prior[0] + 1, last_run]
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


def _upgrade_v1(edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
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
                "x": old["x"], "z": old["z"], "hx": old["hx"], "hz": old["hz"],
                "side": old["side"], "kind": old["kind"],
                "votes": {}, "run": 0, "tw": None,
            }
            index[key] = cur
            out.append(cur)
        cast_vote(cur, old["kind"], 0)
    return out


def load(data_dir: Path, track: str) -> dict[str, Any] | None:
    path = bundle_path(data_dir, track)
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
            before = len(doc["edges"])
            doc["edges"] = _upgrade_v1(doc["edges"])
            doc["version"] = BUNDLE_VERSION
            log.info("upgraded track bundle %s v%s -> v%d (%d points -> %d "
                     "voted cells)", path.name, version, BUNDLE_VERSION,
                     before, len(doc["edges"]))
        return doc
    except (ValueError, KeyError, TypeError, OSError) as exc:
        log.warning("unreadable track bundle %s: %s", path, exc)
        return None


def save(
    data_dir: Path,
    track: str,
    edges: list[dict[str, Any]],
    finish_crossings: list[dict[str, float]],
    count_run: bool,
) -> dict[str, Any]:
    """Merge a run's evidence into the circuit's bundle; returns its meta."""
    existing = load(data_dir, track)
    merged_edges = merge_edges(existing["edges"] if existing else [], edges)
    merged_finish = merge_finish(
        existing["finish_crossings"] if existing else [], finish_crossings
    )
    runs = (existing["meta"]["runs"] if existing else 0) + (1 if count_run else 0)
    meta: dict[str, Any] = {
        "track": track,
        "runs": runs,
        "updated_at": datetime.now(UTC).isoformat(),
    }
    doc: dict[str, Any] = {
        "format": BUNDLE_FORMAT,
        "version": BUNDLE_VERSION,
        "meta": meta,
        "edges": merged_edges,
        "finish_crossings": merged_finish,
    }
    path = bundle_path(data_dir, track)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)  # atomic: a crash never leaves a half-written bundle
    log.info("track bundle saved: %s (%d points, %d runs)",
             path.name, len(merged_edges), runs)
    return {**meta, "points": len(merged_edges)}


def next_run(data_dir: Path, track: str) -> int:
    """The ordinal a run starting now will hold on this circuit's bundle."""
    doc = load(data_dir, track)
    return (doc["meta"]["runs"] if doc else 0) + 1


def list_bundles(data_dir: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    directory = data_dir / BUNDLE_DIR
    if not directory.is_dir():
        return out
    for path in sorted(directory.glob("*.json")):
        # Through load(), so v1 files report their post-upgrade cell count
        # rather than a point count the app would never show again.
        doc = load(directory.parent, path.stem)
        if doc is None:
            continue
        out.append({
            **doc["meta"],
            "slug": path.stem,
            "points": len(doc.get("edges", [])),
            "finish_crossings": len(doc.get("finish_crossings", [])),
        })
    return out
