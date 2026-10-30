"""Database engine + ORM models. SQLite by default; any SQLAlchemy async URL works.

Schema changes go through Alembic (`app/migrations/`), not through this file:
`init_db` upgrades to head at startup. See `docs/internals/architecture.md`.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any, Literal

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import (
    Boolean,
    Connection,
    Engine,
    ForeignKey,
    Index,
    Text,
    delete,
    event,
    func,
    insert,
    inspect,
    select,
)
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    column_property,
    mapped_column,
    relationship,
)
from sqlalchemy.pool import NullPool

from app.netinfo import server_version
from app.storage import upgrade_backup

log = logging.getLogger(__name__)

# The revision every pre-Alembic database is stamped at once it has been
# brought up to that shape (see _catch_up_legacy).
BASELINE_REVISION = "0001_baseline"

# The settings row naming the app version that last migrated the database,
# so a build too old to open it can say which version it needs.
SCHEMA_WRITTEN_BY_KEY = "schema_written_by"

# How long a connection waits for another's lock before "database is locked":
# a lap saved while Compact or a backup holds the file waits it out rather
# than being lost (#188).
BUSY_TIMEOUT_MS = 30_000

# Reasons shown beside excluded times, including automatic race-start exclusions.
ExcludeReason = Literal["off-track", "contact", "restart", "dirty", "pit-out", "race-start"]


class Base(DeclarativeBase):
    pass


class SessionRow(Base):
    __tablename__ = "sessions"
    # An id is used once for the life of the database, deletes included
    # (migration 0011): the sync client and the Analysis view remember
    # sessions by id.
    __table_args__ = {"sqlite_autoincrement": True}

    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[str]
    car_id: Mapped[int]
    car_name: Mapped[str]
    # Broadcast in packet C ("Gr.3", "Gr.4", "N300"...). Stored on the lap too,
    # denormalised like car_id, so category filtering never needs the join.
    car_category: Mapped[str] = mapped_column(default="")
    # From the car inventory (#57), denormalised beside car_name for the same
    # reason it is: a session row should describe the car without a second
    # lookup, and the answer must survive an inventory that later drops the
    # car. Written at session creation and re-filled at startup whenever the
    # inventory changes (app/main.py), so sessions recorded before a car was
    # known get their fields once it is. Empty/0 = the inventory has no answer;
    # for year that is genuinely common (race cars carry no model year).
    car_manufacturer: Mapped[str] = mapped_column(default="")
    car_year: Mapped[int] = mapped_column(default=0)
    car_drivetrain: Mapped[str] = mapped_column(default="")
    car_aspiration: Mapped[str] = mapped_column(default="")
    # The car's full published name ("Nissan Skyline GTS-R (R31) '87"), where
    # car_name is the short form GT7 shows in-game ("Skyline GTS-R (R31) '87").
    car_full_name: Mapped[str] = mapped_column(default="")
    # The published figures, in the source's own units. 0 = not published,
    # which is common and meaningful: an EV has no displacement, a kart no
    # measured torque. Denormalised with the rest so the row is the whole
    # answer about the car that ran this session, not a key into a file that
    # may describe it differently later.
    car_displacement_cc: Mapped[int] = mapped_column(default=0)
    car_power_bhp: Mapped[int] = mapped_column(default=0)
    car_torque_kgfm: Mapped[float] = mapped_column(default=0.0)
    car_weight_kg: Mapped[int] = mapped_column(default=0)
    car_length_mm: Mapped[int] = mapped_column(default=0)
    car_width_mm: Mapped[int] = mapped_column(default=0)
    car_height_mm: Mapped[int] = mapped_column(default=0)
    car_performance_points: Mapped[float] = mapped_column(default=0.0)
    note: Mapped[str] = mapped_column(default="")
    # User-set labels ("wet", "race sim"), comma-separated (#25). Tags may not
    # contain commas — the PATCH endpoint enforces it — so the join is
    # unambiguous without a JSON column.
    tags: Mapped[str] = mapped_column(default="")
    track_name: Mapped[str] = mapped_column(default="")
    # User-set: GT7 records replays exactly like driving — no packet field
    # distinguishes a TT leader's replay from your own laps — so keeping a
    # recorded replay (or another driver's laps) off the personal-best surfaces
    # is a human call, made per session. Excluded sessions stay fully browsable;
    # they just never own a best (#26).
    bests_excluded: Mapped[bool] = mapped_column(default=False)
    # The race result (#60), written once at the checkered-flag edge (GT7
    # reports current_lap = total_laps + 1 after the finish). -1 = no result:
    # a time trial (GT7 sends -1 for position where there is no reporting) or
    # a stream that ended mid-race — deliberately distinct from finishing
    # last, which is >= 2. final_position means "position AT the finish", not
    # at the last recorded lap; a session that never saw the flag keeps -1.
    final_position: Mapped[int] = mapped_column(default=-1)
    final_total_positions: Mapped[int] = mapped_column(default=-1)
    # The race distance (packet total_laps); 0 = not a lapped race.
    race_laps: Mapped[int] = mapped_column(default=0)
    # Sum of the stored lap times, and NULL unless every lap 1..race_laps is
    # accounted for: no packet field carries the real total, and a sum over a
    # session with a missing lap (dropped stream, mid-race join) is a
    # confidently wrong number. tod_ms is NOT a substitute — it is in-game
    # time of day and advances at the event's own rate multiplier.
    race_time_ms: Mapped[int | None] = mapped_column(default=None)
    # Bumped by every write to the session or its laps (Repository._touch),
    # to one more than the table's largest: what a client keeping a copy asks
    # "what changed since N" with (#142).
    revision: Mapped[int] = mapped_column(default=0)

    laps: Mapped[list[LapRow]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )


class TrackRow(Base):
    """Named tracks identified by their geometric signature."""

    __tablename__ = "tracks"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    length_m: Mapped[float]
    min_x: Mapped[float]
    max_x: Mapped[float]
    min_z: Mapped[float]
    max_z: Mapped[float]
    created_at: Mapped[str]
    # "user" (a human named this circuit) or "seed" (computed offline and
    # shipped with the build, #58). The distinction is load-bearing twice: a
    # re-sync replaces every seed row and no user row, and identification
    # prefers a name a person chose over one we inferred.
    provenance: Mapped[str] = mapped_column(default="user")
    # The GT7 configuration a seeded row came from. Empty for user rows —
    # nothing joins a typed name to the catalog, which is the whole reason
    # track_catalog.py suggests rather than decides.
    official_id: Mapped[str] = mapped_column(default="")
    # The seeded racing line as compact JSON [[x, z], ...], in DRIVING ORDER,
    # and the reverse configuration of the same layout when GT7 has one (#58).
    # Order is what a bounding box throws away and what tells a layout from
    # its reverse. Empty on user rows: they carry no path, and they describe
    # whichever way round their author happened to drive.
    path_json: Mapped[str] = mapped_column(default="")
    reverse_id: Mapped[str] = mapped_column(default="")
    reverse_name: Mapped[str] = mapped_column(default="")


class SettingRow(Base):
    """Runtime-configurable settings (override env defaults at startup)."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(primary_key=True)
    value: Mapped[str]


