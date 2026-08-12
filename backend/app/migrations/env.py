"""Alembic environment.

Two entry paths, and both matter:

* **In-process** — `app.storage.db.init_db` hands us a live connection through
  `config.attributes["connection"]`. The app's engine is async, and Alembic is
  synchronous, so the connection arrives already unwrapped by
  `AsyncConnection.run_sync`. Nothing here may open its own engine in that
  case, or SQLite would be asked for a second writer on the same file.
* **CLI** — `alembic upgrade head` from `backend/`, which reads the URL from
  `alembic.ini` (or `-x db=<path>`). Used to generate revisions and to inspect
  a database by hand; the app never depends on it.

`render_as_batch` is on because SQLite cannot ALTER a column in place: batch
mode rewrites the table instead, which is the whole reason the hand-rolled
`ADD COLUMN` list this replaces could only ever add.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.storage.db import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Emit SQL to stdout without a database (`alembic upgrade --sql`)."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()
        return

    engine = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with engine.connect() as conn:
        context.configure(
            connection=conn, target_metadata=target_metadata, render_as_batch=True
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
