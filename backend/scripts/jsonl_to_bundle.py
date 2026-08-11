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

The Tracks view does the same thing from the browser (it lists orphaned logs
and assigns them); this stays for logs that have been moved off the machine
that recorded them, and for a dry run before committing to a label.

    python scripts/jsonl_to_bundle.py <log.jsonl> <data_dir> "<Track Name>"
    python scripts/jsonl_to_bundle.py <log.jsonl> <data_dir> "<Track>" --apply

Without --apply it reports what it would merge and writes nothing.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.processing import survey_log, track_bundle  # noqa: E402


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
    source = track_bundle.source_id(data_dir)
    run = track_bundle.next_run(data_dir, track)
    edges, finish, logged_track = survey_log.edges_from_log(log, run, source)
    if not edges and not finish:
        print("nothing to import — no marks, transitions or crossings in that log")
        return 1

    before = len(existing["edges"]) if existing else 0
    merged = track_bundle.merge_edges(existing["edges"] if existing else [], edges)
    with_y = sum(1 for e in merged if e.get("y") is not None)
    print(f"log          : {log.name}")
    print(f"logged track : {logged_track or '(none — which is why no bundle was written)'}")
    print(f"assigning to : {track!r}  (run #{run} of source {source})")
    print(f"recovered    : {len(edges)} samples, {len(finish)} finish crossings")
    print(f"bundle cells : {before} -> {len(merged)}  (+{len(merged) - before})")
    print(f"elevation    : {with_y}/{len(merged)} cells")
    if not apply:
        print("\nDRY RUN — nothing written. Re-run with --apply to merge.")
        return 0
    meta = survey_log.assign(data_dir, log, track)
    print(f"\nwrote {track_bundle.bundle_path(data_dir, track)}\n  {meta}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
