"""Add local reference corpus manifests, author-reviewed samples and versioned profiles.

Revision ID: 20260727_0028
Revises: 20260726_0027
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260727_0028"
down_revision: str | None = "20260726_0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "reference_corpus_manifests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_path", sa.Text(), nullable=False),
        sa.Column("source_name", sa.String(240), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("source_mtime", sa.DateTime(timezone=True), nullable=False),
        sa.Column("encoding", sa.String(30), nullable=False),
        sa.Column("parser_version", sa.String(60), nullable=False),
        sa.Column(
            "local_analysis_allowed", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "provider_excerpt_allowed", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("chapters", postgresql.JSONB(), nullable=False),
        sa.Column("parse_report", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["project_id"], ["story_projects.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("project_id", "source_sha256", name="uq_reference_corpus_project_sha"),
    )
    op.create_index(
        "ix_reference_corpus_project_created",
        "reference_corpus_manifests",
        ["project_id", "created_at"],
    )
    op.create_table(
        "reference_style_samples",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("manifest_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("category", sa.String(40), nullable=False),
        sa.Column("chapter_ordinal", sa.Integer(), nullable=False),
        sa.Column("chapter_title", sa.String(200), nullable=False),
        sa.Column("start_offset", sa.Integer(), nullable=False),
        sa.Column("end_offset", sa.Integer(), nullable=False),
        sa.Column("raw_sha256", sa.String(64), nullable=False),
        sa.Column("cleaned_sha256", sa.String(64), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="candidate"),
        sa.Column("approved_dimensions", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("prohibited_transfer", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("author_note", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('candidate', 'approved', 'excluded', 'false_positive')",
            name="ck_reference_style_samples_status",
        ),
        sa.ForeignKeyConstraint(
            ["manifest_id"], ["reference_corpus_manifests.id"], ondelete="CASCADE"
        ),
    )
    op.create_index(
        "ix_reference_style_samples_manifest_category",
        "reference_style_samples",
        ["manifest_id", "category"],
    )
    op.create_table(
        "reference_style_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("manifest_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("structural_ranges", postgresql.JSONB(), nullable=False),
        sa.Column("positive_contract", postgresql.JSONB(), nullable=False),
        sa.Column("negative_transfer_rules", postgresql.JSONB(), nullable=False),
        sa.Column("provenance", postgresql.JSONB(), nullable=False),
        sa.Column("blind_test", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("activated_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('draft', 'active', 'superseded')", name="ck_reference_style_profiles_status"
        ),
        sa.ForeignKeyConstraint(["project_id"], ["story_projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["manifest_id"], ["reference_corpus_manifests.id"]),
        sa.UniqueConstraint("project_id", "version", name="uq_reference_style_profile_version"),
    )
    op.create_index(
        "ix_reference_style_profile_project_status",
        "reference_style_profiles",
        ["project_id", "status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_reference_style_profile_project_status", table_name="reference_style_profiles"
    )
    op.drop_table("reference_style_profiles")
    op.drop_index(
        "ix_reference_style_samples_manifest_category", table_name="reference_style_samples"
    )
    op.drop_table("reference_style_samples")
    op.drop_index("ix_reference_corpus_project_created", table_name="reference_corpus_manifests")
    op.drop_table("reference_corpus_manifests")