class LayoutRow(Base):
    """Named overlay/dashboard layouts (v2 grid configs, stored as JSON)."""

    __tablename__ = "layouts"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(unique=True)
    kind: Mapped[str] = mapped_column(default="overlay")  # "overlay" | "dash"
    config_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str]
    updated_at: Mapped[str]


class LapRow(Base):
    __tablename__ = "laps"
    __table_args__ = (Index("ix_laps_session", "session_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"))
    number: Mapped[int]
    time_ms: Mapped[int]
    finished_at: Mapped[str]
    car_id: Mapped[int]
    car_category: Mapped[str] = mapped_column(default="")
    fuel_start: Mapped[float]
    fuel_end: Mapped[float]
    fuel_consumed: Mapped[float]
    full_throttle_pct: Mapped[float]
    full_brake_pct: Mapped[float]
    coasting_pct: Mapped[float]
    tire_spin_pct: Mapped[float]
    max_speed: Mapped[float]
    min_body_height: Mapped[float]
    total_ticks: Mapped[int]
    tod_ms: Mapped[int] = mapped_column(default=-1)  # in-game time of day at lap end
    tcs_active_pct: Mapped[float] = mapped_column(default=0.0)
    asm_active_pct: Mapped[float] = mapped_column(default=0.0)
    max_water_temp: Mapped[float] = mapped_column(default=0.0)
    max_oil_temp: Mapped[float] = mapped_column(default=0.0)
    min_oil_pressure: Mapped[float] = mapped_column(default=-1.0)
    # The span heuristic's verdict: False for partial laps (pit out-laps).
    # Stored in the column still named `counts_for_best` — it was the whole
    # answer until laps could be excluded by hand (#74) — and written by the
    # live pipeline alone, which re-judges it as laps arrive. Read it for "did
    # this lap cover the track" (track identification wants exactly that);
    # read `counts_for_best` below for "does this lap count".
    full_lap: Mapped[bool] = mapped_column("counts_for_best", default=True)
    # An explicit ruling or automatic race-start exclusion (#74): False
    # keeps an off-track or contact lap off every best, True keeps a lap the
    # heuristic misjudged on them. NULL = defer to full_lap. A separate column
    # rather than a write into full_lap, so the heuristic's re-flagging can
    # never clobber it and clearing it restores exactly what the heuristic said.
    best_override: Mapped[bool | None] = mapped_column(default=None)
    # Why a lap was excluded (including race-start)
    # (ExcludeReason) — so the Bests board can say why a time is missing.
    # Empty unless best_override is False.
    exclude_reason: Mapped[str] = mapped_column(default="")
    # Whether the lap counts toward best-lap aggregates: the override when one
    # is set, the heuristic otherwise. Computed in SQL, so every query and
    # aggregate that already filtered on counts_for_best honours overrides
    # without knowing they exist. Read-only — write full_lap or best_override.
    counts_for_best: Mapped[bool] = column_property(
        func.coalesce(best_override, full_lap, type_=Boolean)
    )
    # Track-limits verdict from per-tick surface data (packet C). Distinct
    # from counts_for_best: -1 / NULL = unknown (no surface data recorded).
    off_track_count: Mapped[int] = mapped_column(default=-1)
    # The same verdict judged against the SURVEYED road edges (#41): kept
    # apart from off_track_count because each can fire without the other.
    # -1 / NULL = unknown (circuit unsurveyed, or too little road under lap).
    off_survey_count: Mapped[int] = mapped_column(default=-1)
    clean_lap: Mapped[bool | None] = mapped_column(default=None)
    # Lap recovered from a stream that ended without the counter increment —
    # replay endings (#26). Stored so a time on the Bests board or in a lap
    # list can always be traced to its provenance; a server log line rotates
    # away, a row does not.
    salvaged: Mapped[bool] = mapped_column(default=False)
    # Race position when the lap completed (#60) — which lap places were
    # gained or lost on. -1 = no position reporting (time trial, practice).
    race_position: Mapped[int] = mapped_column(default=-1)
    events_json: Mapped[str] = mapped_column(Text, default="[]")
    gearing_json: Mapped[str] = mapped_column(Text, default="")
    # Packet C's wheelbase, metres. NULL = recorded without packet C.
    wheelbase_m: Mapped[float | None] = mapped_column(default=None)
    samples_json: Mapped[str] = mapped_column(Text)

    session: Mapped[SessionRow] = relationship(back_populates="laps")


