"""Generalize the Writer chunk ledger for all continuation roles."""

import sqlalchemy as sa
from alembic import op

revision = "20260721_0013"
down_revision = "20260720_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("writing_chunk_calls", sa.Column("role", sa.String(20), nullable=True))
    op.add_column("writing_chunk_calls", sa.Column("call_key", sa.String(160), nullable=True))
    op.execute("UPDATE writing_chunk_calls SET role = 'writer'")
    op.execute("UPDATE writing_chunk_calls SET call_key = 'chunk:' || ordinal::text")
    op.alter_column("writing_chunk_calls", "role", nullable=False)
    op.alter_column("writing_chunk_calls", "call_key", nullable=False)
    op.alter_column("writing_chunk_calls", "ordinal", nullable=True)
    op.drop_constraint(
        "writing_chunk_calls_session_id_ordinal_key",
        "writing_chunk_calls",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_agent_calls_session_role_key",
        "writing_chunk_calls",
        ["session_id", "role", "call_key"],
    )
    op.create_check_constraint(
        "ck_agent_calls_role",
        "writing_chunk_calls",
        "role IN ('director', 'writer', 'memory', 'checker', 'editor', 'reader')",
    )
    op.create_check_constraint(
        "ck_agent_calls_writer_ordinal",
        "writing_chunk_calls",
        "role <> 'writer' OR ordinal IS NOT NULL",
    )
    op.create_index(
        "ix_agent_calls_session_role_started",
        "writing_chunk_calls",
        ["session_id", "role", "started_at"],
    )


def downgrade() -> None:
    connection = op.get_bind()
    non_writer = connection.scalar(
        sa.text("SELECT count(*) FROM writing_chunk_calls WHERE role <> 'writer'")
    )
    if non_writer:
        raise RuntimeError("cannot downgrade while non-Writer agent call audit rows exist")
    op.drop_index("ix_agent_calls_session_role_started", table_name="writing_chunk_calls")
    op.drop_constraint("ck_agent_calls_writer_ordinal", "writing_chunk_calls", type_="check")
    op.drop_constraint("ck_agent_calls_role", "writing_chunk_calls", type_="check")
    op.drop_constraint("uq_agent_calls_session_role_key", "writing_chunk_calls", type_="unique")
    op.alter_column("writing_chunk_calls", "ordinal", nullable=False)
    op.create_unique_constraint(
        "writing_chunk_calls_session_id_ordinal_key",
        "writing_chunk_calls",
        ["session_id", "ordinal"],
    )
    op.drop_column("writing_chunk_calls", "call_key")
    op.drop_column("writing_chunk_calls", "role")
