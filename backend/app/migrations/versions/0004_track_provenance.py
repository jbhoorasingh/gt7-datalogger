"""Seeded track signatures, and the provenance that keeps them apart (#58).

Track identification had a bootstrapping hole: a signature only existed once a
human named a circuit, so a new install named nothing, and everything hanging
off the circuit name stayed empty. The fix seeds the table from geometry
computed offline — but a seeded row and a row the user typed are not the same
kind of fact, and two things break if the table cannot tell them apart.

`provenance` is the line between them. It is what lets a re-sync replace every
row the seed owns without touching a single row the user owns, and it is what
lets identification prefer a name a person chose over one we inferred.

`official_id` ties a seeded row to the GT7 configuration it came from, so a
row that turns out to be wrong can be identified against the source rather
than matched by name.

Existing rows are all user-created by definition — the seed did not exist when
they were written — so 'user' is the truth for every one of them.

Revision ID: 0004_track_provenance
Revises: 0003_bests_excluded
Create Date: 2026-08-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_track_provenance"
down_revision: str | None = "0003_bests_excluded"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tracks",
        sa.Column("provenance", sa.String(), nullable=False, server_default="user"),
    )
    op.add_column(
        "tracks",
        sa.Column("official_id", sa.String(), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("tracks", "official_id")
    op.drop_column("tracks", "provenance")
