"""Add at-most-once Writer chunk call ledger."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260720_0012"
down_revision = "20260720_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "writing_chunk_calls",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cost_plan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("model", sa.String(160), nullable=False),
        sa.Column("request_sha256", sa.String(64), nullable=False),
        sa.Column("context_sha256", sa.String(64), nullable=False),
        sa.Column("max_input_tokens", sa.Integer(), nullable=False),
        sa.Column("max_output_tokens", sa.Integer(), nullable=False),
        sa.Column("max_cost_cny", sa.Numeric(12, 4), nullable=False),
        sa.Column("raw_response_sha256", sa.String(64), nullable=True),
        sa.Column("actual_usage", postgresql.JSONB(), nullable=True),
        sa.Column("actual_cost_cny", sa.Numeric(12, 4), nullable=True),
        sa.Column("provider_request_id", sa.String(200), nullable=True),
        sa.Column("failure_reason", sa.String(200), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["session_id"], ["writing_sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["cost_plan_id"], ["writing_cost_plans.id"]),
        sa.UniqueConstraint("session_id", "ordinal"),
    )


def downgrade() -> None:
    op.drop_table("writing_chunk_calls")
