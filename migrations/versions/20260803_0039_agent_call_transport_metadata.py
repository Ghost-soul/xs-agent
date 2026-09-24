"""persist exact provider transport metadata

Revision ID: 20260803_0039
Revises: 20260802_0038
Create Date: 2026-08-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260803_0039"
down_revision: str | None = "20260802_0038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "writing_chunk_calls",
        sa.Column("raw_transport_format", sa.String(80), nullable=True),
    )
    op.add_column(
        "writing_chunk_calls",
        sa.Column("raw_entity_body_sha256", sa.String(64), nullable=True),
    )
    op.add_column(
        "writing_chunk_calls",
        sa.Column(
            "response_headers_redacted",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("writing_chunk_calls", "response_headers_redacted")
    op.drop_column("writing_chunk_calls", "raw_entity_body_sha256")
    op.drop_column("writing_chunk_calls", "raw_transport_format")
