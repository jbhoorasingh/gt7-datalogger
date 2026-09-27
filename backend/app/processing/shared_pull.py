"""Pulling bundles from the shared repo into this installation (#47).

`shared_repo` knows how to read the index and fetch documents; this is what a
pull DOES with them: validate the bundle and the repo's corrections for it,
merge the bundle through the normal voting merge, and keep the corrections as
this installation's copy. One place for it, because three callers need it —
pulling one circuit from the Tracks view, pulling every circuit the repo
offers, and the first start of a fresh installation, which does the latter
unasked so a new logger names and draws the known circuits from its first lap.

Re-pulling is safe: the merge counts a stranger's runs once however often
they arrive, so pulling everything again only picks up what changed.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from app.processing import shared_repo, track_bundle, track_corrections

log = logging.getLogger(__name__)

# The same cap a hand-imported file gets: a pulled bundle is an import.
MAX_IMPORT_BYTES = 64 * 1024 * 1024


async def fetch_index(index_url: str) -> list[dict[str, Any]]:
    """The repo's bundle entries, each with the slug it pulls into."""
    raw = await shared_repo.fetch_json(index_url, shared_repo.MAX_INDEX_BYTES)
    entries = shared_repo.validate_index(raw)
    for entry in entries:
        entry["slug"] = track_bundle.slugify(entry["track"])
    return entries


async def pull_entry(
    data_dir: Path, index_url: str, entry: dict[str, Any], track: str | None = None
) -> dict[str, Any]:
    """Pull one index entry: its bundle, merged, and the repo's corrections.

    Raises `BundleError` for anything the repo serves wrong and
    `httpx.HTTPError` when it cannot be reached. The corrections are fetched
    and checked BEFORE the merge, so a repo serving a bad corrections file
    changes nothing here rather than half of it. They replace the copy held
    here rather than merging with it — they are the repo's decision about the
    evidence, not evidence — and a circuit the repo lists without corrections
    loses whatever copy was here.
    """
    bundle_url = shared_repo.resolve_url(index_url, entry["url"])
    payload = await shared_repo.fetch_json(bundle_url, MAX_IMPORT_BYTES)
    doc = track_bundle.validate_document(payload)
    corrected: dict[str, Any] | None = None
    if entry.get("corrections"):
        corrections_url = shared_repo.resolve_url(index_url, entry["corrections"])
        corrected = track_corrections.validate(
            await shared_repo.fetch_json(corrections_url, MAX_IMPORT_BYTES)
        )
        official_id = str((doc["meta"].get("official") or {}).get("official_id") or "")
        if official_id and corrected["official_id"] != official_id:
            raise track_bundle.BundleError(
                f"corrections are for layout {corrected['official_id']!r}, "
                f"the bundle is {official_id!r}"
            )
    result = await asyncio.to_thread(track_bundle.merge_document, data_dir, doc, track)
    if corrected is None or track_corrections.is_empty(corrected):
        track_corrections.remove(data_dir, result["slug"])
        result["corrections"] = None
    else:
        track_corrections.write(data_dir, result["slug"], corrected)
        result["corrections"] = track_corrections.summary(corrected)
    return result


async def pull_all(
    data_dir: Path, index_url: str, on_merged: Callable[[str], None]
) -> dict[str, Any]:
    """Pull every bundle the repo offers, one at a time.

    The index must be readable — that failure is raised, since nothing was
    pulled. After that each circuit stands alone: one the repo serves broken
    is reported under `failed` and the rest are still pulled. `on_merged` is
    called with each merged circuit's track name, for the caller's
    bundle-changed bookkeeping (corner cache, sync queue, re-judging).
    """
    entries = await fetch_index(index_url)
    pulled: list[dict[str, Any]] = []
    failed: list[dict[str, str]] = []
    for entry in entries:
        try:
            result = await pull_entry(data_dir, index_url, entry)
        except (track_bundle.BundleError, httpx.HTTPError) as exc:
            log.warning("shared bundle %s not pulled: %s", entry["slug"], exc)
            failed.append({"slug": entry["slug"], "track": entry["track"], "error": str(exc)})
            continue
        on_merged(result["track"])
        pulled.append({
            "slug": result["slug"],
            "track": result["track"],
            "added_points": result.get("added_points", 0),
        })
    return {"pulled": pulled, "failed": failed}


def has_bundles(data_dir: Path) -> bool:
    """Whether this installation holds any bundle at all — surveyed, imported
    or pulled. Only the directory listing: no document is read."""
    directory = data_dir / track_bundle.BUNDLE_DIR
    return directory.is_dir() and any(directory.glob("*.json"))
