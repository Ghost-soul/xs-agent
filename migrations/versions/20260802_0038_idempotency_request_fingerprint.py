"""bind idempotency keys to canonical request payloads

Revision ID: 20260802_0038
Revises: 20260801_0037
Create Date: 2026-08-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260802_0038"
down_revision: str | None = "20260801_0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "idempotency_keys",
        sa.Column("request_fingerprint_sha256", sa.String(64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("idempotency_keys", "request_fingerprint_sha256")
