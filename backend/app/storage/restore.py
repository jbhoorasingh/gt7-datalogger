"""Putting a backup back (#161): the half of app.storage.backup that reads one.

A backup is uploaded whole, checked, and kept beside the database until it
is applied or forgotten. On upload only its manifest and the two small
documents Settings lists from (config.json, layouts.json) are read; the rest
waits for the parts that are ticked:

    data       replaces the recordings. Unpacked beside the database, checked,
               brought to this build's schema and given this install's own
               settings and layouts; then the service swaps it in
               (TelemetryService.replace_database), keeping the database it
               replaced as gt7.db.before-restore-<time>
    surveys    merged: every bundle through the import's merge; corrections,
               survey logs and car widths added where this install has none
    source_id  source-id.json, a switch of its own: who this install is in
               the votes of the survey bundles. Moving to a new machine wants
               it; copying recordings to a second install does not
    config     through the settings registry, as PUT /api/admin/config
    layouts    by name: a backup's layout replaces one of the same name
    tokens     into secrets.json

A ZIP is input, not a trust boundary. Every name in it is checked against
what a backup holds, every size against a limit, the manifest against the
contents, the database for damage and for a schema this build can open, and
every document against the validation its own endpoint applies.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
import shutil
import sqlite3
import zipfile
from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager, closing
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import cache
from pathlib import Path
from typing import Any, Literal

from alembic.script import ScriptDirectory

from app import settings_registry
from app.processing import (
    car_refresh,
    car_width,
    shared_pull,
    track_bundle,
    track_corrections,
)
from app.processing.track_compile import app_version
from app.storage import backup, secret_store
from app.storage.db import alembic_config, init_db, make_engine

log = logging.getLogger(__name__)

Item = Literal["data", "surveys", "source_id", "config", "layouts", "tokens"]
ITEMS: tuple[Item, ...] = ("data", "surveys", "source_id", "config", "layouts", "tokens")

# An upload waits this long to be applied, then it goes.
TTL_S = 30 * 60
# The manifest and the small documents: config, layouts, secrets, the
# source id and the car widths, each a few kilobytes when a backup makes it.
SMALL_MAX = 4 << 20
# A bundle or a corrections file: what the import takes, since each is read
# whole to be validated. Survey logs have no limit but free space: the backup
# writes them whatever their size, and they are copied a chunk at a time.
DOCUMENT_MAX = shared_pull.MAX_IMPORT_BYTES
SPACE_MARGIN = backup.SPACE_MARGIN
CHUNK = backup.CHUNK

# Settings that describe the machine more than the person who set it up:
# Settings lists them, unticked, and they go only when ticked. Paths too,
# below: a path names a file on the machine that saved it.
MACHINE_SETTINGS = frozenset(
    {"source", "ps_ip", "telemetry_port", "heartbeat_port", "sim_scenario", "mdns"}
)

# A restored database takes these rows from the one it replaces, besides
# every setting the registry describes: what this machine has done, not
# what was done to the recordings. (The lap re-checks, the car backfill and
# the track seed describe the recordings, and come with them.)
MACHINE_STATE = (car_refresh.UPDATED_AT_KEY, shared_pull.FIRST_PULL_KEY)

_BUNDLE = re.compile(rf"{track_bundle.BUNDLE_DIR}/([a-z0-9-]{{1,120}})\.json")
_CORRECTIONS = re.compile(
    rf"{track_bundle.BUNDLE_DIR}/{track_corrections.DIRECTORY}/([a-z0-9-]{{1,120}})\.json"
)
# Any name the survey's log glob lists, as long as it stays a file name.
_LOG = re.compile(r"surface_survey_[^/\\\x00]{1,200}\.jsonl")
_SMALL = frozenset(
    {
        backup.MANIFEST,
        backup.CONFIG,
        backup.LAYOUTS,
        secret_store.FILENAME,
        car_width.FILENAME,
        track_bundle.SOURCE_FILE,
    }
)
# Tables the recordings cannot be without.
_TABLES = ("alembic_version", "sessions", "laps", "settings", "layouts")


class RestoreError(Exception):
    """A backup, or a part of one, that cannot be restored; the message says why."""


class NotEnoughSpace(RestoreError):
    pass


class TooLarge(ValueError):
    """A file in the backup larger than this version reads; it fails alone."""


def is_machine_setting(key: str) -> bool:
    entry = settings_registry.BY_KEY.get(key)
    return key in MACHINE_SETTINGS or (entry is not None and entry.kind == "path")


def carried_settings() -> frozenset[str]:
    """The settings rows a restored database takes from the one it replaces:
    every setting the registry describes, which the snapshot was made without
    (the config part merges the backup's over them), and the machine's own
    state. Never a credential: those live in secrets.json."""
    registry = frozenset(settings_registry.BY_KEY) - frozenset(secret_store.KEYS)
    return registry | frozenset(MACHINE_STATE)


def part_of(name: str) -> str | None:
    """The backup part a member belongs to, or None for a name no backup holds."""
    if name == backup.DATABASE:
        return "data"
    if (
        _BUNDLE.fullmatch(name)
        or _CORRECTIONS.fullmatch(name)
        or _LOG.fullmatch(name)
        or name in (car_width.FILENAME, track_bundle.SOURCE_FILE)
    ):
        return "surveys"
    if name == backup.CONFIG:
        return "config"
    if name == backup.LAYOUTS:
        return "layouts"
    if name == secret_store.FILENAME:
        return "tokens"
    return None


def _limit(name: str) -> int | None:
    """The most a member may hold when it is read whole; None for the ones
    copied a chunk at a time (the database, survey logs), which free space
    bounds."""
    if name == backup.DATABASE or _LOG.fullmatch(name):
        return None
    return SMALL_MAX if name in _SMALL else DOCUMENT_MAX


def _read(zf: zipfile.ZipFile, name: str) -> bytes:
    """A member read whole, refused as TooLarge past its limit. The size is
    the one the archive states, and reading never yields more than that."""
    size = zf.getinfo(name).file_size
    limit = _limit(name)
    if limit is not None and size > limit:
        # Rounded up: just past the limit must not read as the limit itself.
        mb = -(-size // (1 << 20))
        raise TooLarge(f"{name} is {mb} MB, more than this version reads ({limit >> 20} MB)")
    return zf.read(name)


# --- reading an upload -------------------------------------------------------------


def inspect(path: Path) -> dict[str, Any]:
    """Check an uploaded backup and read what Settings lists from it: the
    manifest, and the config and layouts documents when it holds them.

    Raises RestoreError naming what is wrong with the file as a whole. A
    document that does not read is the part's problem, not the file's: it is
    returned as that part's error, so the other parts can still go back.
    Blocking.
    """
    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise RestoreError("not a ZIP file") from exc
    with zf:
        infos = zf.infolist()
        names = [info.filename for info in infos]
        if len(set(names)) != len(names):
            raise RestoreError("the file holds two entries under one name")
        for info in infos:
            name = info.filename
            if name != backup.MANIFEST and part_of(name) is None:
                raise RestoreError(f"{name!r} is not something a backup holds")
            if info.flag_bits & 0x1:
                raise RestoreError(f"{name} is encrypted")
            if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                raise RestoreError(f"{name} is compressed in a way a backup never is")
        # Sizes are checked where each file is read, so that one larger than
        # this version takes fails its own part, never the whole file — all
        # but the manifest, without which nothing can be listed.
        if backup.MANIFEST not in names:
            raise RestoreError(
                "there is no manifest.json in it: not a backup made in Settings › Data"
            )
        try:
            manifest = json.loads(_read(zf, backup.MANIFEST))
        except TooLarge as exc:
            raise RestoreError(str(exc)) from exc
        except (ValueError, zipfile.BadZipFile) as exc:
            raise RestoreError(f"manifest.json does not read: {exc}") from exc
        check_manifest(manifest, names)
        found: dict[str, Any] = {"manifest": manifest, "names": names}
        if backup.DATABASE in names:
            found["database_bytes"] = zf.getinfo(backup.DATABASE).file_size
        # What the surveys part unpacks to. Survey logs compress a thousandfold,
        # so a small file can hold far more than the disk does.
        found["surveys_bytes"] = sum(
            info.file_size for info in infos if part_of(info.filename) == "surveys"
        )
        for key, name, read in (
            ("config", backup.CONFIG, read_config),
            ("layouts", backup.LAYOUTS, read_layouts),
        ):
            if name not in names:
                continue
            try:
                found[key] = read(json.loads(_read(zf, name)))
            except TooLarge as exc:
                found[f"{key}_error"] = str(exc)
            except (ValueError, zipfile.BadZipFile) as exc:
                found[f"{key}_error"] = f"{name} does not read: {exc}"
    return found


def check_manifest(manifest: Any, names: list[str]) -> None:
    """The manifest describes a backup this build reads, and the file holds
    exactly what it lists."""
    if not isinstance(manifest, dict) or manifest.get("format") != backup.FORMAT:
        raise RestoreError("manifest.json is not a datalogger backup's")
    version = manifest.get("version")
    if version != backup.VERSION:
        if isinstance(version, int) and version > backup.VERSION:
            raise RestoreError(
                f"the backup is in format {version}, newer than this version reads "
                f"({backup.VERSION}); update this install first"
            )
        raise RestoreError(f"the manifest names an unknown format version: {version!r}")
    parts = manifest.get("parts")
    if not isinstance(parts, dict) or not parts:
        raise RestoreError("the manifest lists no parts")
    listed: set[str] = set()
    for part, entry in parts.items():
        if part not in backup.PARTS:
            raise RestoreError(f"the manifest lists a part this version does not know: {part!r}")
        files = entry.get("files") if isinstance(entry, dict) else None
        if not isinstance(files, list) or not all(isinstance(f, str) for f in files):
            raise RestoreError(f"the manifest's {part} part lists no files")
        for name in files:
            if name not in names:
                raise RestoreError(f"the manifest lists {name}, which is not in the file")
            if part_of(name) != part:
                raise RestoreError(f"the manifest lists {name} under {part}")
            listed.add(name)
    unlisted = sorted(set(names) - listed - {backup.MANIFEST})
    if unlisted:
        raise RestoreError(f"{unlisted[0]} is in the file but not in its manifest")


def read_config(doc: Any) -> dict[str, Any]:
    """The values of a backup's config.json: key -> the value as GET
    /api/admin/config reports it. Validated per setting when applied."""
    if not isinstance(doc, dict) or doc.get("format") != backup.CONFIG_FORMAT:
        raise ValueError("not a config document")
    values = doc.get("values")
    if not isinstance(values, dict):
        raise ValueError("no values")
    return {str(k): v for k, v in values.items()}


def read_layouts(doc: Any) -> list[dict[str, Any]]:
    """The layouts of a backup's layouts.json, each a POST /api/layouts body.
    Validated per layout when listed and applied."""
    if not isinstance(doc, dict) or doc.get("format") != backup.LAYOUTS_FORMAT:
        raise ValueError("not a layouts document")
    layouts = doc.get("layouts")
    if not isinstance(layouts, list) or not all(isinstance(x, dict) for x in layouts):
        raise ValueError("no layouts")
    return layouts


@cache
def schema_revisions() -> tuple[str, ...]:
    """Every schema revision this build knows, oldest first; the last is its head."""
    script = ScriptDirectory.from_config(alembic_config())
    return tuple(r.revision for r in reversed(list(script.walk_revisions())))


def revision_problem(revision: Any, made_by: Any) -> str:
    """Why this build cannot take a database at `revision`, or ''.

    Older is fine: it is upgraded, as a start upgrades one. Newer is not — an
    older version cannot open a newer database (0.8.0 stops at startup on
    one that has 0013_lap_wheelbase) — and newer is any revision this build
    has never heard of."""
    if not isinstance(revision, str) or not revision:
        return "the backup does not say which schema its database is at"
    if revision not in schema_revisions():
        return (
            f"the recordings were saved by a newer version ({made_by or 'unknown'}, "
            f"schema {revision}) than this one ({app_version()}), which "
            "cannot open them; update this install, then restore"
        )
    return ""


def check_room(found: dict[str, Any], items: list[Item], free: int) -> None:
    """Refuse a restore whose ticked parts would not fit beside what is here,
    before any of them is applied."""
    needed = SPACE_MARGIN
    if "data" in items:
        needed = database_bytes_needed(found)
    if "surveys" in items:
        needed += int(found.get("surveys_bytes", 0))
    if free < needed:
        raise NotEnoughSpace(
            f"the parts ticked need {needed >> 20} MB free here; {free >> 20} MB is"
        )


def database_bytes_needed(found: dict[str, Any]) -> int:
    """Free space a data restore needs: the database unpacked, room for a
    migration to rebuild its tables when it is older than this build, and
    the margin. The database it replaces is kept by renaming, which costs
    nothing; the upload is on disk already."""
    size = int(found.get("database_bytes", 0))
    revision = found["manifest"].get("alembic_revision")
    upgrade = revision != schema_revisions()[-1]
    return size * (2 if upgrade else 1) + SPACE_MARGIN


# --- uploads ----------------------------------------------------------------------


@dataclass(eq=False)
class Upload:
    """A backup uploaded and waiting to be restored."""

    id: str
    name: str
    path: Path
    size: int
    found: dict[str, Any]
    received_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    applying: bool = False
    discarded: bool = False
    # Its time ran out while it was being applied, or it was applied: either
    # way it goes once the restore is done.
    expired: bool = False
    applied: bool = False
    expiry: asyncio.TimerHandle | None = None

    @property
    def manifest(self) -> dict[str, Any]:
        manifest: dict[str, Any] = self.found["manifest"]
        return manifest

    def holds(self, name: str) -> bool:
        return name in self.found["names"]


class Restores:
    """The backup uploaded to be restored: one at a time, since each is a
    file the size of a compressed database on a disk that may be an SD card.
    A new upload replaces the one before; one being applied cannot be.
    """

    def __init__(self, data_dir: Path, disk: backup.DiskClaim | None = None) -> None:
        self.data_dir = data_dir
        # Shared with backups: one upload, restore or snapshot at a time.
        self.disk = disk or backup.DiskClaim()
        self._current: Upload | None = None
        # Whatever a crash or a stop mid-upload or mid-restore left behind.
        for leftover in data_dir.glob(".restore-*"):
            leftover.unlink(missing_ok=True)

    async def receive(
        self, chunks: AsyncIterator[bytes], name: str, length: int | None
    ) -> Upload:
        """Spool an upload to the data directory, check it, and keep it.

        Refused before a byte is written when its stated length would not fit,
        and stopped when it outgrows the space there was at the start: a
        chunked upload states no length. Refused (DiskBusy) while another
        upload, a restore or a backup's snapshot is filling the disk, since
        that space is theirs.
        """
        async with self.disk.claim(backup.UPLOADING):
            upload = await self._receive(chunks, name, length)
        if self._current is not None:
            self.discard(self._current)
        upload.expiry = asyncio.get_running_loop().call_later(TTL_S, self._expire, upload)
        self._current = upload
        log.info("backup uploaded for restore: %s, %d MB", upload.name, upload.size >> 20)
        return upload

    async def _receive(
        self, chunks: AsyncIterator[bytes], name: str, length: int | None
    ) -> Upload:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        room = shutil.disk_usage(self.data_dir).free - SPACE_MARGIN
        if length is not None and length > room:
            raise NotEnoughSpace(
                f"the backup is {length >> 20} MB and {max(room, 0) >> 20} MB is free here"
            )
        upload_id = secrets.token_urlsafe(12)
        part = self.data_dir / f".restore-{upload_id}.zip.part"
        path = self.data_dir / f".restore-{upload_id}.zip"
        size = 0
        try:
            with part.open("wb") as out:
                async for chunk in chunks:
                    size += len(chunk)
                    if size > room:
                        raise NotEnoughSpace(
                            f"the backup is larger than the {max(room, 0) >> 20} MB free here"
                        )
                    out.write(chunk)
            if size == 0:
                raise RestoreError("the upload was empty")
            found = await asyncio.to_thread(inspect, part)
            part.replace(path)
        except BaseException:
            part.unlink(missing_ok=True)
            raise
        return Upload(id=upload_id, name=_display_name(name), path=path, size=size, found=found)

    def get(self, upload_id: str | None = None) -> Upload | None:
        current = self._current
        if current is None or current.discarded:
            return None
        if upload_id is not None and not secrets.compare_digest(
            current.id.encode(), upload_id.encode()
        ):
            return None
        return current

    @asynccontextmanager
    async def applying(self, upload: Upload) -> AsyncIterator[None]:
        """Hold the upload while it is applied: one restore at a time, none
        beside an upload or a backup's snapshot (DiskBusy), and neither its
        expiry nor a new upload deletes it underneath."""
        async with self.disk.claim(backup.RESTORING):
            upload.applying = True
            try:
                yield
            finally:
                upload.applying = False
                if upload.expired or upload.applied:
                    self.discard(upload)

    def _expire(self, upload: Upload) -> None:
        upload.expired = True
        self.discard(upload)

    def discard(self, upload: Upload) -> None:
        """Forget an upload and delete its file, unless it is being applied:
        then it goes when the restore is done."""
        if upload.applying:
            return
        upload.discarded = True
        if upload.expiry is not None:
            upload.expiry.cancel()
        if self._current is upload:
            self._current = None
        upload.path.unlink(missing_ok=True)

    def discard_all(self) -> None:
        if self._current is not None:
            self._current.applying = False
            self.discard(self._current)


def _display_name(name: str) -> str:
    base = name.replace("\\", "/").rsplit("/", 1)[-1].strip()
    return base[:120] or "backup.zip"


# --- data --------------------------------------------------------------------------


async def run_to_end[T](work: Awaitable[T]) -> T:
    """Await `work` in a task of its own that a cancelled caller waits out.

    A thread cannot be stopped, and cancelling the coroutine that waits on it
    does not stop it either: a restore cancelled mid-swap would otherwise
    reopen the session gate, restart the writers and delete its staged file
    while the thread was still renaming the database. So the caller's
    cancellation is held until `work` has ended, done or undone, and only
    then goes on.
    """
    task = asyncio.ensure_future(work)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.wait([task])
            except asyncio.CancelledError:
                continue
        if not task.cancelled() and task.exception() is not None:
            log.warning("restore step cancelled by its caller failed: %s", task.exception())
        raise


def unpack_database(upload: Path, dest: Path) -> None:
    """The backup's database, written to `dest` a chunk at a time. Blocking."""
    dest.unlink(missing_ok=True)
    try:
        with zipfile.ZipFile(upload) as zf, zf.open(backup.DATABASE) as src, dest.open(
            "wb"
        ) as out:
            shutil.copyfileobj(src, out, CHUNK)
    except zipfile.BadZipFile as exc:
        dest.unlink(missing_ok=True)
        raise RestoreError(f"the backup's database does not unpack: {exc}") from exc
    except BaseException:
        dest.unlink(missing_ok=True)
        raise


def check_database(path: Path, made_by: Any) -> dict[str, Any]:
    """An unpacked database is whole, holds recordings, and is at a schema
    this build can open: its own revision, not the manifest's, decides.
    Returns its revision and its session and lap counts. Blocking."""
    try:
        with closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)) as db:
            check = db.execute("PRAGMA quick_check").fetchall()
            if [tuple(row) for row in check] != [("ok",)]:
                raise RestoreError(f"the backup's database is damaged: {check[0][0]}")
            tables = {
                row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
            missing = [t for t in _TABLES if t not in tables]
            if missing:
                raise RestoreError(
                    f"the backup's database has no {missing[0]} table: not the recordings"
                )
            row = db.execute("SELECT version_num FROM alembic_version").fetchone()
            revision = row[0] if row else None
            problem = revision_problem(revision, made_by)
            if problem:
                raise RestoreError(problem)
            sessions = db.execute("SELECT count(*) FROM sessions").fetchone()[0]
            laps = db.execute("SELECT count(*) FROM laps").fetchone()[0]
    except sqlite3.DatabaseError as exc:
        raise RestoreError(f"the backup's database does not open: {exc}") from exc
    return {"revision": revision, "sessions": sessions, "laps": laps}


async def upgrade_database(path: Path) -> str | None:
    """Bring an unpacked database to this build's schema, as a start would.
    Done before the swap, so the recorder is paused for the swap alone."""
    engine = make_engine(path)
    try:
        await init_db(engine)
    finally:
        await engine.dispose()
    with closing(sqlite3.connect(path)) as db:
        row = db.execute("SELECT version_num FROM alembic_version").fetchone()
    return row[0] if row else None


def kept_path(live: Path) -> Path:
    """Where the database a restore replaces is kept: beside it, by time."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    kept = live.with_name(f"{live.name}.before-restore-{stamp}")
    n = 2
    while kept.exists():
        kept = live.with_name(f"{live.name}.before-restore-{stamp}-{n}")
        n += 1
    return kept


def install_database(live: Path, staged: Path, kept: Path) -> None:
    """Swap `staged` in for the database at `live`, which is kept at `kept`.

    Only with nothing connected to `live`: the caller has shut the session
    gate and closed the engine's connections. The staged database is first
    given this install's own rows — the settings and layouts saved here,
    which the snapshot was made without — and every session in it is marked
    changed past anything the replaced database said, so a client keeping a
    copy (#142) fetches them all again rather than trusting what it holds
    for the same ids. Blocking.
    """
    if any(live.with_name(live.name + end).exists() for end in ("-journal", "-wal")):
        # A transaction that never finished, or pages never checkpointed:
        # they belong to the database at this name, and whichever file next
        # opens here would take them for its own.
        raise RestoreError(
            "the database has an unfinished transaction; restart the app and try again"
        )
    keys = sorted(carried_settings())
    marks = ", ".join("?" * len(keys))
    with closing(sqlite3.connect(staged.resolve().as_uri(), uri=True)) as db:
        db.execute("ATTACH DATABASE ? AS here", (f"{live.resolve().as_uri()}?mode=ro",))
        columns = ", ".join(
            f'"{row[1]}"' for row in db.execute("PRAGMA main.table_info(layouts)")
        )
        with db:
            db.execute(f"DELETE FROM main.settings WHERE key IN ({marks})", keys)
            db.execute(
                "INSERT INTO main.settings (key, value) "
                f"SELECT key, value FROM here.settings WHERE key IN ({marks})",
                keys,
            )
            db.execute("DELETE FROM main.layouts")
            db.execute(
                f"INSERT INTO main.layouts ({columns}) SELECT {columns} FROM here.layouts"
            )
            top = db.execute("SELECT coalesce(max(revision), 0) FROM here.sessions").fetchone()[0]
            db.execute("UPDATE main.sessions SET revision = revision + ?", (top + 1,))
        db.execute("DETACH DATABASE here")
    os.replace(live, kept)
    try:
        os.replace(staged, live)
    except BaseException:
        os.replace(kept, live)
        raise


def uninstall_database(live: Path, kept: Path) -> None:
    """Put the kept database back where the restored one went, which goes."""
    failed = live.with_name(f".restore-failed-{secrets.token_hex(4)}.db")
    os.replace(live, failed)
    os.replace(kept, live)
    failed.unlink(missing_ok=True)


# --- surveys -----------------------------------------------------------------------


def restore_surveys(upload: Path, data_dir: Path) -> dict[str, Any]:
    """Merge a backup's surveys into this install's. Blocking; every write
    holds track_bundle.WRITE_LOCK, as every other writer of these files does.

    Bundles merge as an import does — votes counted once per source however
    often the same bundle arrives, this install's authored corners kept.
    Corrections, survey logs and car widths are added where this install
    has none of its own, and never replace one. A file that does not
    validate is listed under `failed` and the rest still go.

    Returns what changed, with `tracks`: every circuit whose bundle or
    corrections were written, for the caller to re-judge and re-sync.
    """
    merged: list[dict[str, Any]] = []
    corrections: list[str] = []
    logs: list[str] = []
    failed: list[dict[str, str]] = []
    tracks: list[str] = []
    widths = 0
    with zipfile.ZipFile(upload) as zf:
        names = zf.namelist()
        for name in sorted(n for n in names if _BUNDLE.fullmatch(n)):
            try:
                doc = track_bundle.validate_document(json.loads(_read(zf, name)))
                result = track_bundle.merge_document(data_dir, doc)
            except (ValueError, zipfile.BadZipFile) as exc:  # BundleError is a ValueError
                failed.append({"file": name, "error": str(exc)})
                continue
            merged.append(
                {"track": result["track"], "slug": result["slug"],
                 "added_points": result["added_points"]}
            )
            tracks.append(result["track"])
        for name in sorted(names):
            match = _CORRECTIONS.fullmatch(name)
            if match is None:
                continue
            slug = match.group(1)
            try:
                raw = _read(zf, name)
            except (TooLarge, zipfile.BadZipFile) as exc:
                failed.append({"file": name, "error": str(exc)})
                continue
            # Looked for and written in one hold of the lock: corrections
            # saved here meanwhile are this install's own, and are kept.
            with track_bundle.WRITE_LOCK:
                if track_corrections.path(data_dir, slug).exists():
                    continue
                error = _add_corrections(data_dir, slug, raw)
            if error:
                failed.append({"file": name, "error": error})
                continue
            bundle = track_bundle.load_slug(data_dir, slug)
            assert bundle is not None  # _add_corrections checked
            corrections.append(bundle["meta"]["track"])
            tracks.append(bundle["meta"]["track"])
        for name in sorted(n for n in names if _LOG.fullmatch(n)):
            try:
                if _add_file(zf, name, data_dir / name):
                    logs.append(name)
            except zipfile.BadZipFile as exc:
                failed.append({"file": name, "error": str(exc)})
        if car_width.FILENAME in names:
            try:
                widths = _add_car_widths(data_dir, json.loads(_read(zf, car_width.FILENAME)))
            except (ValueError, zipfile.BadZipFile) as exc:
                failed.append({"file": car_width.FILENAME, "error": str(exc)})
    return {
        "bundles": merged,
        "corrections": corrections,
        "logs": logs,
        "car_widths": widths,
        "failed": failed,
        "tracks": list(dict.fromkeys(tracks)),
    }


def _add_corrections(data_dir: Path, slug: str, raw: bytes) -> str:
    """Keep a backup's corrections for a circuit that has none; the error, or ''.
    The checks PUT /api/track-bundles/{slug}/corrections makes."""
    bundle = track_bundle.load_slug(data_dir, slug)
    if bundle is None:
        return "no bundle for its circuit here"
    try:
        doc = track_corrections.validate(json.loads(raw))
    except ValueError as exc:
        return f"invalid corrections: {exc}"
    official_id = str((bundle["meta"].get("official") or {}).get("official_id") or "")
    if official_id and doc["official_id"] != official_id:
        return (
            f"corrections are for layout {doc['official_id']!r}; "
            f"the bundle here is {official_id!r}"
        )
    if not track_corrections.is_empty(doc):
        track_corrections.write(data_dir, slug, doc)
    return ""


def _add_file(zf: zipfile.ZipFile, name: str, dest: Path) -> bool:
    """Unpack a member to `dest` unless something is there already. Claimed
    with a link, as an uploaded survey log is: never over an existing file."""
    if dest.exists():
        return False
    part = dest.with_name(f".restore-{secrets.token_hex(6)}.part")
    try:
        with zf.open(name) as src, part.open("wb") as out:
            shutil.copyfileobj(src, out, CHUNK)
        try:
            os.link(part, dest)
        except FileExistsError:
            return False
    finally:
        part.unlink(missing_ok=True)
    return True


@track_bundle.serialized
def _add_car_widths(data_dir: Path, doc: Any) -> int:
    """Add the widths of cars this install has not measured; how many."""
    if not isinstance(doc, dict) or doc.get("format") != car_width.FORMAT:
        raise ValueError("not a car widths file")
    cars = doc.get("cars")
    if not isinstance(cars, dict):
        raise ValueError("no cars")
    here = car_width.load(data_dir)
    added: dict[int, dict[str, Any]] = {}
    for key, entry in cars.items():
        try:
            car_id = int(key)
        except (TypeError, ValueError):
            continue
        width = entry.get("width_m") if isinstance(entry, dict) else None
        samples = entry.get("samples") if isinstance(entry, dict) else None
        if car_id in here or not isinstance(width, int | float) or not isinstance(samples, int):
            continue
        if not 0.5 <= width <= 3.0:
            continue
        added[car_id] = {
            "width_m": round(float(width), 3),
            "samples": samples,
            "car": str(entry.get("car", ""))[:120],
            "updated_at": str(entry.get("updated_at", ""))[:40],
        }
    if added:
        car_width.save(data_dir, {**here, **added})
    return len(added)


def restore_source_id(upload: Path, data_dir: Path) -> str:
    """Make this install the one that made the backup, in the survey votes."""
    with zipfile.ZipFile(upload) as zf:
        try:
            doc = json.loads(_read(zf, track_bundle.SOURCE_FILE))
        except (KeyError, ValueError, zipfile.BadZipFile) as exc:
            raise RestoreError(f"{track_bundle.SOURCE_FILE} does not read") from exc
    value = doc.get("source_id") if isinstance(doc, dict) else None
    if not isinstance(value, str) or not track_bundle.is_source_id(value):
        raise RestoreError(f"{track_bundle.SOURCE_FILE} holds no install id")
    track_bundle.set_source_id(data_dir, value)
    return value


def read_tokens(upload: Path) -> dict[str, str]:
    """The credentials in a backup's secrets.json, as SecretStore reads them."""
    with zipfile.ZipFile(upload) as zf:
        try:
            doc = json.loads(_read(zf, secret_store.FILENAME))
        except (KeyError, ValueError, zipfile.BadZipFile) as exc:
            raise RestoreError(f"{secret_store.FILENAME} does not read") from exc
    values = doc.get("settings") if isinstance(doc, dict) else None
    if not isinstance(values, dict) or doc.get("format") != secret_store.FORMAT:
        raise RestoreError(f"{secret_store.FILENAME} is not a secrets file")
    return {k: values[k] for k in secret_store.KEYS if isinstance(values.get(k), str)}
