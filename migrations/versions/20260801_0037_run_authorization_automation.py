"""add run authorization slots and durable automation state

Revision ID: 20260801_0037
Revises: 20260801_0036
Create Date: 2026-08-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260801_0037"
down_revision: str | None = "20260801_0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "run_authorizations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("novel_automation_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("story_projects.id"),
            nullable=False,
        ),
        sa.Column(
            "base_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("state_versions.id"),
            nullable=False,
        ),
        sa.Column("run_spec_sha256", sa.String(64), nullable=False),
        sa.Column("provider_profile_revision", sa.String(160), nullable=False),
        sa.Column("envelope", postgresql.JSONB(), nullable=False),
        sa.Column("envelope_sha256", sa.String(64), nullable=False),
        sa.Column("total_cost_ceiling_cny", sa.Numeric(12, 4), nullable=False),
        sa.Column("stop_policy", sa.String(80), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("run_id", "envelope_sha256", name="uq_run_authorization_envelope"),
    )
    op.create_table(
        "authorized_action_slots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "authorization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("run_authorizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("logical_action_id", sa.String(160), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("action_kind", sa.String(80), nullable=False),
        sa.Column("ordinal", sa.Integer()),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("model", sa.String(160), nullable=False),
        sa.Column(
            "prompt_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_prompt_versions.id"),
            nullable=False,
        ),
        sa.Column("max_input_tokens", sa.Integer(), nullable=False),
        sa.Column("max_output_tokens", sa.Integer(), nullable=False),
        sa.Column("visible_content_limit", sa.Integer(), nullable=False),
        sa.Column("reasoning_reserve", sa.Integer(), nullable=False),
        sa.Column("provider_output_limit", sa.Integer(), nullable=False),
        sa.Column("max_cost_cny", sa.Numeric(12, 4), nullable=False),
        sa.Column("data_scope_sha256", sa.String(64), nullable=False),
        sa.Column("optional", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(30), nullable=False, server_default="authorized"),
        sa.Column("reservation_id", postgresql.UUID(as_uuid=True)),
        sa.Column("claimed_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "authorization_id",
            "logical_action_id",
            name="uq_authorized_action_slot_logical_id",
        ),
        sa.UniqueConstraint("reservation_id", name="uq_authorized_action_slot_reservation"),
    )
    op.create_index(
        "ix_authorized_action_slots_authorization_status",
        "authorized_action_slots",
        ["authorization_id", "status"],
    )
    op.create_table(
        "run_automation_states",
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("novel_automation_runs.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "authorization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("run_authorizations.id"),
            nullable=False,
        ),
        sa.Column("mode", sa.String(30), nullable=False, server_default="run_to_review"),
        sa.Column("status", sa.String(30), nullable=False, server_default="queued"),
        sa.Column("idempotency_key_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("envelope_sha256", sa.String(64), nullable=False),
        sa.Column("lease_owner", sa.String(160)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("last_decision_sha256", sa.String(64)),
        sa.Column("last_completed_action_id", sa.String(160)),
        sa.Column("pause_code", sa.String(100)),
        sa.Column("pause_details_sha256", sa.String(64)),
        sa.Column("safety_manifest_sha256", sa.String(64)),
        sa.Column("stop_requested_at", sa.DateTime(timezone=True)),
        sa.Column("state_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("run_id", name="uq_run_automation_state_run"),
    )
    op.create_index(
        "ix_run_automation_claim",
        "run_automation_states",
        ["status", "lease_expires_at"],
    )
    op.add_column(
        "writing_chunk_calls",
        sa.Column(
            "authorization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("run_authorizations.id"),
        ),
    )
    op.add_column("writing_chunk_calls", sa.Column("logical_action_id", sa.String(160)))
    op.add_column(
        "writing_chunk_calls", sa.Column("reservation_id", postgresql.UUID(as_uuid=True))
    )


def downgrade() -> None:
    op.drop_column("writing_chunk_calls", "reservation_id")
    op.drop_column("writing_chunk_calls", "logical_action_id")
    op.drop_column("writing_chunk_calls", "authorization_id")
    op.drop_index("ix_run_automation_claim", table_name="run_automation_states")
    op.drop_table("run_automation_states")
    op.drop_index(
        "ix_authorized_action_slots_authorization_status",
        table_name="authorized_action_slots",
    )
    op.drop_table("authorized_action_slots")
    op.drop_table("run_authorizations")
