"""Track-limits verdict against the surveyed road edges (#41).

`off_survey_count` counts a lap's excursions beyond the compiled survey
borders — the paved-runoff case the per-wheel surface flags can never see.
Kept beside `off_track_count`, not folded into it. Existing rows predate the
judge, so they get -1: unknown, same convention as off_track_count.

Revision ID: 0002_off_survey_count
Revises: 0001_baseline
Create Date: 2026-08-15
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_off_survey_count"
down_revision: str | None = "0001_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "laps",
        sa.Column("off_survey_count", sa.Integer(), nullable=False, server_default="-1"),
    )


def downgrade() -> None:
    op.drop_column("laps", "off_survey_count")
