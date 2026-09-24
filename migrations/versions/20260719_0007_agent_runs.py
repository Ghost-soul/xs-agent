"""Persist four-role runs and round artifacts."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260719_0007"
down_revision = "20260719_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("chapter_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("brief_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("base_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("max_rounds", sa.Integer(), nullable=False),
        sa.Column("revision_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["chapter_id"], ["chapters.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["brief_id"], ["chapter_briefs.id"]),
        sa.ForeignKeyConstraint(["base_version_id"], ["state_versions.id"]),
        sa.ForeignKeyConstraint(["revision_id"], ["chapter_revisions.id"]),
    )
    op.create_index("ix_agent_runs_brief_created", "agent_runs", ["brief_id", "created_at"])
    op.create_table(
        "agent_artifacts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(30), nullable=False),
        sa.Column("round_number", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("content", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("run_id", "role", "round_number", "kind"),
    )
    op.create_index("ix_agent_artifacts_run_round", "agent_artifacts", ["run_id", "round_number"])


def downgrade() -> None:
    op.drop_index("ix_agent_artifacts_run_round", table_name="agent_artifacts")
    op.drop_table("agent_artifacts")
    op.drop_index("ix_agent_runs_brief_created", table_name="agent_runs")
    op.drop_table("agent_runs")
