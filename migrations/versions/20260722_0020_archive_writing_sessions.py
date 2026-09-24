"""Archive writing sessions without deleting audit history.

Revision ID: 20260722_0020
Revises: 20260722_0019
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260722_0020"
down_revision: str | None = "20260722_0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "writing_sessions",
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_writing_sessions_project_archived_created",
        "writing_sessions",
        ["project_id", "archived_at", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_writing_sessions_project_archived_created",
        table_name="writing_sessions",
    )
    op.drop_column("writing_sessions", "archived_at")
