"""Add chapter_summaries and context_compactions tables.

S3: chapter_summaries stores structured summaries extracted from formally
merged chapters (zero LLM calls). S2: context_compactions stores persisted
progressive compression results for Writer context management.

Revision ID: 20260726_0022
Revises: 20260723_0021
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "20260726_0022"
down_revision: str | None = "20260723_0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chapter_summaries",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("story_projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("chapter_number", sa.Integer(), nullable=False),
        sa.Column("volume_number", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("key_events", JSONB(), nullable=False, server_default="[]"),
        sa.Column("characters_present", JSONB(), nullable=False, server_default="[]"),
        sa.Column("character_changes", JSONB(), nullable=False, server_default="[]"),
        sa.Column("promises_touched", JSONB(), nullable=False, server_default="[]"),
        sa.Column("foreshadowings_touched", JSONB(), nullable=False, server_default="[]"),
        sa.Column("location", sa.String(200), nullable=True),
        sa.Column("time_position", sa.String(200), nullable=True),
        sa.Column("word_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("narrative_summary", sa.Text(), nullable=True),
        sa.Column("revision_id", UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "project_id", "chapter_number", name="uq_chapter_summaries_project_chapter"
        ),
    )

    op.create_table(
        "context_compactions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "session_id",
            UUID(as_uuid=True),
            sa.ForeignKey("writing_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_type", sa.String(50), nullable=False),
        sa.Column("source_chapter_range", sa.String(20), nullable=True),
        sa.Column("original_tokens", sa.Integer(), nullable=False),
        sa.Column("summary_tokens", sa.Integer(), nullable=False),
        sa.Column("summary_text", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_context_compactions_session_type",
        "context_compactions",
        ["session_id", "source_type"],
    )


def downgrade() -> None:
    op.drop_index("ix_context_compactions_session_type", table_name="context_compactions")
    op.drop_table("context_compactions")
    op.drop_table("chapter_summaries")
