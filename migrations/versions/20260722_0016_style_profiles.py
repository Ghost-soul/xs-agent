"""Add project genre quality cards and reusable style assets."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260722_0016"
down_revision = "20260721_0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "project_style_profiles",
        sa.Column("project_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("genre_id", sa.String(40), nullable=False),
        sa.Column("assets", postgresql.JSONB(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["project_id"], ["story_projects.id"], ondelete="CASCADE"
        ),
    )


def downgrade() -> None:
    op.drop_table("project_style_profiles")
