"""Manual lap exclusion from best-lap aggregates (#74).

Until now a lap left the bests only when its distance span said it was
partial. An off-track lap, a lap with contact, or an out-lap the heuristic
accepted all still owned a best, and nothing could say otherwise. These
columns hold the user's verdict beside the heuristic's:

  laps.best_override   NULL = defer to the span heuristic (every existing
                       row, which is what they were before this existed);
                       False = never counts; True = counts even though the
                       heuristic called it partial.
  laps.exclude_reason  off-track / contact / restart / dirty / pit-out, so
                       the Bests board can say why a time is missing. Empty
                       unless best_override is False.

The heuristic keeps its own column (`counts_for_best`, mapped as `full_lap`),
and the pipeline goes on re-flagging it as laps arrive. The override lives
apart so that re-flagging cannot overwrite it, and so clearing it hands the
lap straight back to whatever the heuristic last said.

Revision ID: 0010_lap_best_override
Revises: 0009_session_car_figures
Create Date: 2026-09-16
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_lap_best_override"
down_revision: str | None = "0009_session_car_figures"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("laps", sa.Column("best_override", sa.Boolean(), nullable=True))
    op.add_column(
        "laps",
        sa.Column("exclude_reason", sa.String(), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("laps", "exclude_reason")
    op.drop_column("laps", "best_override")
