"""One file for a whole installation (#161): recordings, surveys, settings,
layouts and, when asked for, the credentials.

The lap exports are for reading laps somewhere else; this is for moving an
install — a Pi whose SD card died, a Docker volume moved to another host.
Its parts are optional, each one what a restore applies as a unit:

    data      every session and lap: a snapshot of the database
    surveys   track bundles, their corrections, the survey run logs, the
              measured car widths, and this install's source id
    config    the settings saved in Admin, minus the credentials
    layouts   the overlay and dash layouts
    tokens    the webhook URL and sync token (secrets.json); off by default

The archive is laid out as the data directory is, so that putting a backup
back by hand is unzipping it there with the app stopped:

    manifest.json               what is inside, written last
    gt7.db                      data
    track-bundles/<slug>.json   surveys
    track-bundles/corrections/<slug>.json
    surface_survey_*.jsonl
    car-widths.json
    source-id.json
    config.json                 config: a PUT /api/admin/config body
    layouts.json                layouts: each one a POST /api/layouts body
    secrets.json                tokens, as SecretStore writes it

Left out on purpose: cars.json (the car refresh rebuilds it), the sync
client's state files (what this install has uploaded, which sync works out
again), and track-bundles/compiled/ (derived from the bundles beside it).

The database snapshot has the `settings` rows the registry describes and
every layout taken out, since those are parts of their own. The rows that
record what has been done to the data (the lap re-checks, the car backfill,
the track seed) stay with it: they describe the recordings, not the machine.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import shutil
import sqlite3
import time
import zipfile
from collections.abc import AsyncIterator, Callable, Collection
from contextlib import closing
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any, Literal

from app import settings_registry
from app.processing import car_width, survey_log, track_bundle, track_corrections
from app.processing.track_compile import app_version
from app.storage import secret_store
from app.storage.archive import ChunkSink

if TYPE_CHECKING:
    from app.storage.repository import Repository

log = logging.getLogger(__name__)

FORMAT = "gt7-datalogger-backup"
VERSION = 1
CONFIG_FORMAT = "gt7-datalogger-config"
LAYOUTS_FORMAT = "gt7-datalogger-layouts"

Part = Literal["data", "surveys", "config", "layouts", "tokens"]
PARTS: tuple[Part, ...] = ("data", "surveys", "config", "layouts", "tokens")

MANIFEST = "manifest.json"
DATABASE = "gt7.db"
CONFIG = "config.json"
LAYOUTS = "layouts.json"

# A made backup waits this long for its download, then its snapshot goes.
TTL_S = 15 * 60
# Pages copied per step of the online backup: 4 MiB at the default page
# size, a fraction of a second even on an SD card. The database runs in
# rollback-journal mode, where a reader blocks a writer's commit, so one
# VACUUM INTO of the live file would hold the recorder off for the whole
# copy — half a minute or more on a Pi — and a lap saved meanwhile fails
# with "database is locked". Between steps nothing is held.
STEP_PAGES = 1024
# A write between steps starts the copy again. The recorder writes once a
# lap, so a restart or two is normal; a database that keeps changing faster
# than it can be copied is refused rather than copied forever.
MAX_RESTARTS = 10
# And however it goes, the copy gives up after this long. An 886 MB
# database copies in about a minute on a Pi; this is for a source that stays
# locked, which no restart count sees.
COPY_DEADLINE_S = 10 * 60
# Read and compressed this much at a time: what a streamed backup holds.
CHUNK = 1 << 20
# Deflate's fastest level: a quarter of the size of a real database (lap
# samples are JSON text), at a seventh of the default level's time — which
# on a Pi is the difference between half a minute and three.
COMPRESS_LEVEL = 1
# Room left free beyond the snapshot's own size.
SPACE_MARGIN = 64 << 20


class BackupError(Exception):
    """A backup that cannot be made; the message says why."""


class NotEnoughSpace(BackupError):
    pass


def backup_name(today: date | None = None) -> str:
    return f"gt7-backup-{(today or datetime.now().date()).isoformat()}.zip"


def snapshot_settings_dropped() -> frozenset[str]:
    """The settings rows a snapshot leaves out: every one the registry
    describes, which the config part carries, and the credentials."""
    return frozenset(settings_registry.BY_KEY) | frozenset(secret_store.KEYS)


# --- the database snapshot ------------------------------------------------------


def snapshot_database(
    src: Path,
    dest: Path,
    drop_settings: Collection[str],
    *,
    on_step: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Write a consistent, compact copy of the database at `src` to `dest`.

    Copied with SQLite's online backup in short steps, so the recorder keeps
    writing throughout; the copy then has the `settings` rows named and
    every layout taken out, and is written to `dest` with VACUUM INTO — which
    leaves the free pages behind, and with them whatever deleted laps left in
    them. Blocking; run it in a thread. Needs about twice the database's size
    free beside `dest` while it runs.

    Returns the snapshot's schema revision and its session and lap counts.
    `on_step` runs between steps, for tests.
    """
    part = dest.with_name(dest.name + ".part")
    part.unlink(missing_ok=True)
    dest.unlink(missing_ok=True)
    try:
        _copy_online(src, part, on_step)
        with closing(sqlite3.connect(part)) as db:
            with db:
                db.execute("DELETE FROM layouts")
                db.executemany(
                    "DELETE FROM settings WHERE key = ?", [(k,) for k in sorted(drop_settings)]
                )
            revision = db.execute("SELECT version_num FROM alembic_version").fetchone()
            sessions = db.execute("SELECT count(*) FROM sessions").fetchone()[0]
            laps = db.execute("SELECT count(*) FROM laps").fetchone()[0]
            db.execute("VACUUM INTO ?", (str(dest),))
    except BaseException:
        dest.unlink(missing_ok=True)
        raise
    finally:
        part.unlink(missing_ok=True)
    return {
        "revision": revision[0] if revision else None,
        "sessions": sessions,
        "laps": laps,
    }


