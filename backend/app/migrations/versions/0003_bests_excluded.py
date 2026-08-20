"""Replay bookkeeping: bests-excluded sessions, salvaged laps (#26).

GT7 broadcasts a replay exactly like driving — there is no replay flag in the
telemetry — so a recorded TT-leader replay would silently become "your"
personal best. Telling those sessions apart is a human call: the user flags
them, and every PB query filters on the flag. Default 0 keeps every existing
session eligible, which is what they were before the flag existed.

Laps carry `salvaged` for the same reason from the other side: a lap
recovered from a stream that ended without the counter increment (a replay
ending) must stay traceable to that provenance in every lap list and on the
Bests board, not only in a server log line that rotates away. Existing rows
predate salvage, so 0 is the truth for all of them.

Revision ID: 0003_bests_excluded
Revises: 0002_off_survey_count
Create Date: 2026-08-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_bests_excluded"
down_revision: str | None = "0002_off_survey_count"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "sessions",
        sa.Column("bests_excluded", sa.Boolean(), nullable=False, server_default="0"),
    )
    op.add_column(
        "laps",
        sa.Column("salvaged", sa.Boolean(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("laps", "salvaged")
    op.drop_column("sessions", "bests_excluded")
