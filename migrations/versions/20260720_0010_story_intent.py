"""Add optional author guidance and autonomous story intent."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260720_0010"
down_revision = "20260720_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "writing_sessions",
        sa.Column(
            "author_guidance",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "writing_sessions",
        sa.Column("story_intent", postgresql.JSONB(), nullable=True),
    )
    op.execute(
        """
        UPDATE writing_sessions
        SET author_guidance = jsonb_build_object(
            'preferences', CASE WHEN direction = '' THEN '[]'::jsonb
                                ELSE jsonb_build_array(direction) END,
            'constraints', to_jsonb(avoid),
            'suggestions', to_jsonb(must_include)
        )
        """
    )
    op.alter_column("writing_sessions", "author_guidance", server_default=None)


def downgrade() -> None:
    op.drop_column("writing_sessions", "story_intent")
    op.drop_column("writing_sessions", "author_guidance")
