"""Add durable lineage metadata for story restart and version retention.

Revision ID: 20260727_0030
Revises: 20260727_0029
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260727_0030"
down_revision: str | None = "20260727_0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("state_versions", sa.Column("parent_number", sa.Integer(), nullable=True))
    op.add_column(
        "state_versions", sa.Column("rollback_of_number", sa.Integer(), nullable=True)
    )
    op.add_column(
        "state_versions",
        sa.Column("restart_of_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "state_versions", sa.Column("restart_of_number", sa.Integer(), nullable=True)
    )
    op.add_column(
        "state_versions", sa.Column("restart_base_number", sa.Integer(), nullable=True)
    )
    op.create_foreign_key(
        "fk_state_versions_restart_of_id",
        "state_versions",
        "state_versions",
        ["restart_of_id"],
        ["id"],
    )
    op.execute(
        sa.text(
            """
            UPDATE state_versions AS child
            SET parent_number = parent.number
            FROM state_versions AS parent
            WHERE child.parent_id = parent.id
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE state_versions AS child
            SET rollback_of_number = target.number
            FROM state_versions AS target
            WHERE child.rollback_of_id = target.id
            """
        )
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_state_versions_restart_of_id", "state_versions", type_="foreignkey"
    )
    op.drop_column("state_versions", "restart_base_number")
    op.drop_column("state_versions", "restart_of_number")
    op.drop_column("state_versions", "restart_of_id")
    op.drop_column("state_versions", "rollback_of_number")
    op.drop_column("state_versions", "parent_number")
