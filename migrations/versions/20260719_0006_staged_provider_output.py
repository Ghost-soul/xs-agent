"""Persist provider output before local validation."""

import sqlalchemy as sa
from alembic import op

revision = "20260719_0006"
down_revision = "20260719_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "baseline_generation_plans",
        sa.Column("provider_output_sha256", sa.String(64), nullable=True),
    )
    op.add_column(
        "baseline_generation_plans",
        sa.Column("validation_error", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("baseline_generation_plans", "validation_error")
    op.drop_column("baseline_generation_plans", "provider_output_sha256")
