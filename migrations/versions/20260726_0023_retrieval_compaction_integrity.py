"""Tighten chapter-summary lineage and compaction idempotency.

Revision ID: 20260726_0023
Revises: 20260726_0022
Create Date: 2026-07-26
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260726_0023"
down_revision: str | None = "20260726_0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_foreign_key(
        "fk_chapter_summaries_revision_id",
        "chapter_summaries",
        "chapter_revisions",
        ["revision_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_unique_constraint(
        "uq_context_compactions_call_source",
        "context_compactions",
        ["session_id", "source_type", "source_chapter_range"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_context_compactions_call_source",
        "context_compactions",
        type_="unique",
    )
    op.drop_constraint(
        "fk_chapter_summaries_revision_id",
        "chapter_summaries",
        type_="foreignkey",
    )
