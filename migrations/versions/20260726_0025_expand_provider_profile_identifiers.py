"""Expand provider profile and model identifiers.

Revision ID: 20260726_0025
Revises: 20260726_0024
Create Date: 2026-07-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260726_0025"
down_revision: str | None = "20260726_0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_PROVIDER_TABLES = (
    "brief_planning_sessions",
    "baseline_generation_plans",
    "agent_generation_plans",
)


def upgrade() -> None:
    for table in _PROVIDER_TABLES:
        op.alter_column(
            table,
            "provider",
            existing_type=sa.String(length=20),
            type_=sa.String(length=40),
            existing_nullable=table == "brief_planning_sessions",
        )
        op.alter_column(
            table,
            "model",
            existing_type=sa.String(length=120),
            type_=sa.String(length=160),
            existing_nullable=table == "brief_planning_sessions",
        )


def downgrade() -> None:
    for table in reversed(_PROVIDER_TABLES):
        op.alter_column(
            table,
            "model",
            existing_type=sa.String(length=160),
            type_=sa.String(length=120),
            existing_nullable=table == "brief_planning_sessions",
        )
        op.alter_column(
            table,
            "provider",
            existing_type=sa.String(length=40),
            type_=sa.String(length=20),
            existing_nullable=table == "brief_planning_sessions",
        )
