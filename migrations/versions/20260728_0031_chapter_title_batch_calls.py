"""Add the non-Agent chapter title batch operation ledger.

Revision ID: 20260728_0031
Revises: 20260727_0030
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260728_0031"
down_revision: str | None = "20260727_0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chapter_title_batch_calls",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cost_plan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(length=64), nullable=False),
        sa.Column("review_sha256", sa.String(length=64), nullable=False),
        sa.Column("context_sha256", sa.String(length=64), nullable=False),
        sa.Column("request_sha256", sa.String(length=64), nullable=False),
        sa.Column("prompt_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "request_contexts",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("model", sa.String(length=160), nullable=False),
        sa.Column("max_input_tokens", sa.Integer(), nullable=False),
        sa.Column("max_output_tokens", sa.Integer(), nullable=False),
        sa.Column("reserved_cost_cny", sa.Numeric(precision=12, scale=4), nullable=False),
        sa.Column("raw_response_sha256", sa.String(length=64), nullable=True),
        sa.Column("actual_usage", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("actual_cost_cny", sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column("provider_request_id", sa.String(length=200), nullable=True),
        sa.Column("failure_reason", sa.String(length=200), nullable=True),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('executing', 'completed', 'completed_stale', "
            "'failed_before_dispatch', 'response_invalid', 'outcome_uncertain')",
            name="ck_title_batch_status",
        ),
        sa.ForeignKeyConstraint(["cost_plan_id"], ["writing_cost_plans.id"], ondelete=None),
        sa.ForeignKeyConstraint(["session_id"], ["writing_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "session_id", "idempotency_key_hash", name="uq_title_batch_session_idempotency"
        ),
        sa.UniqueConstraint(
            "session_id",
            "review_sha256",
            "attempt",
            name="uq_title_batch_session_review_attempt",
        ),
    )
    op.create_index(
        "ix_title_batch_session_started",
        "chapter_title_batch_calls",
        ["session_id", "started_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_title_batch_session_started", table_name="chapter_title_batch_calls")
    op.drop_table("chapter_title_batch_calls")
