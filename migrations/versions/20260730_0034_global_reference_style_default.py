"""add a shared default reference style for future projects

Revision ID: 20260730_0034
Revises: 20260730_0033
Create Date: 2026-07-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260730_0034"
down_revision: str | None = "20260730_0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "global_reference_style_defaults",
        sa.Column("key", sa.String(length=30), nullable=False),
        sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("author_reason", sa.Text(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["reference_style_profiles.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("key"),
        sa.UniqueConstraint("profile_id"),
    )


def downgrade() -> None:
    op.drop_table("global_reference_style_defaults")
