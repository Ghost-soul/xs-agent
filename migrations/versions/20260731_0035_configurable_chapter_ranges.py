"""add configurable chapter segmentation ranges

Revision ID: 20260731_0035
Revises: 20260730_0034
Create Date: 2026-07-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260731_0035"
down_revision: str | None = "20260730_0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table in ("writing_sessions", "project_creative_policies"):
        op.add_column(
            table,
            sa.Column("chapter_min_chars", sa.Integer(), server_default="4000", nullable=False),
        )
        op.add_column(
            table,
            sa.Column("chapter_max_chars", sa.Integer(), server_default="6000", nullable=False),
        )


def downgrade() -> None:
    for table in ("project_creative_policies", "writing_sessions"):
        op.drop_column(table, "chapter_max_chars")
        op.drop_column(table, "chapter_min_chars")
