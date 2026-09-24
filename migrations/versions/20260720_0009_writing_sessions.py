"""Add batch-oriented continuous writing sessions."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260720_0009"
down_revision = "20260719_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "writing_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("base_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("direction", sa.Text(), nullable=False),
        sa.Column("must_include", postgresql.JSONB(), nullable=False),
        sa.Column("avoid", postgresql.JSONB(), nullable=False),
        sa.Column("target_min_chars", sa.Integer(), nullable=False),
        sa.Column("target_max_chars", sa.Integer(), nullable=False),
        sa.Column("chapter_target_chars", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("stage", sa.String(30), nullable=False),
        sa.Column("raw_body", sa.Text(), nullable=True),
        sa.Column("raw_sha256", sa.String(64), nullable=True),
        sa.Column("review_package", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("sealed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("merged_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["story_projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["base_version_id"], ["state_versions.id"]),
    )
    op.create_index(
        "ix_writing_sessions_project_created",
        "writing_sessions",
        ["project_id", "created_at"],
    )
    op.create_table(
        "writing_chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["session_id"], ["writing_sessions.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("session_id", "ordinal"),
    )
    op.create_table(
        "writing_artifacts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("stage", sa.String(30), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("content", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["session_id"], ["writing_sessions.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("session_id", "stage", "kind"),
    )


def downgrade() -> None:
    op.drop_table("writing_artifacts")
    op.drop_table("writing_chunks")
    op.drop_index("ix_writing_sessions_project_created", table_name="writing_sessions")
    op.drop_table("writing_sessions")
