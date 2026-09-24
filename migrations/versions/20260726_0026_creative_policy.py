"""Persist author-editable creative policy and per-session scene target.

Revision ID: 20260726_0026
Revises: 20260726_0025
Create Date: 2026-07-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260726_0026"
down_revision: str | None = "20260726_0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "writing_sessions",
        sa.Column("scene_target_chars", sa.Integer(), server_default="4000", nullable=False),
    )
    op.create_table(
        "project_creative_policies",
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("target_chapters", sa.Integer(), nullable=False),
        sa.Column("scene_target_chars", sa.Integer(), nullable=False),
        sa.Column("chapter_target_chars", sa.Integer(), nullable=False),
        sa.Column("target_min_chars", sa.Integer(), nullable=False),
        sa.Column("target_max_chars", sa.Integer(), nullable=False),
        sa.Column("minimum_follow_read_signal", sa.String(length=20), nullable=False),
        sa.Column("stop_on_blocking_risk", sa.Boolean(), nullable=False),
        sa.Column("target_cost_cny", sa.Numeric(precision=12, scale=4), nullable=False),
        sa.Column("soft_budget_cny", sa.Numeric(precision=12, scale=4), nullable=False),
        sa.Column("hard_safety_ceiling_cny", sa.Numeric(precision=12, scale=4), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["project_id"], ["story_projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("project_id"),
    )


def downgrade() -> None:
    op.drop_table("project_creative_policies")
    op.drop_column("writing_sessions", "scene_target_chars")
