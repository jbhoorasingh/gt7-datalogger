"""Per-track survey bundles: track knowledge that outlives a run.

A survey run is ephemeral (restarts, new sessions), but the track isn't —
its perimeters and finish line don't move. Every run's border evidence is
merged into one JSON document per circuit under data/track-bundles/, and
every new run on that circuit starts from it: the map opens with everything
ever mapped and improves from there.

Merging dedups on a 1 m grid per (side, kind), so re-driving the same edges
does not grow the file — it converges. First-seen points win, keeping the
files stable (small diffs once these live in their own repo: the format is
self-describing and versioned precisely so bundles can be exported there
and imported at build time later, like data/tracks.json).

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
BUNDLE_VERSION = 1
BUNDLE_DIR = "track-bundles"
GRID_M = 1.0  # dedup cell: one point per meter per (side, kind)
MAX_POINTS = 50_000
MAX_FINISH_CROSSINGS = 20


def slugify(track: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", track.lower()).strip("-") or "unnamed"


def bundle_path(data_dir: Path, track: str) -> Path:
    return data_dir / BUNDLE_DIR / f"{slugify(track)}.json"


def edge_key(e: dict[str, Any]) -> tuple[int, int, str, str]:
    return (round(e["x"] / GRID_M), round(e["z"] / GRID_M), e["side"], e["kind"])


def merge_edges(
    existing: list[dict[str, Any]], new: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Union on the dedup grid; existing points win (file stability)."""
    merged = list(existing)
    seen = {edge_key(e) for e in existing}
    for e in new:
        key = edge_key(e)
        if key in seen or len(merged) >= MAX_POINTS:
            continue
        seen.add(key)
        merged.append(e)
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


def load(data_dir: Path, track: str) -> dict[str, Any] | None:
    path = bundle_path(data_dir, track)
    if not path.exists():
        return None
    try:
        doc: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        if doc.get("format") != BUNDLE_FORMAT:
            return None
        return doc
    except (ValueError, OSError) as exc:
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


def list_bundles(data_dir: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    directory = data_dir / BUNDLE_DIR
    if not directory.is_dir():
        return out
    for path in sorted(directory.glob("*.json")):
        try:
            doc: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
            if doc.get("format") != BUNDLE_FORMAT:
                continue
            out.append({
                **doc["meta"],
                "slug": path.stem,
                "points": len(doc.get("edges", [])),
                "finish_crossings": len(doc.get("finish_crossings", [])),
            })
        except (ValueError, OSError):
            continue
    return out