def _copy_online(src: Path, dest: Path, on_step: Callable[[], None] | None) -> None:
    restarts = 0
    last: int | None = None
    deadline = time.monotonic() + COPY_DEADLINE_S

    def progress(status: int, remaining: int, _total: int) -> None:
        nonlocal restarts, last
        # A step that copied pages leaves fewer to go. A successful step that
        # did not was a restart (another connection wrote between steps): it
        # starts over at the count the first step leaves, which is no change
        # at all when the writes come early, so the test is "not down", not
        # "up". A busy step (a writer committing) copies nothing and is
        # waited out; the deadline bounds those.
        if status == sqlite3.SQLITE_OK and last is not None and remaining >= last:
            restarts += 1
            if restarts > MAX_RESTARTS:
                raise BackupError(
                    "the database kept changing while it was copied; "
                    "try again between sessions"
                )
        last = remaining
        if time.monotonic() > deadline:
            raise BackupError(
                f"copying the database took more than {COPY_DEADLINE_S // 60} minutes; "
                "try again between sessions"
            )
        if on_step is not None:
            on_step()

    # Read-only, so nothing here can ever write to the recordings.
    with (
        closing(sqlite3.connect(f"{src.resolve().as_uri()}?mode=ro", uri=True)) as source,
        closing(sqlite3.connect(dest)) as target,
    ):
        source.backup(target, pages=STEP_PAGES, progress=progress)


def check_space(data_dir: Path, db_path: Path) -> None:
    """Refuse a snapshot that would fill the disk: the copy and the compacted
    snapshot sit side by side for a moment, each up to the database's size."""
    needed = 2 * db_path.stat().st_size + SPACE_MARGIN
    free = shutil.disk_usage(data_dir).free
    if free < needed:
        raise NotEnoughSpace(
            f"a backup with the recordings needs {needed >> 20} MB free beside the "
            f"database; {free >> 20} MB is"
        )


# --- the other parts --------------------------------------------------------------


def survey_files(data_dir: Path) -> list[tuple[str, Path]]:
    """(name in the archive, path) for every file the surveys part carries."""
    files: list[tuple[str, Path]] = []
    bundles = data_dir / track_bundle.BUNDLE_DIR
    corrections = bundles / track_corrections.DIRECTORY
    for folder in (bundles, corrections):
        if folder.is_dir():
            files += [
                (p.relative_to(data_dir).as_posix(), p)
                for p in sorted(folder.glob("*.json"))
                if p.is_file()
            ]
    files += [(p.name, p) for p in sorted(data_dir.glob(survey_log.LOG_GLOB)) if p.is_file()]
    for name in (car_width.FILENAME, track_bundle.SOURCE_FILE):
        if (data_dir / name).is_file():
            files.append((name, data_dir / name))
    return files


