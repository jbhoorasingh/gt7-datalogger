"""Rebuild a track bundle from a survey JSONL — assign a run to a track after
the fact.

A survey that ran without a circuit label writes no bundle at all, so an
entire session can end up existing only as its log. That log is a complete
record: `mark` lines carry straddle and manual edges verbatim, and transition
records carry the derived wheel-contact points from which `auto` edges
reconstruct. This replays one into the normal merge path, so the result is
indistinguishable from having named the circuit while driving.

Also the way to fix a mis-labeled run: assigning a new label to a live survey
flushes its evidence to the PREVIOUS circuit first (a circuit change is not a
correction), whereas this can rebuild the run under the right track.

    python scripts/jsonl_to_bundle.py <log.jsonl> <data_dir> "<Track Name>"
    python scripts/jsonl_to_bundle.py <log.jsonl> <data_dir> "<Track>" --apply

Without --apply it reports what it would merge and writes nothing.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.processing import track_bundle  # noqa: E402


def edges_from_log(path: Path, run: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
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
                side=mark["side"], kind=mark["kind"], run=run,
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
                side=border, kind="auto", run=run,
                tw=record.get("tw_m"), y=pos_y,
            ))
    return edges, finish, logged_track


def main(argv: list[str]) -> int:
    if len(argv) < 4:
        print(__doc__)
        return 2
    log, data_dir, track = Path(argv[1]), Path(argv[2]), argv[3].strip()
    apply = "--apply" in argv
    if not log.is_file():
        print(f"no such log: {log}")
        return 1
    if not track:
        print("a track name is required")
        return 1

    existing = track_bundle.load(data_dir, track)
    run = (existing["meta"]["runs"] if existing else 0) + 1
    edges, finish, logged_track = edges_from_log(log, run)
    if not edges and not finish:
        print("nothing to import — no marks, transitions or crossings in that log")
        return 1

    before = len(existing["edges"]) if existing else 0
    merged = track_bundle.merge_edges(existing["edges"] if existing else [], edges)
    with_y = sum(1 for e in merged if e.get("y") is not None)
    print(f"log          : {log.name}")
    print(f"logged track : {logged_track or '(none — which is why no bundle was written)'}")
    print(f"assigning to : {track!r}  (run #{run})")
    print(f"recovered    : {len(edges)} samples, {len(finish)} finish crossings")
    print(f"bundle cells : {before} -> {len(merged)}  (+{len(merged) - before})")
    print(f"elevation    : {with_y}/{len(merged)} cells")
    if not apply:
        print("\nDRY RUN — nothing written. Re-run with --apply to merge.")
        return 0
    meta = track_bundle.save(data_dir, track, edges, finish, count_run=True)
    print(f"\nwrote {track_bundle.bundle_path(data_dir, track)}\n  {meta}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
