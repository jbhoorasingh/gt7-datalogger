"""The rest of the car record on the session row (#57).

0008 put the four identifying fields there — manufacturer, year, drivetrain,
aspiration. This adds the remainder of what GT7 publishes, so a session row is
the complete answer about the car that ran it rather than four fields and a
lookup for the rest:

  sessions.car_full_name           "Nissan Skyline GTS-R (R31) '87"; car_name
                                   stays the short in-game form
  sessions.car_displacement_cc     1998; 0 where unpublished (every EV). The
                                   rotaries carry swept volume — 1308 for a
                                   13B, which is 654x2 at the source
  sessions.car_power_bhp           207; 0 if unpublished
  sessions.car_torque_kgfm         25.0
  sessions.car_weight_kg           1340
  sessions.car_length_mm           4660
  sessions.car_width_mm            1690
  sessions.car_height_mm           1365
  sessions.car_performance_points  440.85

Laps deliberately get none of this. Every lap query already inner-joins
sessions — for track_name and bests_excluded, which it cannot avoid — so the
car columns are reachable there at no cost, and a second copy on the table
that grows with every lap driven would buy nothing but a wider row and a
longer backfill. car_category remains the exception that stays on the lap: it
is per-lap telemetry from packet C, and a session's laps can legitimately
disagree with the session row.

Filled by the same startup backfill as 0008, which re-runs whenever the
inventory changes.

Revision ID: 0009_session_car_figures
Revises: 0008_session_car_details
Create Date: 2026-08-30
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0009_session_car_figures"
down_revision: str | None = "0008_session_car_details"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (column, type, server default) — the shape every one of these shares.
COLUMNS: tuple[tuple[str, sa.types.TypeEngine[Any], str], ...] = (
    ("car_full_name", sa.String(), ""),
    ("car_displacement_cc", sa.Integer(), "0"),
    ("car_power_bhp", sa.Integer(), "0"),
    ("car_torque_kgfm", sa.Float(), "0"),
    ("car_weight_kg", sa.Integer(), "0"),
    ("car_length_mm", sa.Integer(), "0"),
    ("car_width_mm", sa.Integer(), "0"),
    ("car_height_mm", sa.Integer(), "0"),
    ("car_performance_points", sa.Float(), "0"),
)


def upgrade() -> None:
    for name, type_, default in COLUMNS:
        op.add_column(
            "sessions", sa.Column(name, type_, nullable=False, server_default=default)
        )


def downgrade() -> None:
    for name, _type, _default in reversed(COLUMNS):
        op.drop_column("sessions", name)
