"""0048 cc_sprint5 — parity-tier additions for customer-care.

Adds:
- ``cc_conversations.tags``                 — TEXT[] for free-form tagging
- ``cc_conversations.sla_paused_at``        — when SLA was paused
- ``cc_conversations.sla_paused_reason``    — reason string
- ``cc_saved_replies``                      — agent saved templates (quick-text)
- ``cc_customer_notes``                     — internal agent notes on a customer
- Postgres trigram index on ``cc_messages.message_body`` for ILIKE search
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, UUID

revision: str = "0048_cc_sprint5"
down_revision: str | Sequence[str] | None = "0047_customer_care"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # cc_conversations columns
    op.add_column(
        "cc_conversations",
        sa.Column("tags", ARRAY(sa.String(length=64)), nullable=True),
    )
    op.add_column(
        "cc_conversations",
        sa.Column("sla_paused_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "cc_conversations",
        sa.Column("sla_paused_reason", sa.String(length=500), nullable=True),
    )

    # cc_saved_replies
    op.create_table(
        "cc_saved_replies",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "owner_user_id", UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True, index=True,
        ),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("language", sa.String(length=8), nullable=True),
        sa.Column("is_shared", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("use_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # cc_customer_notes
    op.create_table(
        "cc_customer_notes",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "customer_id", UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False, index=True,
        ),
        sa.Column(
            "author_user_id", UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("pinned", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # Trigram extension + index on cc_messages for fast ILIKE message search.
    # Wrapped in try/except via raw SQL with IF NOT EXISTS so re-running
    # against a pre-warmed env doesn't fail.
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_cc_messages_body_trgm "
        "ON cc_messages USING gin (message_body gin_trgm_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_cc_messages_body_trgm")
    op.drop_table("cc_customer_notes")
    op.drop_table("cc_saved_replies")
    op.drop_column("cc_conversations", "sla_paused_reason")
    op.drop_column("cc_conversations", "sla_paused_at")
    op.drop_column("cc_conversations", "tags")
