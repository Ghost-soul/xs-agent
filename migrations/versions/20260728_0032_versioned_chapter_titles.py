"""Version formal chapter titles alongside revision mappings.

Revision ID: 20260728_0032
Revises: 20260728_0031
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260728_0032"
down_revision: str | None = "20260728_0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "state_versions",
        sa.Column(
            "chapter_titles",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.execute(
        """
        UPDATE state_versions AS version
        SET chapter_titles = COALESCE(
            (
                SELECT jsonb_object_agg(chapter.id::text, chapter.title)
                FROM chapters AS chapter
                WHERE chapter.project_id = version.project_id
                  AND version.chapter_revisions ? chapter.id::text
            ),
            '{}'::jsonb
        )
        """
    )
    op.alter_column("state_versions", "chapter_titles", server_default=None)


def downgrade() -> None:
    op.drop_column("state_versions", "chapter_titles")
