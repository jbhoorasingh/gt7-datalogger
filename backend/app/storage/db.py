"""Database engine + ORM models. SQLite by default; any SQLAlchemy async URL works."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import ForeignKey, Index, Text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


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


# Columns added after the initial release; applied to existing SQLite files.
_SQLITE_MIGRATIONS = (
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


async def init_db(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if engine.dialect.name == "sqlite":
            for table, column, ddl in _SQLITE_MIGRATIONS:
                info = await conn.exec_driver_sql(f"PRAGMA table_info({table})")
                if column not in {row[1] for row in info}:
                    await conn.exec_driver_sql(ddl)
