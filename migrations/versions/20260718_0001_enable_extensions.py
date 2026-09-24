"""Enable PostgreSQL extensions required by the application."""

from alembic import op

revision = "20260718_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")


def downgrade() -> None:
    # Extensions may be shared by future data, so downgrade intentionally preserves them.
    pass
