"""Add V0 chapter provider locks and baseline cost plans."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260718_0004"
down_revision = "20260718_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chapter_provider_locks",
        sa.Column(
            "chapter_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chapters.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_table(
        "baseline_generation_plans",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "chapter_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chapters.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "brief_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chapter_briefs.id"),
            nullable=False,
        ),
        sa.Column(
            "base_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("state_versions.id"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("model", sa.String(120), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("max_output_tokens", sa.Integer(), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("estimated_input_tokens", sa.Integer(), nullable=False),
        sa.Column("pricing_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("estimated_cost_usd", sa.Numeric(18, 8), nullable=False),
        sa.Column("estimated_cost_cny", sa.Numeric(12, 4), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("actual_usage", postgresql.JSONB(), nullable=True),
        sa.Column("actual_cost_usd", sa.Numeric(18, 8), nullable=True),
        sa.Column("actual_cost_cny", sa.Numeric(12, 4), nullable=True),
        sa.Column("provider_request_id", sa.String(200), nullable=True),
        sa.Column(
            "revision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chapter_revisions.id"),
            nullable=True,
        ),
        sa.Column("failure_reason", sa.String(200), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_baseline_generation_plans_chapter_status",
        "baseline_generation_plans",
        ["chapter_id", "status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_baseline_generation_plans_chapter_status",
        table_name="baseline_generation_plans",
    )
    op.drop_table("baseline_generation_plans")
    op.drop_table("chapter_provider_locks")
