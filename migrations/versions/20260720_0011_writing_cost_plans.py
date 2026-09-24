"""Add unified writing-session cost plans."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260720_0011"
down_revision = "20260720_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "writing_cost_plans",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("model", sa.String(160), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("role_limits", postgresql.JSONB(), nullable=False),
        sa.Column("pricing", postgresql.JSONB(), nullable=False),
        sa.Column("data_scope", postgresql.JSONB(), nullable=False),
        sa.Column("estimated_input_tokens", sa.Integer(), nullable=False),
        sa.Column("estimated_output_tokens", sa.Integer(), nullable=False),
        sa.Column("estimated_cost_cny", sa.Numeric(12, 4), nullable=True),
        sa.Column("pricing_known", sa.Boolean(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["session_id"], ["writing_sessions.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_writing_cost_plans_session_created",
        "writing_cost_plans",
        ["session_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_writing_cost_plans_session_created",
        table_name="writing_cost_plans",
    )
    op.drop_table("writing_cost_plans")
