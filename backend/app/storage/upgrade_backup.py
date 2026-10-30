"""The copy of the database taken before a start migrates it (#182).

A migration rewrites tables in place. It runs in one transaction, so one
that fails leaves the file as it was, but one that succeeds and turns out
wrong — or a build that has to be rolled back — leaves no way back without
a copy from before. So when a start finds the schema behind this build's,
the file is copied first, with SQLite's backup API, to

    <data>/backups/pre-upgrade-<UTC>-<revision>/gt7.db
    <data>/backups/pre-upgrade-<UTC>-<revision>/manifest.json

written into a `.partial` folder that takes its name only once the manifest
is in it: a start killed mid-copy leaves a `.partial`, which the next copy
clears away, never something that looks like a good backup.

Only the newest copies are kept: one, or three on the desktop app, where a
user is likelier to try an older build and want the data it last saw. A Pi's
SD card holding a 900 MB database has room for one more copy, not several.

Putting a copy back is by hand, with the app stopped: copy its gt7.db over
the one in the data directory, and start the build that matches its
manifest's `app_version`.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.netinfo import server_version

log = logging.getLogger(__name__)

FORMAT = "gt7-datalogger-pre-upgrade"
DIRECTORY = "backups"
PREFIX = "pre-upgrade-"
PARTIAL = ".partial"
DATABASE = "gt7.db"
MANIFEST = "manifest.json"

# Free space wanted beside the database before a copy is taken: the copy
# itself, and as much again for the migration, which rebuilds tables in place
# (0011 rebuilt `sessions`; a later one may rebuild `laps`) and holds the old
# pages in the rollback journal until it commits; and some to spare.
SPACE_FACTOR = 2.2
# Copies kept: on the desktop app, and everywhere else.
KEEP_DESKTOP = 3
KEEP = 1
# Counted into the manifest: what a person checking a copy wants to see.
COUNTED = ("sessions", "laps", "tracks", "settings", "layouts")


def keep_count() -> int:
    """How many pre-upgrade copies to keep. GT7_DESKTOP is read directly:
    the desktop launcher sets it, and nothing else needs to know of it yet."""
    return KEEP_DESKTOP if os.environ.get("GT7_DESKTOP") == "1" else KEEP


def room_problem(db_path: Path, backups: Path) -> str:
    """Why there is not room for a copy of `db_path` and its migration, or ''."""
    needed = int(SPACE_FACTOR * db_path.stat().st_size)
    where = backups if backups.exists() else backups.parent
    free = shutil.disk_usage(where).free
    if free >= needed:
        return ""
    return (
        f"a copy of the database before upgrading it needs {needed >> 20} MB free "
        f"beside it; {free >> 20} MB is"
    )


def back_up(db_path: Path, backups: Path, from_revision: str, to_revision: str) -> Path:
    """Copy the database at `db_path` into a new pre-upgrade folder in
    `backups`, then keep only the newest copies. Returns the folder.

    Blocking. Taken at startup, before anything else has the file open, so
    the copy is made in one step rather than backup.py's short ones.
    """
    backups.mkdir(parents=True, exist_ok=True)
    for stale in backups.glob(f"{PREFIX}*{PARTIAL}"):
        shutil.rmtree(stale, ignore_errors=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    name = f"{PREFIX}{stamp}-{from_revision}"
    final = backups / name
    n = 2
    while final.exists():
        final = backups / f"{name}-{n}"
        n += 1
    partial = final.with_name(final.name + PARTIAL)
    partial.mkdir()
    try:
        copy = partial / DATABASE
        with (
            closing(sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)) as src,
            closing(sqlite3.connect(copy)) as dest,
        ):
            src.backup(dest)
        manifest = describe(copy)
        manifest.update(
            {
                "format": FORMAT,
                "app_version": server_version(),
                "from_revision": from_revision,
                "to_revision": to_revision,
                "made_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "source": str(db_path),
            }
        )
        (partial / MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n")
        partial.rename(final)
    except BaseException:
        shutil.rmtree(partial, ignore_errors=True)
        raise
    if manifest["quick_check"] != "ok":
        log.warning(
            "the database copied before upgrading fails its integrity check: %s",
            manifest["quick_check"],
        )
    prune(backups, keep_count())
    return final


def describe(copy: Path) -> dict[str, Any]:
    """The copy's size, row counts and `quick_check` verdict."""
    with closing(sqlite3.connect(f"{copy.resolve().as_uri()}?mode=ro", uri=True)) as db:
        tables = {
            row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        counts = {
            table: db.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
            for table in COUNTED
            if table in tables
        }
        check = [str(row[0]) for row in db.execute("PRAGMA quick_check")]
    return {
        "database_bytes": copy.stat().st_size,
        "counts": counts,
        "quick_check": "ok" if check == ["ok"] else "; ".join(check),
    }


def prune(backups: Path, keep: int) -> list[Path]:
    """Remove all but the newest `keep` finished copies; returns those removed.
    The UTC stamp leads every name, so name order is age order."""
    done = sorted(
        p for p in backups.glob(f"{PREFIX}*") if p.is_dir() and not p.name.endswith(PARTIAL)
    )
    gone = done[: max(len(done) - keep, 0)]
    for old in gone:
        shutil.rmtree(old, ignore_errors=True)
    return gone
