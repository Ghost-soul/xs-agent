"""persist idempotency request schema and resource scope

Revision ID: 20260803_0040
Revises: 20260803_0039
Create Date: 2026-08-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260803_0040"
down_revision: str | None = "20260803_0039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "idempotency_keys",
        sa.Column("request_schema_version", sa.String(40), nullable=True),
    )
    op.add_column(
        "idempotency_keys",
        sa.Column("resource_scope", sa.String(240), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("idempotency_keys", "resource_scope")
    op.drop_column("idempotency_keys", "request_schema_version")
