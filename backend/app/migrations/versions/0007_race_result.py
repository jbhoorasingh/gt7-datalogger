"""Persist race position and the final race result (#60).

GT7 broadcasts race_position/total_positions on every packet and the live
pipeline already watches them — then throws the answer away when the stream
stops. These columns keep it:

  sessions.final_position        finishing position, written only at the
                                 checkered-flag edge (current_lap passes
                                 total_laps). -1 = no result: a time trial,
                                 or a stream that ended mid-race. GT7 sends
                                 -1 where there is no position reporting, so
                                 "no race" and "finished last" stay distinct
                                 in the schema — last place is >= 2.
  sessions.final_total_positions field size at the finish; -1 with no result.
  sessions.race_laps             the race distance (packet total_laps);
                                 0 = not a lapped race.
  sessions.race_time_ms          sum of the stored lap times — NULL unless
                                 every lap 1..race_laps is present, because
                                 a sum over a session with a missing lap is
                                 a confidently wrong number, and no packet
                                 field carries the real total.
  laps.race_position             position when the lap completed;
                                 -1 = no position reporting.

Revision ID: 0007_race_result
Revises: 0006_session_tags
Create Date: 2026-08-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_race_result"
down_revision: str | None = "0006_session_tags"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for column in ("final_position", "final_total_positions"):
        op.add_column(
            "sessions",
            sa.Column(column, sa.Integer(), nullable=False, server_default="-1"),
        )
    op.add_column(
        "sessions", sa.Column("race_laps", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column("sessions", sa.Column("race_time_ms", sa.Integer(), nullable=True))
    op.add_column(
        "laps",
        sa.Column("race_position", sa.Integer(), nullable=False, server_default="-1"),
    )


def downgrade() -> None:
    op.drop_column("laps", "race_position")
    for column in ("race_time_ms", "race_laps", "final_total_positions", "final_position"):
        op.drop_column("sessions", column)
