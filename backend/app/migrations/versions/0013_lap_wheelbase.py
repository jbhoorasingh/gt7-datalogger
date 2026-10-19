"""The car's wheelbase on every lap, from packet C.

With the new `wheel_angle` sample column (the front road wheels' mean
steering angle) it gives the kinematic yaw rate a car would have with no
slip at all — speed * angle / wheelbase — so understeer and oversteer can be
measured against it directly instead of against a learned baseline.

Constant per car, so one figure per lap rather than a sample column. NULL
for laps recorded without packet C, and for every lap stored before this.

Revision ID: 0013_lap_wheelbase
Revises: 0012_session_revision
Create Date: 2026-10-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_lap_wheelbase"
down_revision: str | None = "0012_session_revision"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("laps", sa.Column("wheelbase_m", sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("laps") as batch:
        batch.drop_column("wheelbase_m")
