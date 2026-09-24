"""Add content-free deletion receipts and resumable cleanup manifests."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260907_0046"
down_revision: str | None = "20260831_0045"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "project_deletions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column("request_key_sha256", sa.String(64), nullable=False, unique=True),
        sa.Column("request_sha256", sa.String(64), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("counts", postgresql.JSONB(), nullable=False),
        sa.Column("cost_summary", postgresql.JSONB(), nullable=False),
        sa.Column("retired_commands", postgresql.JSONB(), nullable=False),
        sa.Column("cleanup_manifest", postgresql.JSONB(), nullable=False),
        sa.Column("cleanup_error", sa.String(80), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('cleanup_pending', 'completed')", name="ck_project_deletion_status"
        ),
    )


def downgrade() -> None:
    op.drop_table("project_deletions")
