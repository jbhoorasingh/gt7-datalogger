"""Session ids are never handed out twice.

`sessions.id` was a plain INTEGER PRIMARY KEY, and SQLite gives a new row
one more than the largest id left in the table. So deleting every session
("Delete all recorded data"), or only the newest one, made the next session
reuse an id that already meant another drive. Anything that remembers
sessions by id then mistook the new one for the old: the sync client sent a
race's laps into the deleted qualifier's server session, replacing its laps,
and the Analysis view reopened "session 1" that was no longer that session.

AUTOINCREMENT makes SQLite keep the high-water mark in `sqlite_sequence`,
which survives deletes, so an id is used once for the life of the database.
SQLite can only declare it at CREATE TABLE, so the table is rebuilt.
`sessions` is one small row per drive, so the rebuild costs nothing even on
a Pi with years of history. The copy seeds the sequence with the largest id
present, so numbering carries on from where it was.

`laps` keeps its plain key: rebuilding it would copy every lap's samples,
hundreds of megabytes on a long history, and nothing keeps lap ids across a
delete the way the sync client keeps session ids.

Revision ID: 0011_session_ids_never_reused
Revises: 0010_lap_best_override
Create Date: 2026-09-28
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0011_session_ids_never_reused"
down_revision: str | None = "0010_lap_best_override"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table(
        "sessions", recreate="always", table_kwargs={"sqlite_autoincrement": True}
    ):
        pass


def downgrade() -> None:
    with op.batch_alter_table(
        "sessions", recreate="always", table_kwargs={"sqlite_autoincrement": False}
    ):
        pass
