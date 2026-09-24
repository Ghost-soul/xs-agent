"""Add collaborative ChapterBrief planning sessions."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260719_0005"
down_revision = "20260718_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "brief_planning_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "chapter_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chapters.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "base_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("state_versions.id"),
            nullable=False,
        ),
        sa.Column("inspiration", sa.Text(), nullable=False),
        sa.Column("must_include", postgresql.JSONB(), nullable=False),
        sa.Column("avoid", postgresql.JSONB(), nullable=False),
        sa.Column("options", postgresql.JSONB(), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("provider", sa.String(20), nullable=True),
        sa.Column("model", sa.String(120), nullable=True),
        sa.Column("actual_usage", postgresql.JSONB(), nullable=True),
        sa.Column("provider_request_id", sa.String(200), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column(
            "finalized_brief_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chapter_briefs.id"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_brief_planning_sessions_chapter_created",
        "brief_planning_sessions",
        ["chapter_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_brief_planning_sessions_chapter_created",
        table_name="brief_planning_sessions",
    )
    op.drop_table("brief_planning_sessions")
