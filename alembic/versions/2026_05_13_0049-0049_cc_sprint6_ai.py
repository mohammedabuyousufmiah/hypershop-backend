"""0049 cc_sprint6_ai — AI-native enrichment columns.

Adds:
- ``cc_messages.sentiment``           — positive/neutral/negative/null
- ``cc_messages.sentiment_score``     — -1.0 … +1.0
- ``cc_messages.intent_tag``          — refund/shipping/pre_sales/complaint/other
- ``cc_messages.translated_body``     — translated text (TEXT)
- ``cc_messages.translated_language`` — code (e.g. "en", "bn")
- ``cc_messages.rag_citation_ids``    — UUID[] of cc_knowledge_chunks
- ``cc_conversations.ai_summary``     — TEXT, populated on resolve
- ``cc_conversations.summary_generated_at`` — timestamp
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, UUID

revision: str = "0049_cc_sprint6_ai"
down_revision: str | Sequence[str] | None = "0048_cc_sprint5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("cc_messages", sa.Column("sentiment", sa.String(length=16), nullable=True))
    op.add_column("cc_messages", sa.Column("sentiment_score", sa.Numeric(4, 3), nullable=True))
    op.add_column("cc_messages", sa.Column("intent_tag", sa.String(length=40), nullable=True))
    op.add_column("cc_messages", sa.Column("translated_body", sa.Text, nullable=True))
    op.add_column("cc_messages", sa.Column("translated_language", sa.String(length=8), nullable=True))
    op.add_column(
        "cc_messages",
        sa.Column("rag_citation_ids", ARRAY(UUID(as_uuid=True)), nullable=True),
    )
    op.add_column("cc_conversations", sa.Column("ai_summary", sa.Text, nullable=True))
    op.add_column(
        "cc_conversations",
        sa.Column("summary_generated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_cc_messages_intent_tag", "cc_messages", ["intent_tag"],
        postgresql_where=sa.text("intent_tag IS NOT NULL"),
    )
    op.create_index(
        "ix_cc_messages_sentiment", "cc_messages", ["sentiment"],
        postgresql_where=sa.text("sentiment IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_cc_messages_sentiment", table_name="cc_messages")
    op.drop_index("ix_cc_messages_intent_tag", table_name="cc_messages")
    op.drop_column("cc_conversations", "summary_generated_at")
    op.drop_column("cc_conversations", "ai_summary")
    op.drop_column("cc_messages", "rag_citation_ids")
    op.drop_column("cc_messages", "translated_language")
    op.drop_column("cc_messages", "translated_body")
    op.drop_column("cc_messages", "intent_tag")
    op.drop_column("cc_messages", "sentiment_score")
    op.drop_column("cc_messages", "sentiment")
