"""Add rejection, correction, regeneration, and recovery lineage."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260718_0003"
down_revision = "20260718_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "approvals_target_type_target_id_key",
        "approvals",
        type_="unique",
    )
    op.create_index(
        "ix_approvals_target_created",
        "approvals",
        ["target_type", "target_id", "created_at"],
    )

    op.add_column(
        "chapter_briefs",
        sa.Column("supersedes_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_chapter_briefs_supersedes",
        "chapter_briefs",
        "chapter_briefs",
        ["supersedes_id"],
        ["id"],
    )

    op.add_column(
        "chapter_revisions",
        sa.Column("brief_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "chapter_revisions",
        sa.Column("supersedes_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_chapter_revisions_brief",
        "chapter_revisions",
        "chapter_briefs",
        ["brief_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_chapter_revisions_supersedes",
        "chapter_revisions",
        "chapter_revisions",
        ["supersedes_id"],
        ["id"],
    )
    op.execute(
        """
        UPDATE chapter_revisions AS revision
        SET brief_id = (
            SELECT brief.id
            FROM chapter_briefs AS brief
            WHERE brief.chapter_id = revision.chapter_id
              AND brief.base_version_id = revision.base_version_id
            ORDER BY brief.created_at DESC, brief.id DESC
            LIMIT 1
        )
        """
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_chapter_revisions_supersedes",
        "chapter_revisions",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_chapter_revisions_brief",
        "chapter_revisions",
        type_="foreignkey",
    )
    op.drop_column("chapter_revisions", "supersedes_id")
    op.drop_column("chapter_revisions", "brief_id")

    op.drop_constraint(
        "fk_chapter_briefs_supersedes",
        "chapter_briefs",
        type_="foreignkey",
    )
    op.drop_column("chapter_briefs", "supersedes_id")

    op.drop_index("ix_approvals_target_created", table_name="approvals")
    op.execute(
        """
        DELETE FROM approvals
        WHERE id IN (
            SELECT id
            FROM (
                SELECT
                    id,
                    row_number() OVER (
                        PARTITION BY target_type, target_id
                        ORDER BY created_at DESC, id DESC
                    ) AS position
                FROM approvals
            ) AS ranked
            WHERE position > 1
        )
        """
    )
    op.create_unique_constraint(
        "approvals_target_type_target_id_key",
        "approvals",
        ["target_type", "target_id"],
    )
