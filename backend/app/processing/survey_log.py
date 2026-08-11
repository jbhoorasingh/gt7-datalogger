"""Reading survey JSONL logs back — the rescue path for orphaned runs.

A survey with no circuit label saves no bundle at all, so a run that started
before the track was identified exists ONLY as its log: the author lost ~55
minutes of driving that way (#45). The log is a complete record — `mark`
lines carry straddle and manual edges verbatim, transition records carry the
derived wheel-contact points that `auto` edges reconstruct from — so a run
can be assigned to a circuit after the fact and merged through the normal
voting path, indistinguishable from having named it while driving.

This lives in the app (rather than only in scripts/jsonl_to_bundle.py, where
it started) because "which of my runs went nowhere" is a question the track
management view has to be able to answer without the driver knowing that a
JSONL exists, let alone how to run a script against it (#46).
"""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Any

from app.processing import track_bundle

log = logging.getLogger(__name__)

LOG_GLOB = "surface_survey_*.jsonl"

# Line prefixes, as json.dumps writes them with sorted-by-insertion keys. The
# summary scan tests these instead of parsing every line: a long run's log is
# mostly transition records, and listing runs must not cost a full parse of
# every one of them.
_META = '{"meta":'
_TRACK = '{"track":'
_MARK = '{"mark":'
_FINISH = '{"finish":'


def summarize(path: Path) -> dict[str, Any]:
    """What a run's log says about itself, without parsing every record."""
    track = ""
    started_at = ""
    session_id: int | None = None
    width = None
    marks = transitions = finish = 0
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if line.startswith(_META):
                    try:
                        meta = json.loads(line)["meta"]
                    except (ValueError, KeyError):
                        continue
                    track = meta.get("track", "") or track
                    started_at = meta.get("started_at", "") or started_at
                    session_id = meta.get("session_id")
                    width = meta.get("track_width_m")
                elif line.startswith(_TRACK):
                    try:
                        # Identification arriving mid-run: the LAST label the
                        # run carried is the one its evidence went to.
                        track = json.loads(line).get("track") or track
                    except ValueError:
                        continue
                elif line.startswith(_MARK):
                    marks += 1
                elif line.startswith(_FINISH):
                    finish += 1
                else:
                    transitions += 1
    except OSError as exc:
        log.warning("unreadable survey log %s: %s", path, exc)
    return {
        "name": path.name,
        "track": track,
        "started_at": started_at,
        "session_id": session_id,
        "track_width_m": width,
        "marks": marks,
        "transitions": transitions,
        "finish_crossings": finish,
        "bytes": path.stat().st_size if path.exists() else 0,
        # A log with evidence and no label is a run that went nowhere. That is
        # the failure this list exists to make visible; without it the only
        # symptom is a bundle that never appears.
        "orphaned": not track and (marks > 0 or transitions > 0),
    }


def list_logs(data_dir: Path) -> list[dict[str, Any]]:
    if not data_dir.is_dir():
        return []
    return [summarize(p) for p in sorted(data_dir.glob(LOG_GLOB), reverse=True)]


def log_path(data_dir: Path, name: str) -> Path | None:
    """Resolve a log file name from the listing to a path inside data_dir."""
    if "/" in name or "\\" in name or not name.endswith(".jsonl"):
        return None
    path = data_dir / name
    # Belt and braces against traversal: the name is user-supplied and the
    # only thing standing between it and an arbitrary read is this check.
    if path.parent.resolve() != data_dir.resolve() or not path.is_file():
        return None
    return path


def edges_from_log(
    path: Path, run: int, source: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    """Every border sample the log holds, as bundle records."""
    edges: list[dict[str, Any]] = []
    finish: list[dict[str, Any]] = []
    logged_track = ""
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue  # a run killed mid-write leaves one truncated line
        if "meta" in record:
            logged_track = record["meta"].get("track", "") or logged_track
            continue
        if "track" in record and len(record) == 1:
            logged_track = record["track"] or logged_track
            continue
        if "finish" in record:
            finish.append(record["finish"])
            continue
        mark = record.get("mark")
        if isinstance(mark, dict):
            # Straddle and manual edges exist nowhere else — verbatim.
            edges.append(track_bundle.new_edge(
                x=mark["x"], z=mark["z"], hx=mark["hx"], hz=mark["hz"],
                side=mark["side"], kind=mark["kind"], run=run, source=source,
                tw=mark.get("tw"), y=mark.get("y"),
            ))
            continue
        # Transition record: "auto" edges are the changed wheels' contact
        # points, kept only where the transition belongs to one border
        # unambiguously — the same rule the live path applies.
        border = record.get("border")
        contacts = record.get("contacts")
        if not border or not contacts:
            continue
        vel = record.get("vel") or [0.0, 0.0, 0.0]
        norm = math.hypot(vel[0], vel[2])
        if norm <= 0:
            continue
        pos = record.get("pos")
        pos_y = pos[1] if isinstance(pos, list) and len(pos) > 2 else None
        for wheel in record.get("changed", []):
            point = contacts.get(wheel)
            if not point:
                continue
            edges.append(track_bundle.new_edge(
                x=point[0], z=point[1], hx=vel[0] / norm, hz=vel[2] / norm,
                side=border, kind="auto", run=run, source=source,
                tw=record.get("tw_m"), y=pos_y,
            ))
    return edges, finish, logged_track


def assign(data_dir: Path, path: Path, track: str) -> dict[str, Any]:
    """Rebuild a logged run under `track` and merge it into that bundle.

    Counted as a real run of this installation: it is one, it just went to
    its circuit late.
    """
    name = track.strip()
    if not name:
        raise track_bundle.BundleError("a track name is required")
    source = track_bundle.source_id(data_dir)
    run = track_bundle.next_run(data_dir, name)
    edges, finish, logged_track = edges_from_log(path, run, source)
    if not edges and not finish:
        raise track_bundle.BundleError(
            "nothing to recover — no marks, transitions or crossings in that log"
        )
    existing = track_bundle.load(data_dir, name)
    before = len(existing["edges"]) if existing else 0
    meta = track_bundle.save(data_dir, name, edges, finish, count_run=True)
    # Record the label in the log itself, exactly as a live run does when the
    # circuit is identified mid-drive. Without this the run keeps reporting as
    # orphaned after it has been rescued — which reads as "that didn't work"
    # and invites assigning it a second time, merging the same run twice.
    try:
        with path.open("a", encoding="ascii") as handle:
            handle.write(json.dumps({"track": name}, separators=(",", ":")) + "\n")
    except OSError as exc:  # the merge already happened; the log is a record
        log.warning("could not label survey log %s: %s", path.name, exc)
    doc = track_bundle.load(data_dir, name)
    stats = track_bundle.stats(doc) if doc else {}
    return {
        **meta, **stats,
        "slug": track_bundle.slugify(name),
        "log": path.name,
        "logged_track": logged_track,
        "run": run,
        "recovered_points": len(edges),
        "added_points": stats.get("points", before) - before,
    }