def survey_summary(files: list[tuple[str, Path]]) -> dict[str, Any]:
    bundles = f"{track_bundle.BUNDLE_DIR}/"
    corrections = f"{bundles}{track_corrections.DIRECTORY}/"
    names = [name for name, _ in files]
    return {
        "bundles": sum(1 for n in names if n.startswith(bundles) and "/" not in n[len(bundles):]),
        "corrections": sum(1 for n in names if n.startswith(corrections)),
        "logs": sum(1 for n in names if n.startswith("surface_survey_")),
        "car_widths": car_width.FILENAME in names,
        "source_id": track_bundle.SOURCE_FILE in names,
    }


def config_document(stored: dict[str, str]) -> dict[str, Any]:
    """The settings saved in Admin, in the shape PUT /api/admin/config takes.

    Only what was saved, not what the environment says: that belongs to the
    machine. Never a credential — those are the tokens part, and the admin
    token is not carried at all, so a restore cannot lock anyone out. A
    saved value that no longer validates is left behind, as a start would
    skip it.
    """
    values: dict[str, Any] = {}
    for entry in settings_registry.ENTRIES:
        text = stored.get(entry.key)
        if text is None or not entry.editable or entry.secret:
            continue
        if entry.key in secret_store.KEYS:
            continue
        try:
            values[entry.key] = settings_registry.to_api(
                entry, settings_registry.from_store(entry, text)
            )
        except settings_registry.SettingError:
            continue
    return {"format": CONFIG_FORMAT, "version": 1, "values": values}


def layouts_document(layouts: list[dict[str, Any]]) -> dict[str, Any]:
    """Every layout, each in the shape POST /api/layouts takes. Ids are left
    out: they are this database's, and mean nothing to another."""
    return {
        "format": LAYOUTS_FORMAT,
        "version": 1,
        "layouts": [
            {k: layout[k] for k in ("name", "kind", "config", "created_at", "updated_at")}
            for layout in layouts
        ],
    }


def tokens_document(values: dict[str, str]) -> dict[str, Any]:
    """The secrets file as SecretStore writes it, so it can go straight back."""
    return {
        "format": secret_store.FORMAT,
        "version": secret_store.VERSION,
        "settings": dict(sorted(values.items())),
    }


async def inventory(
    repo: Repository, data_dir: Path, db_path: Path, tokens: dict[str, str]
) -> dict[str, Any]:
    """What each part would hold if a backup were made now."""
    stats = await repo.stats()
    surveys = survey_files(data_dir)
    config = config_document(await repo.get_settings())
    layouts = await repo.list_layouts()
    return {
        "data": {
            "available": db_path.is_file(),
            "bytes": db_path.stat().st_size if db_path.is_file() else 0,
            "sessions": stats["sessions"],
            "laps": stats["laps"],
        },
        "surveys": {
            "available": bool(surveys),
            "bytes": sum(_size(p) for _, p in surveys),
            **survey_summary(surveys),
        },
        "config": {"available": bool(config["values"]), "settings": len(config["values"])},
        "layouts": {"available": bool(layouts), "layouts": len(layouts)},
        "tokens": {"available": bool(tokens), "keys": sorted(tokens)},
        "free_bytes": shutil.disk_usage(data_dir).free if data_dir.is_dir() else 0,
    }


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


# --- made backups -----------------------------------------------------------------


@dataclass(eq=False)
class Prepared:
    """A backup made and waiting for its download.

    The snapshot is taken when the backup is made, which is the slow part
    and the part that can fail; everything else is read as it is streamed.
    """

    id: str
    parts: tuple[Part, ...]
    created_at: str
    name: str
    snapshot: Path | None = None
    data: dict[str, Any] = field(default_factory=dict)
    streaming: int = 0
    discarded: bool = False
    expiry: asyncio.TimerHandle | None = None

    def describe(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "url": f"/api/backup/{self.id}/{self.name}",
            "parts": list(self.parts),
            "created_at": self.created_at,
            "expires_in_s": TTL_S,
            "database_bytes": self.snapshot.stat().st_size if self.snapshot else 0,
        }


