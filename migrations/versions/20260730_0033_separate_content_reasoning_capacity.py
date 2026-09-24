"""separate visible content capacity from provider total output

Revision ID: 20260730_0033
Revises: 20260728_0032
Create Date: 2026-07-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260730_0033"
down_revision: str | None = "20260728_0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "writing_chunk_calls",
        sa.Column("max_content_tokens", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("writing_chunk_calls", "max_content_tokens")