def _url(db_path: Path | str) -> str | URL:
    if isinstance(db_path, Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        # Built, not formatted: in an f-string URL a "?" or "#" in a folder
        # name starts the query or fragment and the path is cut short there
        # ("100% Racing?/gt7.db" opened "100% Racing") (#178).
        return URL.create("sqlite+aiosqlite", database=str(db_path))
    return db_path  # full SQLAlchemy URL (e.g. postgresql+asyncpg://...)


def make_engine(db_path: Path | str) -> AsyncEngine:
    """The app's engine. Keeps the driver's own transaction handling — pysqlite
    issues BEGIN before a write and not before a read — which everything
    written against it relies on; only the busy timeout is added."""
    engine = create_async_engine(_url(db_path))
    wait_for_locks(engine.sync_engine)
    return engine


def make_migration_engine(url: Path | str | URL) -> AsyncEngine:
    """The engine a migration runs on: never pooled, and on it BEGIN really
    is BEGIN.

    pysqlite (and aiosqlite over it) does not issue BEGIN before DDL, so on
    the app's engine `engine.begin()` around a migration made nothing atomic:
    each statement committed as it ran, and a migration that failed half way
    left a file every later start failed on ("duplicate column name"). With
    the driver's handling turned off and BEGIN emitted when SQLAlchemy
    begins, the whole upgrade commits or rolls back as one (#182).
    """
    if isinstance(url, URL):
        url = url.render_as_string(hide_password=False)
    engine = create_async_engine(_url(url), poolclass=NullPool)
    transactional_ddl(engine.sync_engine)
    wait_for_locks(engine.sync_engine)
    return engine


def transactional_ddl(engine: Engine) -> None:
    """SQLAlchemy's recipe for real transactions on pysqlite and aiosqlite:
    the driver begins nothing itself, and BEGIN goes out when SQLAlchemy
    begins. For migrations only — see make_migration_engine."""
    if engine.dialect.name != "sqlite":
        return

    @event.listens_for(engine, "connect")
    def _no_driver_transactions(dbapi_connection: Any, _record: Any) -> None:
        dbapi_connection.isolation_level = None

    @event.listens_for(engine, "begin")
    def _begin(conn: Connection) -> None:
        conn.exec_driver_sql("BEGIN")


def wait_for_locks(engine: Engine) -> None:
    """busy_timeout on every connection the engine opens (#188).

    Every install keeps SQLite's rollback journal, where a commit waits for
    readers to finish and a reader for a commit. Without a long wait, a lap
    that ends while a backup copies fails with "database is locked" and is
    lost. `foreign_keys` stays off, deliberately: 0011 rebuilds `sessions`,
    which with it on cascades into deleting every lap (#182).
    """
    if engine.dialect.name != "sqlite":
        return

    @event.listens_for(engine, "connect")
    def _busy_timeout(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
        cursor.close()


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


# Columns added between the initial release and the adoption of Alembic, kept
# ONLY to carry a pre-Alembic SQLite file up to the baseline revision before it
# is stamped. This list is frozen: every schema change from here on is a
# revision under app/migrations/versions/. (#14)
_LEGACY_SQLITE_COLUMNS = (
    (
        "sessions",
        "track_name",
        "ALTER TABLE sessions ADD COLUMN track_name VARCHAR NOT NULL DEFAULT ''",
    ),
    (
        "sessions",
        "car_category",
        "ALTER TABLE sessions ADD COLUMN car_category VARCHAR NOT NULL DEFAULT ''",
    ),
    ("laps", "tod_ms", "ALTER TABLE laps ADD COLUMN tod_ms INTEGER NOT NULL DEFAULT -1"),
    (
        "laps",
        "counts_for_best",
        "ALTER TABLE laps ADD COLUMN counts_for_best BOOLEAN NOT NULL DEFAULT 1",
    ),
) + tuple(
    ("laps", column, f"ALTER TABLE laps ADD COLUMN {column} {ddl}")
    for column, ddl in (
        ("tcs_active_pct", "FLOAT NOT NULL DEFAULT 0"),
        ("asm_active_pct", "FLOAT NOT NULL DEFAULT 0"),
        ("max_water_temp", "FLOAT NOT NULL DEFAULT 0"),
        ("max_oil_temp", "FLOAT NOT NULL DEFAULT 0"),
        ("min_oil_pressure", "FLOAT NOT NULL DEFAULT -1"),
        ("events_json", "TEXT NOT NULL DEFAULT '[]'"),
        ("gearing_json", "TEXT NOT NULL DEFAULT ''"),
        ("off_track_count", "INTEGER NOT NULL DEFAULT -1"),
        ("clean_lap", "BOOLEAN"),
        ("car_category", "VARCHAR NOT NULL DEFAULT ''"),
    )
)


def alembic_config() -> Config:
    """Alembic Config built in Python — no ini file involved at runtime.

    `script_location` is resolved from the package so it works from any
    working directory (the app is started from the repo root as often as from
    backend/) and survives an editable install. No URL: the app always hands
    Alembic a live connection instead (see `_upgrade`), so a second engine
    against the same SQLite file never exists.
    """
    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "migrations"))
    return cfg


