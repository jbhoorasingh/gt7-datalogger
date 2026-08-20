"""The seeded racing line, so a reverse layout stops wearing its twin's name (#58).

A bounding box cannot tell a layout from its reverse: same tarmac, same
length, same box. Seeding therefore named reverse laps after the forward
configuration, and because bests are keyed on the circuit name, forward and
reverse times pooled under one name and competed for the same personal best.
On the author's own recordings that was 10 laps of Deep Forest Reverse filed
as Deep Forest Raceway, against 6 genuine forward laps at the same circuit.

What separates them is the one thing a box discards: the ORDER the road is
driven in. `path_json` is the seeded racing line thinned to a point every
20 m or so, kept in driving order, and a lap that walks it backwards is a lap
of the reverse configuration — whose own id and name GT7 publishes, and which
`reverse_id`/`reverse_name` carry so naming one costs no catalog read.

All three are empty for user rows, which have no path and describe whichever
direction their author drove.

Revision ID: 0005_track_direction
Revises: 0004_track_provenance
Create Date: 2026-08-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_track_direction"
down_revision: str | None = "0004_track_provenance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for column in ("path_json", "reverse_id", "reverse_name"):
        op.add_column(
            "tracks", sa.Column(column, sa.Text(), nullable=False, server_default="")
        )


def downgrade() -> None:
    for column in ("reverse_name", "reverse_id", "path_json"):
        op.drop_column("tracks", column)
