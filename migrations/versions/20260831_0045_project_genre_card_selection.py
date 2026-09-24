"""add explicit project genre-card selection and secondary card pool

Revision ID: 20260831_0045
Revises: 20260808_0044
Create Date: 2026-08-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260831_0045"
down_revision: str | None = "20260808_0044"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "project_style_profiles",
        sa.Column(
            "selection_mode",
            sa.String(20),
            nullable=False,
            server_default="automatic",
        ),
    )
    op.add_column(
        "project_style_profiles",
        sa.Column("secondary_genre_ids", postgresql.JSONB(), nullable=True),
    )
    op.execute(
        "UPDATE project_style_profiles "
        "SET secondary_genre_ids = '[]'::jsonb "
        "WHERE secondary_genre_ids IS NULL"
    )
    op.alter_column("project_style_profiles", "secondary_genre_ids", nullable=False)


def downgrade() -> None:
    op.drop_column("project_style_profiles", "secondary_genre_ids")
    op.drop_column("project_style_profiles", "selection_mode")