def _catch_up_legacy(conn: Connection) -> bool:
    """Bring a pre-Alembic SQLite database up to the baseline shape.

    Returns True when this database predates migrations and was (or already
    was) at baseline — the caller stamps it rather than running the baseline
    revision, which would try to CREATE tables that are sitting right there.

    A database with no `sessions` table at all is simply new: it gets the
    baseline revision like any fresh install.
    """
    inspector = inspect(conn)
    tables = set(inspector.get_table_names())
    if "alembic_version" in tables or "sessions" not in tables:
        return False
    log.info("pre-Alembic database found — bringing it to %s", BASELINE_REVISION)
    # Whole tables first, then columns. Baseline gained TABLES as well as
    # columns — `layouts` arrived with the overlay builder — and the hand-rolled
    # list below only ever knew how to ALTER, because the startup path used to
    # run create_all before it. Dropping that left a first-release database
    # stamped at baseline with no `layouts` table and no revision that would
    # ever create one, so every layout request 500s. create_all only touches
    # what is missing, which is exactly the old behaviour.
    Base.metadata.create_all(conn, checkfirst=True)
    for table, column, ddl in _LEGACY_SQLITE_COLUMNS:
        if table not in tables:
            continue  # just created from the models, so already at baseline
        if column not in {c["name"] for c in inspector.get_columns(table)}:
            conn.exec_driver_sql(ddl)
    return True


