"""Add author blind reviews and local quality regression cases."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260722_0017"
down_revision = "20260722_0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "quality_lab_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("result", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["project_id"], ["story_projects.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_quality_lab_project_kind_created",
        "quality_lab_records",
        ["project_id", "kind", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_quality_lab_project_kind_created", table_name="quality_lab_records")
    op.drop_table("quality_lab_records")
