"""What a start does before it migrates (#182): refuse a database a newer
version wrote, copy one it is about to upgrade, and upgrade it atomically.

Every database here is built at a past revision with Alembic itself and
given a session and a lap, so the upgrade has something to lose.
"""

import hashlib
import json
import logging
import shutil
import sqlite3
from collections import namedtuple
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import create_engine, text

from app import main
from app.config import Settings
from app.netinfo import server_version
from app.storage import db as db_module
from app.storage import upgrade_backup
from app.storage.db import (
    BUSY_TIMEOUT_MS,
    SCHEMA_WRITTEN_BY_KEY,
    DatabaseTooNew,
    NoRoomForBackup,
    alembic_config,
    init_db,
    make_engine,
    make_migration_engine,
    make_session_factory,
)
from app.storage.repository import Repository
from tests.test_migrations import _head, _revision

MIGRATIONS = Path(db_module.__file__).resolve().parents[1] / "migrations"


def _at_revision(path: Path, revision: str) -> None:
    """A database at `revision` holding one session and one lap."""
    sync = create_engine(f"sqlite:///{path}")
    cfg = alembic_config()
    with sync.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, revision)
    sync.dispose()
    con = sqlite3.connect(path)
    with con:
        for table, given in (
            ("sessions", {"id": 1, "started_at": "2026-01-01T00:00:00Z", "car_name": "Roadster"}),
            ("laps", {"id": 1, "session_id": 1, "number": 1, "time_ms": 91234,
                      "samples_json": "{}"}),
        ):
            row = dict(given)
            # Every other required column at a neutral value: the schema at
            # this revision, not the models, says which there are.
            for _, name, kind, notnull, default, _pk in con.execute(
                f"PRAGMA table_info({table})"
            ):
                if name not in row and notnull and default is None:
                    row[name] = "" if "CHAR" in kind.upper() or "TEXT" in kind.upper() else 0
            names = ", ".join(row)
            marks = ", ".join("?" * len(row))
            con.execute(f"INSERT INTO {table} ({names}) VALUES ({marks})", list(row.values()))
    con.close()


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _copies(backups: Path) -> list[Path]:
    return sorted(backups.glob("pre-upgrade-*")) if backups.exists() else []


async def _laps(path: Path) -> list[int]:
    engine = make_engine(path)
    try:
        laps = await Repository(make_session_factory(engine)).list_laps()
        return [lap["time_ms"] for lap in laps]
    finally:
        await engine.dispose()


# --- a newer database -----------------------------------------------------------------


def _from_the_future(path: Path) -> None:
    _at_revision(path, "head")
    con = sqlite3.connect(path)
    with con:
        con.execute("UPDATE alembic_version SET version_num = '0099_from_the_future'")
        con.execute(
            "INSERT INTO settings (key, value) VALUES (?, '0.99.0')", (SCHEMA_WRITTEN_BY_KEY,)
        )
    con.close()


async def test_a_database_from_a_newer_version_is_refused_and_left_alone(tmp_path) -> None:
    path = tmp_path / "gt7.db"
    _from_the_future(path)
    before = _digest(path)
    backups = tmp_path / "backups"

    engine = make_engine(path)
    with pytest.raises(DatabaseTooNew) as refused:
        await init_db(engine, backups=backups)
    await engine.dispose()

    assert refused.value.stored == "0099_from_the_future"
    assert refused.value.written_by == "0.99.0"
    assert "0.99.0" in str(refused.value) and server_version() in str(refused.value)
    assert _digest(path) == before
    assert not path.with_name("gt7.db-journal").exists()
    assert _copies(backups) == []


async def test_a_refused_start_says_why_in_one_line_without_a_traceback(
    tmp_path, monkeypatch, caplog
) -> None:
    """The lifespan logs the refusal and the server is told the start failed,
    without the traceback uvicorn would print from Starlette's report."""
    path = tmp_path / "gt7.db"
    _from_the_future(path)
    monkeypatch.setattr(main, "get_settings", lambda: Settings(source="sim", db_path=path))
    monkeypatch.setattr(main, "configure_logging", lambda level: None)

    app = main.create_app()
    sent: list[dict] = []
    incoming = iter([{"type": "lifespan.startup"}])

    async def receive() -> dict:
        return next(incoming)

    async def send(message) -> None:
        sent.append(dict(message))

    with caplog.at_level(logging.ERROR), pytest.raises(DatabaseTooNew):
        await app({"type": "lifespan", "asgi": {"version": "3.0"}, "state": {}}, receive, send)

    assert sent == [{"type": "lifespan.startup.failed", "message": ""}]
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1
    assert "newer version" in errors[0].getMessage() and errors[0].exc_info is None


