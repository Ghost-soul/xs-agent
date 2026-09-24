"""Add immutable prompt versions and bind future agent calls to rendered prompts."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260721_0014"
down_revision = "20260721_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_prompt_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("system_template", sa.Text(), nullable=False),
        sa.Column("template_sha256", sa.String(64), nullable=False),
        sa.Column("input_contract", postgresql.JSONB(), nullable=False),
        sa.Column("output_contract", postgresql.JSONB(), nullable=False),
        sa.Column("author_reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("role", "version", name="uq_agent_prompt_role_version"),
        sa.CheckConstraint(
            "role IN ('director', 'writer', 'memory', 'checker', 'editor', 'reader')",
            name="ck_agent_prompt_versions_role",
        ),
    )
    op.create_table(
        "agent_prompt_activations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("prompt_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["prompt_version_id"], ["agent_prompt_versions.id"]),
        sa.CheckConstraint(
            "role IN ('director', 'writer', 'memory', 'checker', 'editor', 'reader')",
            name="ck_agent_prompt_activations_role",
        ),
    )
    op.create_index(
        "ix_agent_prompt_activation_role_created",
        "agent_prompt_activations",
        ["role", "created_at", "id"],
    )
    op.add_column(
        "writing_chunk_calls",
        sa.Column("prompt_version_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "writing_chunk_calls",
        sa.Column("rendered_prompt_sha256", sa.String(64), nullable=True),
    )
    op.create_foreign_key(
        "fk_agent_calls_prompt_version",
        "writing_chunk_calls",
        "agent_prompt_versions",
        ["prompt_version_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_agent_calls_prompt_version", "writing_chunk_calls", type_="foreignkey"
    )
    op.drop_column("writing_chunk_calls", "rendered_prompt_sha256")
    op.drop_column("writing_chunk_calls", "prompt_version_id")
    op.drop_index(
        "ix_agent_prompt_activation_role_created", table_name="agent_prompt_activations"
    )
    op.drop_table("agent_prompt_activations")
    op.drop_table("agent_prompt_versions")
