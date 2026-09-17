"""Whole-session export archives (#76).

Exporting laps one at a time made backing up or sharing a session a matter
of clicking through it. A session archive is a ZIP of the per-lap export
documents — byte-for-byte the `gt7-datalogger-lap` files that
GET /api/laps/{id}/export serves, and so exactly what POST /api/laps/import
already accepts — plus a `session.json` saying what they belong to:

    gt7-session-12/
      session.json            format, version, the session row, a lap index
      laps/lap-001-345.json   lap number 1, lap id 345
      laps/lap-002-346.json

The session row is the one GET /api/sessions lists: car, circuit, tags, note,
the bests exclusion and the race result. Laps are named by number first so a
file browser lists them in driving order, and by id as well because a session
can hold two laps with one number (GT7 re-reporting a lap after a rewind).
"""

from __future__ import annotations

import asyncio
import json
import zipfile
from datetime import UTC, datetime
from typing import IO, Any

from app.processing.laps import decode_samples
from app.storage.repository import EXPORT_VERSION, Repository

SESSION_FORMAT = "gt7-datalogger-session"
SESSION_EXPORT_VERSION = 1


def archive_name(session_id: int) -> str:
    return f"gt7-session-{session_id}.zip"


def _write_lap(zf: zipfile.ZipFile, name: str, lap: dict[str, Any], samples_json: str) -> None:
    """Assemble and compress one lap document. Run off the event loop: a
    lap's samples are hundreds of kilobytes to parse, serialize and deflate."""
    doc = {
        "format": "gt7-datalogger-lap",
        "version": EXPORT_VERSION,
        "lap": {**lap, "samples": decode_samples(samples_json)},
    }
    # The same encoding FastAPI uses for the single-lap export, so the file
    # in the archive is the file that endpoint would have served.
    zf.writestr(
        name, json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode()
    )


async def write_session_archive(repo: Repository, session_id: int, out: IO[bytes]) -> bool:
    """Write the session's archive to `out`. False when there is no such session.

    Laps are read and written one at a time, so memory holds a single lap's
    samples however long the session ran; the compressed archive goes to
    `out`, which the caller spools to disk past a size it is happy to hold.
    """
    session = await repo.get_session(session_id)
    if session is None:
        return False
    folder = archive_name(session_id).removesuffix(".zip")
    laps = sorted(await repo.list_laps(session_id), key=lambda lap: (lap["number"], lap["id"]))
    index: list[dict[str, Any]] = []
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for summary in laps:
            lap = await repo.get_lap(summary["id"], with_samples=False)
            samples_json = await repo.lap_samples_json(summary["id"])
            if lap is None or samples_json is None:
                continue  # deleted while the archive was being written
            name = f"laps/lap-{lap['number']:03d}-{lap['id']}.json"
            await asyncio.to_thread(_write_lap, zf, f"{folder}/{name}", lap, samples_json)
            index.append(
                {
                    "file": name,
                    "id": lap["id"],
                    "number": lap["number"],
                    "time_ms": lap["time_ms"],
                    "counts_for_best": lap["counts_for_best"],
                }
            )
        manifest = {
            "format": SESSION_FORMAT,
            "version": SESSION_EXPORT_VERSION,
            "exported_at": datetime.now(UTC).isoformat(),
            "session": session,
            "laps": index,
        }
        zf.writestr(
            f"{folder}/session.json",
            json.dumps(manifest, ensure_ascii=False, indent=2).encode(),
        )
    return True
