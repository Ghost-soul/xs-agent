"""add setup drafts, local tasks and reading bookmarks

Revision ID: 20260803_0041
Revises: 20260803_0040
Create Date: 2026-08-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260803_0041"
down_revision: str | None = "20260803_0040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "project_setup_drafts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column(
            "schema_version", sa.String(40), nullable=False, server_default="project-setup-draft-v1"
        ),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="draft"),
        sa.Column(
            "finalized_project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("story_projects.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_table(
        "reading_bookmarks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "schema_version", sa.String(40), nullable=False, server_default="reading-bookmark-v1"
        ),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("story_projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "chapter_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chapters.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "revision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chapter_revisions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("offset", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("anchor_sha256", sa.String(64), nullable=False),
        sa.Column("note", sa.String(500), nullable=False, server_default=""),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("project_id", "chapter_id", "anchor_sha256"),
    )
    op.create_table(
        "local_tasks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "schema_version", sa.String(40), nullable=False, server_default="local-task-v1"
        ),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("story_projects.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="queued"),
        sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("input_sha256", sa.String(64), nullable=False),
        sa.Column("output_sha256", sa.String(64), nullable=True),
        sa.Column("error_code", sa.String(80), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("lease_owner", sa.String(120), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "progress >= 0 AND progress <= 100", name="ck_local_tasks_progress"
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed')",
            name="ck_local_tasks_status",
        ),
    )
    op.create_index(
        "ix_local_tasks_claim",
        "local_tasks",
        ["status", "lease_expires_at", "created_at"],
    )
    op.create_table(
        "import_previews",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "schema_version", sa.String(40), nullable=False, server_default="import-preview-v1"
        ),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("original_filename", sa.String(300), nullable=False, server_default=""),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("upload_sha256", sa.String(64), nullable=False),
        sa.Column("selected_encoding", sa.String(30), nullable=False),
        sa.Column("parser_version", sa.String(40), nullable=False),
        sa.Column("chapter_manifest_sha256", sa.String(64), nullable=False),
        sa.Column("preview", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("storage_key", sa.String(80), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="previewed"),
        sa.Column(
            "finalized_project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("story_projects.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_table("import_previews")
    op.drop_index("ix_local_tasks_claim", table_name="local_tasks")
    op.drop_table("local_tasks")
    op.drop_table("reading_bookmarks")
    op.drop_table("project_setup_drafts")
