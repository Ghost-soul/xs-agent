"""Separate historical chapter identity from current display numbering.

Revision ID: 20260723_0021
Revises: 20260722_0020
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260723_0021"
down_revision: str | None = "20260722_0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("chapters", sa.Column("display_ordinal", sa.Integer(), nullable=True))
    op.execute(
        """
        WITH visible AS (
            SELECT c.id,
                   row_number() OVER (
                       PARTITION BY c.project_id ORDER BY c.ordinal, c.id
                   )::integer AS display_ordinal
            FROM chapters c
            JOIN story_projects p ON p.id = c.project_id
            JOIN state_versions v ON v.id = p.current_version_id
            WHERE v.chapter_revisions ? c.id::text
        )
        UPDATE chapters c
        SET display_ordinal = visible.display_ordinal
        FROM visible
        WHERE c.id = visible.id
        """
    )
    op.execute(
        """
        UPDATE chapters
        SET title = '第' || display_ordinal::text || '章'
        WHERE display_ordinal IS NOT NULL AND title ~ '^第[0-9]+章$'
        """
    )
    op.create_unique_constraint(
        "uq_chapters_project_display_ordinal",
        "chapters",
        ["project_id", "display_ordinal"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_chapters_project_display_ordinal", "chapters", type_="unique"
    )
    op.drop_column("chapters", "display_ordinal")
