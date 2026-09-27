"""Whole-session export archives (#76), every-lap backups, and the lap CSV.

The every-lap archives are described at stream_all_laps below; this part is
about the session archive.

Exporting laps one at a time made backing up or sharing a session a matter
of clicking through it. A session archive is a ZIP of the per-lap export
documents — byte-for-byte the `gt7-datalogger-lap` files that
GET /api/laps/{id}/export serves, and so exactly what POST /api/laps/import
already accepts — plus a `session.json` saying what they belong to:

    gt7-session-12/
      session.json            format, version, the session row, a lap index
      analysis.json           the lap analysis document (#115), when there is one
      laps/lap-001-345.json   lap number 1, lap id 345
      laps/lap-002-346.json

`analysis.json` is the `gt7-datalogger-lap-analysis` document that
GET /api/sessions/{id}/analysis.json serves: the session measured per corner
against its best lap. It is derived from the laps beside it and nothing
reads it back on import — it is there so that the archive is the whole
session, the figures as well as the series they came from.

The session row is the one GET /api/sessions lists: car, circuit, tags, note,
the bests exclusion and the race result. Laps are named by number first so a
file browser lists them in driving order, and by id as well because a session
can hold two laps with one number (GT7 re-reporting a lap after a rewind).
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import zipfile
from collections.abc import AsyncIterator, Callable
from datetime import UTC, date, datetime
from typing import IO, Any, Literal

from app.processing.laps import decode_samples
from app.storage.repository import EXPORT_VERSION, Repository

SESSION_FORMAT = "gt7-datalogger-session"
SESSION_EXPORT_VERSION = 1


ANALYSIS_FILE = "analysis.json"


def archive_name(session_id: int) -> str:
    return f"gt7-session-{session_id}.zip"


def analysis_name(session_id: int) -> str:
    """What the lap analysis document is called when downloaded by itself."""
    return f"gt7-session-{session_id}-analysis.json"


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


async def write_session_archive(
    repo: Repository,
    session_id: int,
    out: IO[bytes],
    analysis: dict[str, Any] | None = None,
) -> bool:
    """Write the session's archive to `out`. False when there is no such session.

    `analysis` is the session's lap analysis document, compiled by the
    caller (it needs the circuit's corners, which the repository knows
    nothing of); None leaves the file out.

    Laps are read and written one at a time, so memory holds a single lap's
    samples however long the session ran; the compressed archive goes to
    `out`, which the caller spools to disk past a size it is happy to hold.
    """
    session = await repo.get_session(session_id)
    if session is None:
        return False
    folder = archive_name(session_id).removesuffix(".zip")
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        index = [entry async for entry in _write_session_laps(zf, repo, session_id, folder)]
        _write_manifest(zf, folder, session, index, analysis)
    return True


async def _write_session_laps(
    zf: zipfile.ZipFile, repo: Repository, session_id: int, folder: str
) -> AsyncIterator[dict[str, Any]]:
    """Write a session's lap files under `folder`, yielding each one's index
    entry as it lands — the bulk export hands the response what the ZIP
    writer produced at each yield, so a long session is not held whole."""
    laps = sorted(await repo.list_laps(session_id), key=lambda lap: (lap["number"], lap["id"]))
    for summary in laps:
        lap = await repo.get_lap(summary["id"], with_samples=False)
        samples_json = await repo.lap_samples_json(summary["id"])
        if lap is None or samples_json is None:
            continue  # deleted while the archive was being written
        name = f"laps/{_lap_stem(lap)}.json"
        await asyncio.to_thread(_write_lap, zf, f"{folder}/{name}", lap, samples_json)
        yield {
            "file": name,
            "id": lap["id"],
            "number": lap["number"],
            "time_ms": lap["time_ms"],
            "counts_for_best": lap["counts_for_best"],
        }


def _write_manifest(
    zf: zipfile.ZipFile,
    folder: str,
    session: dict[str, Any],
    index: list[dict[str, Any]],
    analysis: dict[str, Any] | None,
) -> None:
    manifest: dict[str, Any] = {
        "format": SESSION_FORMAT,
        "version": SESSION_EXPORT_VERSION,
        "exported_at": datetime.now(UTC).isoformat(),
        "session": session,
        "laps": index,
    }
    if analysis is not None:
        zf.writestr(
            f"{folder}/{ANALYSIS_FILE}",
            json.dumps(analysis, ensure_ascii=False, separators=(",", ":")).encode(),
        )
        manifest["analysis"] = ANALYSIS_FILE
    zf.writestr(
        f"{folder}/session.json",
        json.dumps(manifest, ensure_ascii=False, indent=2).encode(),
    )


def _lap_stem(lap: dict[str, Any]) -> str:
    return f"lap-{lap['number']:03d}-{lap['id']}"


# --- every lap at once --------------------------------------------------------
#
# The Settings "Back up" row: every recorded lap in one download, as the
# JSON export documents or as CSV. One session folder per session, laid out
# exactly as that session's own archive is (less analysis.json, which is
# derived, costs a compile per session, and is one click away in Sessions):
#
#     gt7-laps-2026-09-27/
#       gt7-session-12/session.json
#       gt7-session-12/laps/lap-001-345.json
#       gt7-session-14/...
#
# The CSV archive has the same folders holding lap-001-345.csv files and no
# session.json — a spreadsheet user has no use for the JSON beside them.
#
# Unlike a session archive these are streamed as they are written, not
# spooled first: a whole database's worth would otherwise sit in a
# temporary file on a Raspberry Pi's SD card before the first byte left, and
# the browser would show nothing until it had. The cost is that a failure
# part-way ends the download early rather than answering with an error
# status — the ZIP is then missing its central directory, which every unzip
# tool reports, so a broken backup does not pass for a whole one. Memory
# holds one lap's samples and its compressed file at a time.


def bulk_archive_name(kind: Literal["json", "csv"], today: date | None = None) -> str:
    day = (today or datetime.now().date()).isoformat()
    return f"gt7-laps-{day}.zip" if kind == "json" else f"gt7-laps-csv-{day}.zip"


class _ChunkSink:
    """A write-only file for the ZIP writer that the response drains.

    It has no tell() or seek(), which is what tells zipfile to write each
    member's sizes in a data descriptor after it rather than seeking back
    to patch its header — the only way to write a ZIP to a stream.
    """

    def __init__(self) -> None:
        self._chunks: list[bytes] = []

    def write(self, data: bytes, /) -> int:
        self._chunks.append(bytes(data))
        return len(data)

    def flush(self) -> None:
        pass

    def close(self) -> None:
        pass

    def take(self) -> bytes:
        data = b"".join(self._chunks)
        self._chunks.clear()
        return data


async def _sessions(repo: Repository) -> list[dict[str, Any]]:
    # Oldest first, so the folders list in the order they were driven.
    return sorted(await repo.list_sessions(), key=lambda s: s["id"])


async def stream_all_laps(repo: Repository, root: str) -> AsyncIterator[bytes]:
    """Every lap's JSON export document, one folder per session under `root`.

    Each lap file is the one GET /api/laps/{id}/export serves, and so what
    POST /api/laps/import accepts. A session with no laps is left out.
    """
    sink = _ChunkSink()
    with zipfile.ZipFile(sink, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for session in await _sessions(repo):
            folder = f"{root}/{archive_name(session['id']).removesuffix('.zip')}"
            index: list[dict[str, Any]] = []
            async for entry in _write_session_laps(zf, repo, session["id"], folder):
                index.append(entry)
                yield sink.take()
            if index:
                _write_manifest(zf, folder, session, index, None)
    yield sink.take()  # the central directory, written on close


async def stream_all_laps_csv(
    repo: Repository, root: str, car_name: Callable[[int], str]
) -> AsyncIterator[bytes]:
    """Every lap as the CSV GET /api/laps/{id}/export.csv serves, one folder
    per session under `root`. `car_name` resolves a car id the way that
    endpoint does, from the loaded car inventory."""
    sink = _ChunkSink()
    with zipfile.ZipFile(sink, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for session in await _sessions(repo):
            folder = f"{root}/{archive_name(session['id']).removesuffix('.zip')}"
            laps = sorted(
                await repo.list_laps(session["id"]), key=lambda lap: (lap["number"], lap["id"])
            )
            for summary in laps:
                lap = await repo.get_lap(summary["id"], with_samples=False)
                samples_json = await repo.lap_samples_json(summary["id"])
                if lap is None or samples_json is None:
                    continue  # deleted while the archive was being written
                name = f"{folder}/{_lap_stem(lap)}.csv"
                car = car_name(lap["car_id"])
                await asyncio.to_thread(_write_lap_csv, zf, name, lap, samples_json, car)
                yield sink.take()
    yield sink.take()


def _write_lap_csv(
    zf: zipfile.ZipFile, name: str, lap: dict[str, Any], samples_json: str, car: str
) -> None:
    text = lap_csv({**lap, "samples": decode_samples(samples_json)}, car)
    zf.writestr(name, text.encode())


# --- CSV ----------------------------------------------------------------------

# Channel map for CSV export: sample column -> (display name, unit).
CSV_CHANNELS = (
    ("t", "Time", "s"),
    ("dist", "Distance", "m"),
    ("speed", "Ground Speed", "km/h"),
    ("throttle", "Throttle Pos", "%"),
    ("brake", "Brake Pos", "%"),
    ("gear", "Gear", ""),
    ("rpm", "Engine RPM", "rpm"),
    ("boost", "Boost Pressure", "bar"),
    ("tire_slip", "Tyre Slip Ratio", ""),
    ("yaw_rate", "Yaw Rate", "rad/s"),
    ("pos_x", "Pos X", "m"),
    ("pos_z", "Pos Z", "m"),
    ("pos_y", "Pos Y", "m"),
    ("body_height", "Ride Height", "mm"),
    ("fuel", "Fuel Level", "L"),
    ("slip_fl", "Tyre Slip FL", ""),
    ("slip_fr", "Tyre Slip FR", ""),
    ("slip_rl", "Tyre Slip RL", ""),
    ("slip_rr", "Tyre Slip RR", ""),
    ("tt_fl", "Tyre Temp FL", "C"),
    ("tt_fr", "Tyre Temp FR", "C"),
    ("tt_rl", "Tyre Temp RL", "C"),
    ("tt_rr", "Tyre Temp RR", "C"),
    ("sus_fl", "Susp Travel FL", "mm"),
    ("sus_fr", "Susp Travel FR", "mm"),
    ("sus_rl", "Susp Travel RL", "mm"),
    ("sus_rr", "Susp Travel RR", "mm"),
    ("aids", "Driver Aids", ""),
    ("surface", "Surface Mask", ""),
    ("steer", "Steering Angle", "rad"),
    # Raw broadcast units — see analysis.accel_calibration for what they turn
    # out to be. Exported unconverted so an external tool calibrates its own way.
    ("acc_lat", "Accel Lateral", ""),
    ("acc_long", "Accel Longitudinal", ""),
    ("acc_vert", "Accel Vertical", ""),
    ("throttle_f", "Throttle Applied", "%"),
    ("brake_f", "Brake Applied", "%"),
    ("race_pos", "Race Position", ""),
    ("body_slip", "Body Slip Angle", "deg"),
)


def _csv_text(value: str) -> str:
    """Neutralize spreadsheet formula injection in text cells."""
    return f"'{value}" if value[:1] in ("=", "+", "-", "@") else value


def lap_csv(lap: dict[str, Any], car: str) -> str:
    """A lap, with its samples, as MoTeC-style CSV (i2 'CSV file' import,
    Excel, etc.). Moved here from the single-lap route so the bulk export
    writes the very same text.

    The "Sample Rate" header is nominal — the `t` column is authoritative
    (it integrates packet-id deltas, so dropped frames widen its steps).
    """
    samples = lap["samples"]
    cols = [c for c in CSV_CHANNELS if c[0] in samples]
    time_ms = lap["time_ms"]
    duration = f"{time_ms // 60000}:{(time_ms % 60000) / 1000:06.3f}"

    buf = io.StringIO()
    meta = csv.writer(buf, quoting=csv.QUOTE_ALL, lineterminator="\n")
    data = csv.writer(buf, quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
    meta.writerow(["Format", "MoTeC CSV File"])
    meta.writerow(["Device", "GT7 Datalogger"])
    meta.writerow(["Vehicle", _csv_text(car)])
    meta.writerow(["Comment", _csv_text(f"Lap {lap['number']} - {duration}")])
    meta.writerow(["Log Date", _csv_text(str(lap.get("finished_at", "")))])
    meta.writerow(["Sample Rate", "60.000"])
    buf.write("\n")  # blank separator line between metadata and channels
    meta.writerow([name for _, name, _ in cols])
    meta.writerow([unit for _, _, unit in cols])
    n = len(samples["t"])
    for i in range(n):
        # Guard against ragged legacy rows; values are numeric so
        # QUOTE_MINIMAL leaves them unquoted.
        data.writerow(
            [samples[key][i] if i < len(samples[key]) else "" for key, _, _ in cols]
        )
    return buf.getvalue()