class Backups:
    """The backup waiting to be downloaded: one at a time, since each holds a
    copy of the database on a disk that may be an SD card.

    A backup is the download link's only credential: its id is unguessable
    and lives for TTL_S. Making it is admin-gated; fetching it cannot be,
    because a browser download cannot carry the X-API-Key header.
    """

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self._current: Prepared | None = None
        self._lock = asyncio.Lock()
        # The snapshot of a backup whose request was cancelled. Its thread
        # cannot be stopped, so it is waited for, and what it wrote removed.
        self._orphan: asyncio.Future[dict[str, Any]] | None = None
        # Whatever a crash or a stop mid-backup left behind.
        for leftover in data_dir.glob(".backup-*"):
            leftover.unlink(missing_ok=True)

    async def prepare(
        self, parts: Collection[Part], db_path: Path, revision: str | None
    ) -> Prepared:
        ordered = tuple(p for p in PARTS if p in parts)
        async with self._lock:
            # One copy of the database at a time, a cancelled one included.
            if self._orphan is not None:
                await asyncio.wait([self._orphan])
            # A new backup replaces the one before: its link stops working.
            if self._current is not None:
                self.discard(self._current)
            prepared = Prepared(
                id=secrets.token_urlsafe(18),
                parts=ordered,
                created_at=datetime.now(UTC).isoformat(),
                name=backup_name(),
                data={"revision": revision},
            )
            if "data" in ordered:
                if not db_path.is_file():
                    raise BackupError("there is no database file to back up")
                self.data_dir.mkdir(parents=True, exist_ok=True)
                check_space(self.data_dir, db_path)
                snapshot = self.data_dir / f".backup-{prepared.id}.db"
                prepared.data = await self._snapshot(db_path, snapshot)
                prepared.snapshot = snapshot
            prepared.expiry = asyncio.get_running_loop().call_later(
                TTL_S, self.discard, prepared
            )
            self._current = prepared
            log.info("backup made: %s", ", ".join(ordered))
            return prepared

    async def _snapshot(self, db_path: Path, snapshot: Path) -> dict[str, Any]:
        work = asyncio.ensure_future(
            asyncio.to_thread(snapshot_database, db_path, snapshot, snapshot_settings_dropped())
        )
        try:
            return await asyncio.shield(work)
        except asyncio.CancelledError:
            # The request is gone but the copy goes on in its thread, with
            # nothing to own the file it writes. Removing it now would race
            # the thread recreating it, so it goes once the thread is done.
            self._orphan = work
            work.add_done_callback(lambda done: self._drop_orphan(done, snapshot))
            raise

    def _drop_orphan(self, work: asyncio.Future[dict[str, Any]], snapshot: Path) -> None:
        if not work.cancelled() and work.exception() is not None:
            log.info("a cancelled backup's snapshot failed: %s", work.exception())
        snapshot.unlink(missing_ok=True)
        if self._orphan is work:
            self._orphan = None

    def get(self, backup_id: str) -> Prepared | None:
        current = self._current
        if current is None or current.discarded:
            return None
        if not secrets.compare_digest(current.id.encode(), backup_id.encode()):
            return None
        return current

    def discard(self, prepared: Prepared) -> None:
        """Forget a backup; its snapshot goes once no download is reading it."""
        prepared.discarded = True
        if prepared.expiry is not None:
            prepared.expiry.cancel()
        if self._current is prepared:
            self._current = None
        if not prepared.streaming and prepared.snapshot is not None:
            prepared.snapshot.unlink(missing_ok=True)

    def discard_all(self) -> None:
        if self._current is not None:
            self.discard(self._current)

    async def stream(
        self, prepared: Prepared, chunks: AsyncIterator[bytes]
    ) -> AsyncIterator[bytes]:
        """`chunks`, keeping the snapshot until they are sent; a download that
        finishes frees the disk at once rather than at expiry."""
        prepared.streaming += 1
        complete = False
        try:
            async for chunk in chunks:
                yield chunk
            complete = True
        finally:
            prepared.streaming -= 1
            if complete:
                self.discard(prepared)
            elif prepared.discarded and not prepared.streaming and prepared.snapshot:
                prepared.snapshot.unlink(missing_ok=True)


# --- writing the archive ----------------------------------------------------------


