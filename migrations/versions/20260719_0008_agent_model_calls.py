"""Add confirmed agent generation plans and per-role call ledger."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260719_0008"
down_revision = "20260719_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_generation_plans",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("chapter_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("brief_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("base_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("model", sa.String(120), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("role_output_limits", postgresql.JSONB(), nullable=False),
        sa.Column("pricing_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("estimated_input_tokens", sa.Integer(), nullable=False),
        sa.Column("estimated_output_tokens", sa.Integer(), nullable=False),
        sa.Column("estimated_cost_usd", sa.Numeric(18, 8), nullable=False),
        sa.Column("estimated_cost_cny", sa.Numeric(12, 4), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["chapter_id"], ["chapters.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["brief_id"], ["chapter_briefs.id"]),
        sa.ForeignKeyConstraint(["base_version_id"], ["state_versions.id"]),
    )
    op.add_column(
        "agent_runs",
        sa.Column("generation_plan_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_agent_runs_generation_plan",
        "agent_runs",
        "agent_generation_plans",
        ["generation_plan_id"],
        ["id"],
    )
    op.create_table(
        "agent_model_calls",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(30), nullable=False),
        sa.Column("round_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("provider_output_sha256", sa.String(64), nullable=True),
        sa.Column("actual_usage", postgresql.JSONB(), nullable=True),
        sa.Column("actual_cost_usd", sa.Numeric(18, 8), nullable=True),
        sa.Column("actual_cost_cny", sa.Numeric(12, 4), nullable=True),
        sa.Column("provider_request_id", sa.String(200), nullable=True),
        sa.Column("failure_reason", sa.String(200), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("run_id", "role", "round_number"),
    )


def downgrade() -> None:
    op.drop_table("agent_model_calls")
    op.drop_constraint("fk_agent_runs_generation_plan", "agent_runs", type_="foreignkey")
    op.drop_column("agent_runs", "generation_plan_id")
    op.drop_table("agent_generation_plans")
