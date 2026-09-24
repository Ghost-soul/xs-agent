"""Add persistent whole-novel automation runs and append-only checkpoints."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260721_0015"
down_revision = "20260721_0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "novel_automation_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("base_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("current_session_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("phase", sa.String(40), nullable=False),
        sa.Column("spec", postgresql.JSONB(), nullable=False),
        sa.Column("state", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["project_id"], ["story_projects.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["base_version_id"], ["state_versions.id"]),
        sa.ForeignKeyConstraint(["current_session_id"], ["writing_sessions.id"]),
    )
    op.create_index(
        "ix_novel_automation_runs_project_created",
        "novel_automation_runs",
        ["project_id", "created_at"],
    )
    op.create_table(
        "novel_automation_checkpoints",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["run_id"], ["novel_automation_runs.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "run_id", "sequence", name="uq_novel_run_checkpoint_sequence"
        ),
    )


def downgrade() -> None:
    op.drop_table("novel_automation_checkpoints")
    op.drop_index(
        "ix_novel_automation_runs_project_created",
        table_name="novel_automation_runs",
    )
    op.drop_table("novel_automation_runs")