# --- the copy before an upgrade -------------------------------------------------------


async def test_an_upgrade_is_preceded_by_a_copy_with_its_manifest(tmp_path) -> None:
    path = tmp_path / "gt7.db"
    _at_revision(path, "0012_session_revision")
    backups = tmp_path / "backups"

    engine = make_engine(path)
    await init_db(engine, backups=backups)
    await engine.dispose()

    [copy] = _copies(backups)
    assert copy.name.startswith("pre-upgrade-") and copy.name.endswith("-0012_session_revision")
    assert not list(backups.glob("*.partial"))
    manifest = json.loads((copy / "manifest.json").read_text())
    assert manifest["format"] == "gt7-datalogger-pre-upgrade"
    assert manifest["app_version"] == server_version()
    assert manifest["from_revision"] == "0012_session_revision"
    assert manifest["to_revision"] == _head()
    assert manifest["quick_check"] == "ok"
    assert manifest["counts"]["sessions"] == 1 and manifest["counts"]["laps"] == 1
    assert manifest["database_bytes"] == (copy / "gt7.db").stat().st_size
    # The copy is the database as it was; the live one moved on.
    assert _revision(copy / "gt7.db") == "0012_session_revision"
    assert _revision(path) == _head()
    con = sqlite3.connect(copy / "gt7.db")
    assert con.execute("SELECT time_ms FROM laps").fetchall() == [(91234,)]
    con.close()
    assert await _laps(path) == [91234]

    # Who migrated it is on record, for a build too old to open it.
    con = sqlite3.connect(path)
    assert con.execute(
        "SELECT value FROM settings WHERE key = ?", (SCHEMA_WRITTEN_BY_KEY,)
    ).fetchone() == (server_version(),)
    con.close()

    # Nothing to upgrade the next time: no new copy.
    engine = make_engine(path)
    await init_db(engine, backups=backups)
    await engine.dispose()
    assert _copies(backups) == [copy]


async def test_a_fresh_database_gets_no_copy(tmp_path) -> None:
    backups = tmp_path / "backups"
    engine = make_engine(tmp_path / "gt7.db")
    await init_db(engine, backups=backups)
    await engine.dispose()
    assert _copies(backups) == []


@pytest.mark.parametrize(("desktop", "kept"), [("", 1), ("1", 3)])
async def test_only_the_newest_copies_are_kept(tmp_path, monkeypatch, desktop, kept) -> None:
    monkeypatch.setenv("GT7_DESKTOP", desktop)
    backups = tmp_path / "backups"
    for stamp in ("20250101T000000Z", "20250201T000000Z", "20250301T000000Z"):
        (backups / f"pre-upgrade-{stamp}-0009_session_car_figures").mkdir(parents=True)
    # Left by a start killed mid-copy: never a backup, always cleared.
    (backups / "pre-upgrade-20250401T000000Z-0010_lap_best_override.partial").mkdir()
    path = tmp_path / "gt7.db"
    _at_revision(path, "0012_session_revision")

    engine = make_engine(path)
    await init_db(engine, backups=backups)
    await engine.dispose()

    names = [p.name for p in _copies(backups)]
    assert len(names) == kept
    assert names[-1].endswith("-0012_session_revision")
    assert names[:-1] == [
        "pre-upgrade-20250201T000000Z-0009_session_car_figures",
        "pre-upgrade-20250301T000000Z-0009_session_car_figures",
    ][3 - kept:]


async def test_no_room_for_the_copy_means_no_upgrade(tmp_path, monkeypatch, caplog) -> None:
    path = tmp_path / "gt7.db"
    _at_revision(path, "0012_session_revision")
    before = _digest(path)
    size = path.stat().st_size
    usage = namedtuple("usage", "total used free")
    # Twice the file free, short of the 2.2 times a copy and a migration need.
    monkeypatch.setattr(
        upgrade_backup.shutil, "disk_usage", lambda _p: usage(1 << 40, 0, 2 * size)
    )

    engine = make_engine(path)
    with caplog.at_level(logging.WARNING), pytest.raises(NoRoomForBackup) as refused:
        await init_db(engine, backups=tmp_path / "backups")
    await engine.dispose()

    assert "0012_session_revision" in str(refused.value)
    assert any(r.levelno == logging.WARNING and "MB free" in r.getMessage() for r in caplog.records)
    assert _digest(path) == before
    assert _copies(tmp_path / "backups") == []


