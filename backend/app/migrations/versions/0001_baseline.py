"""Baseline schema (#14).

The schema as it stood when migrations were adopted — everything the
hand-rolled `ALTER TABLE ADD COLUMN` list in `init_db` used to build up one
release at a time. A database created before this revision existed is
brought to this exact shape by `app.storage.db._catch_up_legacy` and then
stamped, so the two paths converge here and every change after this one is
an ordinary revision.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-08-11
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_baseline"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sessions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.String(), nullable=False),
        sa.Column("car_id", sa.Integer(), nullable=False),
        sa.Column("car_name", sa.String(), nullable=False),
        sa.Column("car_category", sa.String(), nullable=False),
        sa.Column("note", sa.String(), nullable=False),
        sa.Column("track_name", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "tracks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("length_m", sa.Float(), nullable=False),
        sa.Column("min_x", sa.Float(), nullable=False),
        sa.Column("max_x", sa.Float(), nullable=False),
        sa.Column("min_z", sa.Float(), nullable=False),
        sa.Column("max_z", sa.Float(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "settings",
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("value", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_table(
        "layouts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("config_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "laps",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("time_ms", sa.Integer(), nullable=False),
        sa.Column("finished_at", sa.String(), nullable=False),
        sa.Column("car_id", sa.Integer(), nullable=False),
        sa.Column("car_category", sa.String(), nullable=False),
        sa.Column("fuel_start", sa.Float(), nullable=False),
        sa.Column("fuel_end", sa.Float(), nullable=False),
        sa.Column("fuel_consumed", sa.Float(), nullable=False),
        sa.Column("full_throttle_pct", sa.Float(), nullable=False),
        sa.Column("full_brake_pct", sa.Float(), nullable=False),
        sa.Column("coasting_pct", sa.Float(), nullable=False),
        sa.Column("tire_spin_pct", sa.Float(), nullable=False),
        sa.Column("max_speed", sa.Float(), nullable=False),
        sa.Column("min_body_height", sa.Float(), nullable=False),
        sa.Column("total_ticks", sa.Integer(), nullable=False),
        sa.Column("tod_ms", sa.Integer(), nullable=False),
        sa.Column("tcs_active_pct", sa.Float(), nullable=False),
        sa.Column("asm_active_pct", sa.Float(), nullable=False),
        sa.Column("max_water_temp", sa.Float(), nullable=False),
        sa.Column("max_oil_temp", sa.Float(), nullable=False),
        sa.Column("min_oil_pressure", sa.Float(), nullable=False),
        sa.Column("counts_for_best", sa.Boolean(), nullable=False),
        sa.Column("off_track_count", sa.Integer(), nullable=False),
        sa.Column("clean_lap", sa.Boolean(), nullable=True),
        sa.Column("events_json", sa.Text(), nullable=False),
        sa.Column("gearing_json", sa.Text(), nullable=False),
        sa.Column("samples_json", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_laps_session", "laps", ["session_id"])


def downgrade() -> None:
    op.drop_index("ix_laps_session", table_name="laps")
    op.drop_table("laps")
    op.drop_table("layouts")
    op.drop_table("settings")
    op.drop_table("tracks")
    op.drop_table("sessions")
