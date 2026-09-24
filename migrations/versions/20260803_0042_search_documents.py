"""add rebuildable local search documents

Revision ID: 20260803_0042
Revises: 20260803_0041
Create Date: 2026-08-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260803_0042"
down_revision: str | None = "20260803_0041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _is_postgresql() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    if _is_postgresql():
        op.execute(
            """
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_available_extensions WHERE name = 'pg_trgm'
                ) THEN
                    RAISE EXCEPTION 'required PostgreSQL extension pg_trgm is unavailable';
                END IF;
            END
            $$
            """
        )
        op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    locator_type = sa.JSON().with_variant(
        postgresql.JSONB(astext_type=sa.Text()),
        "postgresql",
    )
    op.create_table(
        "search_documents",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "schema_version",
            sa.String(40),
            nullable=False,
            server_default="search-document-v1",
        ),
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey("story_projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("document_key", sa.String(64), nullable=False),
        sa.Column("source_kind", sa.String(40), nullable=False),
        sa.Column("source_id", sa.String(200), nullable=False),
        sa.Column(
            "version_id",
            sa.Uuid(),
            sa.ForeignKey("state_versions.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("version_number", sa.Integer(), nullable=True),
        sa.Column(
            "revision_id",
            sa.Uuid(),
            sa.ForeignKey("chapter_revisions.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("normalized_text", sa.Text(), nullable=False),
        sa.Column("text_sha256", sa.String(64), nullable=False),
        sa.Column("locator_metadata", locator_type, nullable=False),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "project_id",
            "document_key",
            name="uq_search_documents_project_key",
        ),
    )
    op.create_index(
        "ix_search_documents_project_current_kind",
        "search_documents",
        ["project_id", "is_current", "source_kind", "id"],
    )
    if _is_postgresql():
        op.create_index(
            "ix_search_documents_text_trgm",
            "search_documents",
            ["normalized_text"],
            postgresql_using="gin",
            postgresql_ops={"normalized_text": "gin_trgm_ops"},
        )
        op.create_index(
            "ix_search_documents_title_trgm",
            "search_documents",
            ["title"],
            postgresql_using="gin",
            postgresql_ops={"title": "gin_trgm_ops"},
        )


def downgrade() -> None:
    if _is_postgresql():
        op.drop_index("ix_search_documents_title_trgm", table_name="search_documents")
        op.drop_index("ix_search_documents_text_trgm", table_name="search_documents")
    op.drop_index(
        "ix_search_documents_project_current_kind",
        table_name="search_documents",
    )
    op.drop_table("search_documents")
