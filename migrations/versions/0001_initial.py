"""Enable pgvector and create refusal_events.

Revision ID: 0001_initial
Revises:
Create Date: 2026-11-23

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "refusal_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_id", sa.String(128), nullable=False),
        sa.Column("session_id", sa.String(128), nullable=False),
        sa.Column("turn_index", sa.Integer(), nullable=False),
        sa.Column("user_input", sa.Text(), nullable=False),
        sa.Column("refusal_category", sa.String(64), nullable=True),
        sa.Column("refusal_explanation", sa.Text(), nullable=True),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("request_id", sa.String(128), nullable=True),
        sa.Column("reviewed", sa.Boolean(), nullable=False),
        sa.Column("review_note", sa.Text(), nullable=True),
    )
    op.create_index("ix_refusal_events_user_id", "refusal_events", ["user_id"])
    op.create_index("ix_refusal_events_session_id", "refusal_events", ["session_id"])


def downgrade() -> None:
    op.drop_index("ix_refusal_events_session_id", table_name="refusal_events")
    op.drop_index("ix_refusal_events_user_id", table_name="refusal_events")
    op.drop_table("refusal_events")
    # The vector extension is left in place; other objects may depend on it.