class DatabaseRefused(Exception):
    """A database this start will not open or upgrade. The message is the
    whole explanation: the app logs it as one line and exits."""


class DatabaseTooNew(DatabaseRefused):
    """The database is at a schema revision this build has never heard of:
    a newer version wrote it. Nothing in the file has been changed."""

    def __init__(self, stored: str, written_by: str | None) -> None:
        self.stored = stored
        self.written_by = written_by
        super().__init__(
            "the database was written by a newer version of GT7 Datalogger "
            f"({written_by or 'unknown'}, schema {stored}) than this one "
            f"({server_version()}), which cannot open it; update to "
            f"{written_by or 'that version'} or later, or restore a backup made "
            "by this version. The database has not been changed."
        )


class NoRoomForBackup(DatabaseRefused):
    """An upgrade is due and there is no room to copy the database first."""

    def __init__(self, stored: str, head: str, problem: str) -> None:
        self.stored = stored
        self.head = head
        super().__init__(
            f"not upgrading the database from schema {stored} to {head} without "
            f"a copy of it: {problem}. Free some space and start again; the "
            "database has not been changed."
        )


@cache
def _known_revisions(script_location: str) -> frozenset[str]:
    cfg = Config()
    cfg.set_main_option("script_location", script_location)
    return frozenset(r.revision for r in ScriptDirectory.from_config(cfg).walk_revisions())


@dataclass(frozen=True)
class _Plan:
    stored: str | None  # the revision the file is at; None: not stamped
    head: str
    legacy: bool  # tables from before Alembic, and no revision

    @property
    def pending(self) -> bool:
        """An upgrade is due on a database that already holds something."""
        return self.legacy or (self.stored is not None and self.stored != self.head)

    @property
    def label(self) -> str:
        return self.stored or "pre-alembic"


