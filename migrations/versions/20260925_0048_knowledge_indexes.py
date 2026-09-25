"""Novel-owned, version-scoped knowledge projections and a transactional index queue."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

from novel_writer.knowledge.models import Vector

revision: str = "20260925_0048"
down_revision: str | None = "20260921_0047"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "knowledge_indexes",
        sa.Column(
            "version_id",
            UUID(),
            sa.ForeignKey("state_versions.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "project_id",
            UUID(),
            sa.ForeignKey("story_projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(24), nullable=False, server_default="queued"),
        sa.Column("manifest_sha256", sa.String(64)),
        sa.Column("model_key", sa.String(64)),
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(80)),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_knowledge_indexes_project_id", "knowledge_indexes", ["project_id"])
    op.create_table(
        "knowledge_chunks",
        sa.Column(
            "project_id",
            UUID(),
            sa.ForeignKey("story_projects.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "version_id",
            UUID(),
            sa.ForeignKey("state_versions.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("text_sha256", sa.String(64), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
    )
    op.create_index("ix_knowledge_chunks_text_sha256", "knowledge_chunks", ["text_sha256"])
    op.create_table(
        "knowledge_vectors",
        sa.Column(
            "project_id",
            UUID(),
            sa.ForeignKey("story_projects.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("model_key", sa.String(64), primary_key=True),
        sa.Column("text_sha256", sa.String(64), primary_key=True),
        sa.Column("embedding", Vector(), nullable=False),
    )
    op.execute("""
        CREATE FUNCTION queue_knowledge_version() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.current_version_id IS NOT NULL AND
             (TG_OP = 'INSERT' OR NEW.current_version_id IS DISTINCT FROM OLD.current_version_id)
          THEN
            INSERT INTO knowledge_indexes(version_id, project_id)
            VALUES (NEW.current_version_id, NEW.id) ON CONFLICT DO NOTHING;
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER knowledge_version_changed
        AFTER INSERT OR UPDATE OF current_version_id ON story_projects
        FOR EACH ROW EXECUTE FUNCTION queue_knowledge_version();
        INSERT INTO knowledge_indexes(version_id, project_id)
        SELECT current_version_id, id FROM story_projects WHERE current_version_id IS NOT NULL
        ON CONFLICT DO NOTHING;
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER knowledge_version_changed ON story_projects")
    op.execute("DROP FUNCTION queue_knowledge_version()")
    op.drop_table("knowledge_vectors")
    op.drop_table("knowledge_chunks")
    op.drop_table("knowledge_indexes")
