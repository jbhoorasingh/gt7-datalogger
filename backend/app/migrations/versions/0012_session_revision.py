"""A change counter on sessions, for clients that keep a copy (#142).

The phone app pulls sessions onto the device and, after that, only the ones
that changed. Nothing said which those were: a session changes when a lap
is saved, ruled in or out, re-judged against the survey, or deleted, when
its note, tags, track or race result are written, and when a car-inventory
refresh re-derives its car columns — a dozen write paths, none of which
touched a timestamp.

`sessions.revision` is bumped by every one of them (Repository._touch) to
one more than the largest revision in the table, so "everything above N"
is exactly what changed since a client last saw revision N. Existing rows
start at their id, distinct and above zero, so a client asking from zero
gets every session.

Revision ID: 0012_session_revision
Revises: 0011_session_ids_never_reused
Create Date: 2026-10-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_session_revision"
down_revision: str | None = "0011_session_ids_never_reused"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "sessions",
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
    )
    op.execute("UPDATE sessions SET revision = id")


def downgrade() -> None:
    with op.batch_alter_table("sessions") as batch:
        batch.drop_column("revision")
