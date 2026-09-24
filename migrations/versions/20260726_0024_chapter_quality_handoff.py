"""Persist revision-bound chapter quality handoff data.

Revision ID: 20260726_0024
Revises: 20260726_0023
Create Date: 2026-07-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "20260726_0024"
down_revision: str | None = "20260726_0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "chapter_summaries",
        sa.Column("quality_handoff", JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("chapter_summaries", "quality_handoff")
