"""Database engine + ORM models. SQLite by default; any SQLAlchemy async URL works.

Schema changes go through Alembic (`app/migrations/`), not through this file:
`init_db` upgrades to head at startup. See `docs/internals/architecture.md`.
"""

from __future__ import annotations

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Connection, ForeignKey, Index, Text, inspect
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

log = logging.getLogger(__name__)

# The revision every pre-Alembic database is stamped at once it has been
# brought up to that shape (see _catch_up_legacy).
BASELINE_REVISION = "0001_baseline"


class Base(DeclarativeBase):
    pass


class SessionRow(Base):
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[str]
    car_id: Mapped[int]
    car_name: Mapped[str]
    # Broadcast in packet C ("Gr.3", "Gr.4", "N300"...). Stored on the lap too,
    # denormalised like car_id, so category filtering never needs the join.
    car_category: Mapped[str] = mapped_column(default="")
    note: Mapped[str] = mapped_column(default="")
    track_name: Mapped[str] = mapped_column(default="")

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
    # False for partial laps (pit out-laps): excluded from best-lap aggregates
    counts_for_best: Mapped[bool] = mapped_column(default=True)
    # Track-limits verdict from per-tick surface data (packet C). Distinct
    # from counts_for_best: -1 / NULL = unknown (no surface data recorded).
    off_track_count: Mapped[int] = mapped_column(default=-1)
    # The same verdict judged against the SURVEYED road edges (#41): kept
    # apart from off_track_count because each can fire without the other.
    # -1 / NULL = unknown (circuit unsurveyed, or too little road under lap).
    off_survey_count: Mapped[int] = mapped_column(default=-1)
    clean_lap: Mapped[bool | None] = mapped_column(default=None)
    events_json: Mapped[str] = mapped_column(Text, default="[]")
    gearing_json: Mapped[str] = mapped_column(Text, default="")
    samples_json: Mapped[str] = mapped_column(Text)

    session: Mapped[SessionRow] = relationship(back_populates="laps")


def make_engine(db_path: Path | str) -> AsyncEngine:
    if isinstance(db_path, Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        url = f"sqlite+aiosqlite:///{db_path}"
    else:
        url = db_path  # full SQLAlchemy URL (e.g. postgresql+asyncpg://...)
    return create_async_engine(url)


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


def _upgrade(conn: Connection) -> None:
    """Run migrations on an already-open (sync) connection."""
    cfg = alembic_config()
    cfg.attributes["connection"] = conn
    if _catch_up_legacy(conn):
        command.stamp(cfg, BASELINE_REVISION)
    before = MigrationContext.configure(conn).get_current_revision()
    command.upgrade(cfg, "head")
    after = MigrationContext.configure(conn).get_current_revision()
    if before != after:
        log.info("database schema %s -> %s", before or "empty", after)


async def init_db(engine: AsyncEngine) -> None:
    """Create or upgrade the schema. Safe to call on every startup."""
    async with engine.begin() as conn:
        await conn.run_sync(_upgrade)
