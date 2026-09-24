"""Add project naturalness policy and evidence-bound regression samples.

Revision ID: 20260727_0029
Revises: 20260727_0028
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260727_0029"
down_revision: str | None = "20260727_0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "project_naturalness_policies",
        sa.Column("project_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("reference_style_profile_id", postgresql.UUID(as_uuid=True)),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("sensitivity", sa.String(20), nullable=False, server_default="balanced"),
        sa.Column("diagnostic_only", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "sensitivity IN ('conservative', 'balanced', 'sensitive')",
            name="ck_naturalness_policy_sensitivity",
        ),
        sa.ForeignKeyConstraint(["project_id"], ["story_projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["reference_style_profile_id"], ["reference_style_profiles.id"]),
    )
    op.create_table(
        "naturalness_regression_samples",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("revision_id", postgresql.UUID(as_uuid=True)),
        sa.Column("session_id", postgresql.UUID(as_uuid=True)),
        sa.Column("body_sha256", sa.String(64), nullable=False),
        sa.Column("issue_family", sa.String(40), nullable=False),
        sa.Column("decision", sa.String(30), nullable=False),
        sa.Column("start_offset", sa.Integer(), nullable=False),
        sa.Column("end_offset", sa.Integer(), nullable=False),
        sa.Column("quote", sa.Text(), nullable=False),
        sa.Column(
            "suggested_action",
            sa.String(40),
            nullable=False,
            server_default="manual-review",
        ),
        sa.Column("protected_content", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("author_note", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "decision IN ('should-flag', 'should-not-flag', 'protection')",
            name="ck_naturalness_sample_decision",
        ),
        sa.ForeignKeyConstraint(["project_id"], ["story_projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["revision_id"], ["chapter_revisions.id"]),
        sa.ForeignKeyConstraint(["session_id"], ["writing_sessions.id"]),
        sa.UniqueConstraint(
            "project_id",
            "body_sha256",
            "start_offset",
            "end_offset",
            "issue_family",
            name="uq_naturalness_sample_evidence",
        ),
    )
    op.create_index(
        "ix_naturalness_samples_project_decision",
        "naturalness_regression_samples",
        ["project_id", "decision", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_naturalness_samples_project_decision",
        table_name="naturalness_regression_samples",
    )
    op.drop_table("naturalness_regression_samples")
    op.drop_table("project_naturalness_policies")