def _written_by(conn: Connection) -> str | None:
    if "settings" not in inspect(conn).get_table_names():
        return None
    value = conn.execute(
        select(SettingRow.value).where(SettingRow.key == SCHEMA_WRITTEN_BY_KEY)
    ).scalar_one_or_none()
    return str(value) if value is not None else None


def _plan(conn: Connection) -> _Plan:
    """What this start has to do to the database. Reads only; raises
    DatabaseTooNew on a revision this build does not know."""
    cfg = alembic_config()
    location = cfg.get_main_option("script_location") or ""
    head = ScriptDirectory.from_config(cfg).get_current_head()
    if head is None:
        raise RuntimeError(f"no migrations at {location}")
    tables = set(inspect(conn).get_table_names())
    if "alembic_version" not in tables:
        return _Plan(None, head, legacy="sessions" in tables)
    stored = MigrationContext.configure(conn).get_current_revision()
    if stored is not None and stored not in _known_revisions(location):
        raise DatabaseTooNew(stored, _written_by(conn))
    return _Plan(stored, head, legacy=False)


def _sqlite_file(engine: AsyncEngine) -> Path | None:
    url = engine.url
    if url.get_backend_name() != "sqlite" or url.database in (None, "", ":memory:"):
        return None
    return Path(url.database)


def _upgrade(conn: Connection) -> None:
    """Run migrations on an already-open (sync) connection, inside the
    transaction its caller began, and record which version ran them."""
    cfg = alembic_config()
    cfg.attributes["connection"] = conn
    if _catch_up_legacy(conn):
        command.stamp(cfg, BASELINE_REVISION)
    before = MigrationContext.configure(conn).get_current_revision()
    command.upgrade(cfg, "head")
    after = MigrationContext.configure(conn).get_current_revision()
    if before != after or _written_by(conn) is None:
        # The version that migrated, or — the first start to keep this row —
        # one that can open the schema the file is at: either way what a
        # build too old for it should tell the user to update to.
        conn.execute(delete(SettingRow).where(SettingRow.key == SCHEMA_WRITTEN_BY_KEY))
        conn.execute(
            insert(SettingRow).values(key=SCHEMA_WRITTEN_BY_KEY, value=server_version())
        )
    if before != after:
        log.info("database schema %s -> %s", before or "empty", after)


async def init_db(engine: AsyncEngine, *, backups: Path | None = None) -> None:
    """Create or upgrade the schema. Safe to call on every startup.

    A database at a revision this build does not know raises DatabaseTooNew
    and is left exactly as it was. When an upgrade is due on an existing
    SQLite file and `backups` is given, the file is copied there first
    (upgrade_backup); with no room for the copy, NoRoomForBackup is raised
    and nothing is upgraded. The upgrade itself runs on a migration engine
    of its own, in one transaction: one that fails leaves the file at the
    revision it was at.
    """
    migrator = make_migration_engine(engine.url)
    try:
        async with migrator.connect() as conn:
            plan = await conn.run_sync(_plan)
            await conn.rollback()
            path = _sqlite_file(engine)
            copy: Path | None = None
            if plan.pending and backups is not None and path is not None:
                problem = await asyncio.to_thread(upgrade_backup.room_problem, path, backups)
                if problem:
                    log.warning("%s", problem)
                    raise NoRoomForBackup(plan.label, plan.head, problem)
                copy = await asyncio.to_thread(
                    upgrade_backup.back_up, path, backups, plan.label, plan.head
                )
                log.info("copied the database to %s before upgrading it", copy)
            try:
                async with conn.begin():
                    await conn.run_sync(_upgrade)
            except Exception:
                if plan.pending:
                    log.error(
                        "upgrading the database from schema %s to %s failed and was "
                        "rolled back: it is still at %s%s",
                        plan.label,
                        plan.head,
                        plan.label,
                        f", and a copy from before is in {copy}" if copy else "",
                    )
                raise
    finally:
        await migrator.dispose()
