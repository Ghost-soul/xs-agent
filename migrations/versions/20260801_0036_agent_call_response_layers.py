"""add provider response layers and stable failure codes

Revision ID: 20260801_0036
Revises: 20260731_0035
Create Date: 2026-08-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260801_0036"
down_revision: str | None = "20260731_0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("writing_chunk_calls", sa.Column("failure_code", sa.String(80)))
    op.add_column("writing_chunk_calls", sa.Column("raw_transport_sha256", sa.String(64)))
    op.add_column(
        "writing_chunk_calls", sa.Column("extracted_content_sha256", sa.String(64))
    )
    op.add_column(
        "writing_chunk_calls", sa.Column("validation_report_sha256", sa.String(64))
    )


def downgrade() -> None:
    op.drop_column("writing_chunk_calls", "validation_report_sha256")
    op.drop_column("writing_chunk_calls", "extracted_content_sha256")
    op.drop_column("writing_chunk_calls", "raw_transport_sha256")
    op.drop_column("writing_chunk_calls", "failure_code")
