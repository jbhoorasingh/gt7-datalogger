"""Session tags, next to the (until now unused) note column (#25).

`sessions.note` has been in the schema since the first release and nothing
ever wrote it. Notes are prose ("testing new diff, still loose on exit");
tags are the repeatable labels sessions get filtered by ("wet", "race sim"),
so they get their own column instead of a convention buried inside the text.
Stored comma-separated — tags are forbidden to contain commas at the API
boundary, and a query language over a JSON column is not something a lap
logger needs.

Revision ID: 0006_session_tags
Revises: 0005_track_direction
Create Date: 2026-08-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_session_tags"
down_revision: str | None = "0005_track_direction"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "sessions", sa.Column("tags", sa.String(), nullable=False, server_default="")
    )


def downgrade() -> None:
    op.drop_column("sessions", "tags")
