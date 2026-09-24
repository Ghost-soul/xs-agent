"""persist real reader notes and calibration rule history

Revision ID: 20260806_0043
Revises: 20260803_0042
Create Date: 2026-08-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260806_0043"
down_revision: str | None = "20260803_0042"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    jsonb = postgresql.JSONB(astext_type=sa.Text())
    op.create_table(
        "real_reader_notes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "schema_version",
            sa.String(40),
            nullable=False,
            server_default="real-reader-note-v1",
        ),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("story_projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "chapter_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chapters.id"),
            nullable=False,
        ),
        sa.Column(
            "revision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chapter_revisions.id"),
            nullable=False,
        ),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("body_sha256", sa.String(64), nullable=False),
        sa.Column("reader_id", sa.String(120), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", jsonb, nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('active', 'revoked')",
            name="ck_real_reader_notes_status",
        ),
    )
    op.create_index(
        "ix_real_reader_notes_project_recorded",
        "real_reader_notes",
        ["project_id", "recorded_at"],
    )
    op.create_index(
        "ix_real_reader_notes_project_chapter",
        "real_reader_notes",
        ["project_id", "chapter_id"],
    )

    op.create_table(
        "reader_calibration_rules",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "schema_version",
            sa.String(40),
            nullable=False,
            server_default="reader-calibration-rule-v1",
        ),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("story_projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("rule_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="advisory"),
        sa.Column("sample_sha256", sa.String(64), nullable=False),
        sa.Column("report_sha256", sa.String(64), nullable=False),
        sa.Column("sample_manifest", jsonb, nullable=False),
        sa.Column("report", jsonb, nullable=False),
        sa.Column("rule_payload", jsonb, nullable=False, server_default="{}"),
        sa.Column(
            "based_on_rule_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("reader_calibration_rules.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rolled_back_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "project_id",
            "rule_version",
            name="uq_reader_calibration_rules_project_version",
        ),
        sa.CheckConstraint(
            "status IN ('advisory', 'active', 'superseded', 'rolled_back')",
            name="ck_reader_calibration_rules_status",
        ),
    )
    op.create_index(
        "ix_reader_calibration_rules_project_status",
        "reader_calibration_rules",
        ["project_id", "status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_reader_calibration_rules_project_status",
        table_name="reader_calibration_rules",
    )
    op.drop_table("reader_calibration_rules")
    op.drop_index("ix_real_reader_notes_project_chapter", table_name="real_reader_notes")
    op.drop_index("ix_real_reader_notes_project_recorded", table_name="real_reader_notes")
    op.drop_table("real_reader_notes")
