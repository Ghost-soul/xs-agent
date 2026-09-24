"""Add recoverable project archiving.

Revision ID: 20260726_0027
Revises: 20260726_0026
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260726_0027"
down_revision: str | None = "20260726_0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("story_projects", sa.Column("archived_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("story_projects", "archived_at")
