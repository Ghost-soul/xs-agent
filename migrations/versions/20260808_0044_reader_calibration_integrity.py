"""tighten reader calibration sample and active rule integrity

Revision ID: 20260808_0044
Revises: 20260806_0043
Create Date: 2026-08-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260808_0044"
down_revision: str | None = "20260806_0043"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "real_reader_notes",
        sa.Column("note_fingerprint_sha256", sa.String(64), nullable=True),
    )
    op.execute(
        "UPDATE real_reader_notes "
        "SET note_fingerprint_sha256 = "
        "lpad(replace(id::text, '-', ''), 64, '0') "
        "WHERE note_fingerprint_sha256 IS NULL"
    )
    op.alter_column(
        "real_reader_notes",
        "note_fingerprint_sha256",
        nullable=False,
    )
    op.create_unique_constraint(
        "uq_real_reader_notes_project_fingerprint",
        "real_reader_notes",
        ["project_id", "note_fingerprint_sha256"],
    )
    op.add_column(
        "reader_calibration_rules",
        sa.Column("rule_payload_sha256", sa.String(64), nullable=True),
    )
    op.create_index(
        "uq_reader_calibration_rules_one_active",
        "reader_calibration_rules",
        ["project_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_reader_calibration_rules_one_active",
        table_name="reader_calibration_rules",
    )
    op.drop_column("reader_calibration_rules", "rule_payload_sha256")
    op.drop_constraint(
        "uq_real_reader_notes_project_fingerprint",
        "real_reader_notes",
        type_="unique",
    )
    op.drop_column("real_reader_notes", "note_fingerprint_sha256")
