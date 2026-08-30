"""The car's own details on the session row (#57).

Until now a session knew its car's id, its name and — from packet C only — its
category. Everything else about the car was unanswerable, because the bundled
lookup was 575 rows of id,name and had nothing else to give. The inventory that
replaces it carries manufacturer, model year, drivetrain and aspiration, and
these four columns put them where car_name already is:

  sessions.car_manufacturer   "Nissan"
  sessions.car_year           1987; 0 where the car carries no model year,
                              which is normal for race cars and concepts
  sessions.car_drivetrain     "FR", "FF", "MR", "RR", "4WD"; "" if unpublished
  sessions.car_aspiration     "NA", "TC", "SC", "TC+SC", "EV"; "" if unpublished

Denormalised for the reason car_name and car_category already are: filtering
and display should not need a lookup, and the row must keep describing the car
even after GT7's list stops publishing it (ten such cars are carried forward in
the inventory today for exactly this reason).

Existing rows are left empty here and filled by the startup backfill in
app/main.py, which re-runs whenever the inventory changes — one code path that
keeps old sessions current instead of a one-shot UPDATE frozen at this
revision, which would go stale the first time the background refresh added a
car.

Revision ID: 0008_session_car_details
Revises: 0007_race_result
Create Date: 2026-08-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_session_car_details"
down_revision: str | None = "0007_race_result"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "sessions",
        sa.Column("car_manufacturer", sa.String(), nullable=False, server_default=""),
    )
    op.add_column(
        "sessions",
        sa.Column("car_year", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "sessions",
        sa.Column("car_drivetrain", sa.String(), nullable=False, server_default=""),
    )
    op.add_column(
        "sessions",
        sa.Column("car_aspiration", sa.String(), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("sessions", "car_aspiration")
    op.drop_column("sessions", "car_drivetrain")
    op.drop_column("sessions", "car_year")
    op.drop_column("sessions", "car_manufacturer")