async def write_archive(
    prepared: Prepared,
    data_dir: Path,
    repo: Repository,
    tokens: dict[str, str],
) -> AsyncIterator[bytes]:
    """The backup as a ZIP, streamed as it is written (see archive.ChunkSink)."""
    sink = ChunkSink()
    parts: dict[str, dict[str, Any]] = {}
    with zipfile.ZipFile(
        sink, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=COMPRESS_LEVEL
    ) as zf:
        if prepared.snapshot is not None:
            async for chunk in _write_file(zf, sink, prepared.snapshot, DATABASE):
                yield chunk
            size = zf.getinfo(DATABASE).file_size
            parts["data"] = {
                "files": [DATABASE],
                "bytes": size,
                "sessions": prepared.data["sessions"],
                "laps": prepared.data["laps"],
            }
        if "surveys" in prepared.parts:
            written: list[tuple[str, Path]] = []
            for name, path in survey_files(data_dir):
                try:
                    async for chunk in _write_file(zf, sink, path, name):
                        yield chunk
                except FileNotFoundError:
                    continue  # removed since the listing
                written.append((name, path))
            parts["surveys"] = {
                "files": [n for n, _ in written],
                "bytes": sum(zf.getinfo(n).file_size for n, _ in written),
                **survey_summary(written),
            }
        if "config" in prepared.parts:
            doc = config_document(await repo.get_settings())
            parts["config"] = {
                **_write_json(zf, CONFIG, doc),
                "settings": len(doc["values"]),
            }
        if "layouts" in prepared.parts:
            doc = layouts_document(await repo.list_layouts())
            parts["layouts"] = {
                **_write_json(zf, LAYOUTS, doc),
                "layouts": len(doc["layouts"]),
            }
        if "tokens" in prepared.parts:
            entry: dict[str, Any] = {"files": [], "bytes": 0}
            if tokens:
                # Owner-only when unzipped, as SecretStore creates it.
                entry = _write_json(zf, secret_store.FILENAME, tokens_document(tokens), mode=0o600)
            parts["tokens"] = {**entry, "keys": sorted(tokens)}
        manifest = {
            "format": FORMAT,
            "version": VERSION,
            "app_version": app_version(),
            "created_at": prepared.created_at,
            "alembic_revision": prepared.data.get("revision"),
            "parts": parts,
        }
        _write_json(zf, MANIFEST, manifest)
        yield sink.take()
    yield sink.take()  # the central directory, written on close


def _write_json(
    zf: zipfile.ZipFile, name: str, doc: dict[str, Any], *, mode: int = 0o644
) -> dict[str, Any]:
    data = json.dumps(doc, ensure_ascii=False, indent=1).encode()
    info = zipfile.ZipInfo(name, date_time=_zip_time())
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = (0o100000 | mode) << 16  # a regular file, with these permissions
    zf.writestr(info, data)
    return {"files": [name], "bytes": len(data)}


async def _write_file(
    zf: zipfile.ZipFile, sink: ChunkSink, path: Path, name: str
) -> AsyncIterator[bytes]:
    """One file a chunk at a time, read and compressed off the event loop, so
    neither memory nor the loop ever holds a whole file: not the database,
    and not a survey log, which grows with the run that wrote it."""
    # From the file, as ZipFile.write builds it: its date, its mode, and its
    # size, which decides Zip64 up front — the stream cannot go back and add
    # it once the entry is written.
    info = zipfile.ZipInfo.from_file(path, name)
    info.compress_type = zipfile.ZIP_DEFLATED
    # The archive's level, set the way ZipFile.write sets it (an alias of
    # compress_level from Python 3.13; typeshed declares neither for 3.12).
    info._compresslevel = COMPRESS_LEVEL  # type: ignore[attr-defined]
    with open(path, "rb") as src, zf.open(info, "w") as dst:
        while await asyncio.to_thread(_copy_chunk, src, dst):
            yield sink.take()


def _copy_chunk(src: IO[bytes], dst: IO[bytes]) -> bool:
    chunk = src.read(CHUNK)
    dst.write(chunk)
    return bool(chunk)


def _zip_time() -> tuple[int, int, int, int, int, int]:
    now = datetime.now()
    return (now.year, now.month, now.day, now.hour, now.minute, now.second)
