"""Keep local Qwen for lightweight rule checks only.

Revision ID: 20260722_0019
Revises: 20260722_0018
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260722_0019"
down_revision: str | None = "20260722_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE project_local_model_profiles "
            "SET preferred_tasks = CAST(:tasks AS jsonb), updated_at = now()"
        ).bindparams(tasks='["rule_check"]')
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE project_local_model_profiles "
            "SET preferred_tasks = CAST(:tasks AS jsonb), updated_at = now()"
        ).bindparams(
            tasks='["memory", "world_review", "rule_check", "thread_check"]'
        )
    )
