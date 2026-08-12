"""Schema migrations (#14).

The interesting case is not the fresh database — it is the one that already
exists on somebody's Raspberry Pi, was built one hand-rolled `ADD COLUMN` at a
time, and must arrive at the same schema as a fresh install without losing a
single recorded lap.
"""

import sqlite3

import pytest
from alembic.script import ScriptDirectory
from sqlalchemy import inspect

from app.storage.db import (
    BASELINE_REVISION,
    Base,
    alembic_config,
    init_db,
    make_engine,
    make_session_factory,
)
from app.storage.repository import Repository

# A database as the very first release wrote it: no track_name, no aid
# metrics, no events, no category — and no alembic_version.
V1_SCHEMA = """
CREATE TABLE sessions (
  id INTEGER NOT NULL PRIMARY KEY, started_at VARCHAR NOT NULL,
  car_id INTEGER NOT NULL, car_name VARCHAR NOT NULL, note VARCHAR NOT NULL);
CREATE TABLE tracks (
  id INTEGER NOT NULL PRIMARY KEY, name VARCHAR NOT NULL, length_m FLOAT NOT NULL,
  min_x FLOAT NOT NULL, max_x FLOAT NOT NULL, min_z FLOAT NOT NULL,
  max_z FLOAT NOT NULL, created_at VARCHAR NOT NULL);
CREATE TABLE settings (key VARCHAR NOT NULL PRIMARY KEY, value VARCHAR NOT NULL);
CREATE TABLE laps (
  id INTEGER NOT NULL PRIMARY KEY, session_id INTEGER NOT NULL, number INTEGER NOT NULL,
  time_ms INTEGER NOT NULL, finished_at VARCHAR NOT NULL, car_id INTEGER NOT NULL,
  fuel_start FLOAT NOT NULL, fuel_end FLOAT NOT NULL, fuel_consumed FLOAT NOT NULL,
  full_throttle_pct FLOAT NOT NULL, full_brake_pct FLOAT NOT NULL,
  coasting_pct FLOAT NOT NULL, tire_spin_pct FLOAT NOT NULL, max_speed FLOAT NOT NULL,
  min_body_height FLOAT NOT NULL, total_ticks INTEGER NOT NULL, samples_json TEXT NOT NULL,
  FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE);
CREATE INDEX ix_laps_session ON laps (session_id);
INSERT INTO sessions VALUES (1, '2024-01-01T00:00:00Z', 42, 'Mazda Roadster', '');
INSERT INTO laps VALUES
  (1, 1, 1, 91234, '2024-01-01T00:01:31Z', 42, 50, 49, 1, 61, 12, 27, 3, 214, 78, 5400, '{}');
"""


def _columns(path, table) -> set[str]:
    con = sqlite3.connect(path)
    try:
        return {row[1] for row in con.execute(f"PRAGMA table_info({table})")}
    finally:
        con.close()


def _revision(path) -> str | None:
    con = sqlite3.connect(path)
    try:
        row = con.execute("SELECT version_num FROM alembic_version").fetchone()
        return row[0] if row else None
    except sqlite3.OperationalError:
        return None
    finally:
        con.close()


def test_migration_history_has_one_head() -> None:
    """Two heads means `upgrade head` is ambiguous and startup would fail on
    somebody else's machine, not on the one that added the second branch."""
    heads = ScriptDirectory.from_config(alembic_config()).get_heads()
    assert len(heads) == 1, heads


async def test_fresh_database_is_created_and_stamped(tmp_path) -> None:
    path = tmp_path / "fresh.db"
    engine = make_engine(path)
    await init_db(engine)
    async with engine.connect() as conn:
        tables = set(await conn.run_sync(lambda c: inspect(c).get_table_names()))
    await engine.dispose()

    assert {t.name for t in Base.metadata.sorted_tables} <= tables
    assert "alembic_version" in tables
    assert _revision(path) is not None


async def test_init_db_is_idempotent(tmp_path) -> None:
    path = tmp_path / "twice.db"
    engine = make_engine(path)
    await init_db(engine)
    await init_db(engine)
    await init_db(engine)
    await engine.dispose()
    assert _revision(path) is not None


async def test_pre_alembic_database_is_caught_up_and_stamped(tmp_path) -> None:
    path = tmp_path / "legacy.db"
    con = sqlite3.connect(path)
    con.executescript(V1_SCHEMA)
    con.commit()
    con.close()

    engine = make_engine(path)
    await init_db(engine)

    assert _revision(path) == BASELINE_REVISION
    # Every column a fresh install would have, on a file that had none of them.
    assert _columns(path, "laps") == {c.name for c in Base.metadata.tables["laps"].columns}
    assert _columns(path, "sessions") == {
        c.name for c in Base.metadata.tables["sessions"].columns
    }

    # And the recorded lap is still there, readable through the ORM.
    repo = Repository(make_session_factory(engine))
    sessions = await repo.list_sessions()
    laps = await repo.list_laps()
    await engine.dispose()
    assert [s["car_name"] for s in sessions] == ["Mazda Roadster"]
    assert [lap["time_ms"] for lap in laps] == [91234]
    # Columns the file never had come back as their declared defaults, not NULL.
    assert laps[0]["counts_for_best"] is True
    assert laps[0]["car_category"] == ""
    assert laps[0]["off_track_count"] == -1


async def test_legacy_catch_up_leaves_a_partly_upgraded_file_alone(tmp_path) -> None:
    """A database from somewhere in the middle of the hand-rolled era: some of
    the added columns present, some not. It must not be re-ALTERed for the
    ones it has."""
    path = tmp_path / "middle.db"
    con = sqlite3.connect(path)
    con.executescript(V1_SCHEMA)
    con.executescript(
        "ALTER TABLE sessions ADD COLUMN track_name VARCHAR NOT NULL DEFAULT '';"
        "ALTER TABLE laps ADD COLUMN tod_ms INTEGER NOT NULL DEFAULT -1;"
        "UPDATE sessions SET track_name = 'Suzuka';"
    )
    con.commit()
    con.close()

    engine = make_engine(path)
    await init_db(engine)
    repo = Repository(make_session_factory(engine))
    sessions = await repo.list_sessions()
    await engine.dispose()

    assert _revision(path) == BASELINE_REVISION
    assert sessions[0]["track_name"] == "Suzuka"
    assert _columns(path, "laps") == {c.name for c in Base.metadata.tables["laps"].columns}


@pytest.mark.parametrize("table", ["laps", "sessions", "tracks", "layouts", "settings"])
async def test_baseline_matches_the_orm_models(tmp_path, table) -> None:
    """The baseline revision and the ORM must not drift: the next revision is
    autogenerated by diffing them, and a mismatch here becomes a spurious
    'drop this column' in whatever comes next."""
    engine = make_engine(tmp_path / f"{table}.db")
    await init_db(engine)
    async with engine.connect() as conn:
        columns = await conn.run_sync(lambda c: inspect(c).get_columns(table))
    await engine.dispose()
    assert {c["name"] for c in columns} == {
        c.name for c in Base.metadata.tables[table].columns
    }
