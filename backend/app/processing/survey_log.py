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

import hashlib
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
    source = ""
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
                    source = str(meta.get("source", "") or "")
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
        "source": source,
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


class LogFormatError(ValueError):
    """The file is not a survey JSONL log."""


# Present in the header since the first writer (survey.start). `source` and
# `wheels` came later, so requiring them would refuse genuine old logs; a
# track bundle's meta has NEITHER of these, so two keys already tell the
# documents apart.
_HEADER_KEYS = ("started_at", "track_width_m")

# The keys `edges_from_log` bracket-accesses on a mark. A mark missing one
# uploads fine under any looser gate and then raises KeyError at assign time.
_MARK_KEYS = ("x", "z", "hx", "hz", "side", "kind")


def _header_ok(record: Any) -> bool:
    return (
        isinstance(record, dict)
        and isinstance(record.get("meta"), dict)
        and all(k in record["meta"] for k in _HEADER_KEYS)
    )


def _record_ok(record: Any) -> bool:
    """Would `edges_from_log` cross this record without raising?

    Mirrors that function's accesses exactly — its bracket-lookups define the
    required shape, its .get() defaults define the tolerance — so the two
    cannot drift apart without this file changing in both places. Unknown
    record types pass, because the reader skips them.
    """
    if not isinstance(record, dict):
        return False
    if "meta" in record:
        return isinstance(record["meta"], dict)
    if "track" in record and len(record) == 1:
        return True
    if "finish" in record:
        return True
    mark = record.get("mark")
    if isinstance(mark, dict):
        return all(k in mark for k in _MARK_KEYS)
    # Transition shape. The reader only touches vel/contacts/changed when
    # border and contacts are both truthy, so anything else is a skip.
    contacts = record.get("contacts")
    if not record.get("border") or not contacts:
        return True
    if not isinstance(contacts, dict):
        return False
    vel = record.get("vel")
    if vel and not (
        isinstance(vel, list)
        and len(vel) >= 3
        and all(isinstance(v, int | float) for v in vel)
    ):
        return False
    if not isinstance(record.get("changed", []), list):
        return False
    # Truthy contact points get indexed [0]/[1] by the reader.
    return all(
        not point
        or (isinstance(point, list) and len(point) >= 2
            and all(isinstance(v, int | float) for v in point[:2]))
        for point in contacts.values()
    )


def validate_log(path: Path) -> None:
    """Refuse a file that is not a survey JSONL log, before it can list.

    Every line must parse — the reader tolerates a truncated line because a
    crash mid-write leaves one, but an UPLOAD with a malformed line is a
    wrong or damaged file, and accepting it means silently dropping records
    at assign time. Raises LogFormatError with the offending line number.
    """
    lines = 0
    try:
        with path.open("r", encoding="utf-8") as handle:
            for lines, line in enumerate(handle, start=1):
                try:
                    record = json.loads(line)
                except ValueError as exc:
                    raise LogFormatError(f"line {lines} is not valid JSON") from exc
                if lines == 1:
                    if not _header_ok(record):
                        raise LogFormatError(
                            "first line is not a survey log header"
                        )
                elif not _record_ok(record):
                    raise LogFormatError(
                        f"line {lines} is not a survey log record"
                    )
    except UnicodeDecodeError as exc:
        raise LogFormatError("not UTF-8 text") from exc
    if lines == 0:
        raise LogFormatError("empty file")


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


def source_for(summary: dict[str, Any], path: Path) -> str:
    """Whose evidence a logged run is — the machine that DROVE it.

    Not the machine replaying it. Logs travel (that is what the CLI is for),
    and stamping the destination would let the same physical run be counted
    twice if the machine that recorded it ever contributes its own bundle.
    Logs written before the id was recorded get a synthetic one derived from
    the file, which is stable across replays of the same log and distinct
    from any real installation's.
    """
    recorded = summary.get("source") or ""
    if track_bundle.is_source_id(recorded):
        return recorded
    seed = f"{path.name}|{summary.get('started_at', '')}|{summary.get('bytes', 0)}"
    return "0" + hashlib.sha256(seed.encode("utf-8", "replace")).hexdigest()[
        : track_bundle.SOURCE_ID_CHARS - 1
    ]


def assign(data_dir: Path, path: Path, track: str) -> dict[str, Any]:
    """Rebuild a logged run under `track` and merge it into that bundle.

    Counted as a real run of whoever drove it: it is one, it just went to its
    circuit late.
    """
    name = track.strip()
    if not name:
        raise track_bundle.BundleError("a track name is required")
    summary = summarize(path)
    source = source_for(summary, path)
    doc = track_bundle.load(data_dir, name)
    run = max(
        doc["meta"]["source_runs"].get(source, 0) if doc else 0,
        track_bundle.watermarks(doc["edges"]).get(source, 0) if doc else 0,
    ) + 1
    edges, finish, logged_track = edges_from_log(path, run, source)
    if not edges and not finish:
        raise track_bundle.BundleError(
            "nothing to recover — no marks, transitions or crossings in that log"
        )
    # Label the log BEFORE merging. This marker is the only durable record
    # that the run has been rescued, and a log can be read-only even when the
    # bundle directory is not (a file copied off another machine, say). Doing
    # it afterwards and swallowing the failure reports success while leaving
    # the run listed as orphaned — so the obvious retry merges it twice.
    try:
        with path.open("a", encoding="ascii") as handle:
            handle.write(json.dumps({"track": name}, separators=(",", ":")) + "\n")
    except OSError as exc:
        raise track_bundle.BundleError(
            f"cannot record the assignment in {path.name} ({exc}) — refusing to "
            "merge, because a run that cannot be marked as rescued would be "
            "offered for rescue again"
        ) from exc

    before = len(doc["edges"]) if doc else 0
    meta = track_bundle.save(data_dir, name, edges, finish, count_run=True,
                             source=source)
    doc = track_bundle.load(data_dir, name)
    stats = track_bundle.stats(doc) if doc else {}
    return {
        **meta, **stats,
        "slug": track_bundle.slugify(name),
        "log": path.name,
        "logged_track": logged_track,
        "run": run,
        "source": source,
        "recovered_points": len(edges),
        "added_points": stats.get("points", before) - before,
    }