# --- one transaction ------------------------------------------------------------------

FAILING = '''"""A revision that does some of its work and then fails.

Revision ID: 0099_fails_half_way
Revises: {down}
"""

import sqlalchemy as sa
from alembic import op

revision = "0099_fails_half_way"
down_revision = "{down}"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("laps", sa.Column("half_done", sa.Integer(), nullable=True))
    op.create_table("half_made", sa.Column("id", sa.Integer(), primary_key=True))
    op.execute("UPDATE sessions SET note = 'touched'")
    raise RuntimeError("the migration failed half way")


def downgrade() -> None:
    pass
'''


async def test_a_failing_migration_leaves_the_database_where_it_was(
    tmp_path, monkeypatch, caplog
) -> None:
    """0012 → 0013 → a revision that adds a column, a table and an update and
    then raises: the whole upgrade rolls back, 0013's column included."""
    scripts = tmp_path / "migrations"
    shutil.copytree(MIGRATIONS, scripts, ignore=shutil.ignore_patterns("__pycache__"))
    (scripts / "versions" / "0099_fails_half_way.py").write_text(
        FAILING.format(down=_head())
    )

    def config():
        cfg = alembic_config()
        cfg.set_main_option("script_location", str(scripts))
        return cfg

    path = tmp_path / "gt7.db"
    _at_revision(path, "0012_session_revision")
    monkeypatch.setattr(db_module, "alembic_config", config)

    engine = make_engine(path)
    with caplog.at_level(logging.ERROR), pytest.raises(RuntimeError, match="half way"):
        await init_db(engine, backups=tmp_path / "backups")
    await engine.dispose()

    assert _revision(path) == "0012_session_revision"
    con = sqlite3.connect(path)
    columns = {row[1] for row in con.execute("PRAGMA table_info(laps)")}
    tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    notes = con.execute("SELECT note FROM sessions").fetchall()
    laps = con.execute("SELECT time_ms FROM laps").fetchall()
    con.close()
    assert "wheelbase_m" not in columns and "half_done" not in columns
    assert "half_made" not in tables
    assert notes == [("",)]
    assert laps == [(91234,)]
    assert any("rolled back" in r.getMessage() for r in caplog.records)
    # The copy taken before is there to go back to, should it be wanted.
    [copy] = _copies(tmp_path / "backups")
    assert _revision(copy / "gt7.db") == "0012_session_revision"

    # And with the broken revision gone, the next start upgrades as normal.
    monkeypatch.undo()
    engine = make_engine(path)
    await init_db(engine)
    await engine.dispose()
    assert _revision(path) == _head()
    assert await _laps(path) == [91234]


# --- pragmas --------------------------------------------------------------------------


async def _pragmas(engine) -> dict[str, int]:
    async with engine.connect() as conn:
        return {
            name: (await conn.execute(text(f"PRAGMA {name}"))).scalar_one()
            for name in ("foreign_keys", "busy_timeout")
        }


async def test_every_connection_waits_for_locks_and_leaves_foreign_keys_off(tmp_path) -> None:
    path = tmp_path / "gt7.db"
    for engine in (make_engine(path), make_migration_engine(path)):
        try:
            assert await _pragmas(engine) == {"foreign_keys": 0, "busy_timeout": BUSY_TIMEOUT_MS}
        finally:
            await engine.dispose()


async def test_a_0010_database_keeps_its_lap_through_every_migration(tmp_path) -> None:
    """With foreign_keys on, 0011's rebuild of `sessions` cascades into
    deleting every lap. Upgraded here under exactly the pragmas the app sets,
    the lap must come through."""
    path = tmp_path / "gt7.db"
    _at_revision(path, "0010_lap_best_override")
    engine = make_engine(path)
    await init_db(engine, backups=tmp_path / "backups")
    assert await _pragmas(engine) == {"foreign_keys": 0, "busy_timeout": BUSY_TIMEOUT_MS}
    await engine.dispose()

    assert _revision(path) == _head()
    assert await _laps(path) == [91234]
