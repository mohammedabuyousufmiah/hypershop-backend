"""0047 customer_care — full CC schema folded into Hypershop.

Adds all ``cc_*`` tables. Original CC app shipped 4 migrations
(initial / hardening / rag / external_events); we collapse them
into a single Hypershop alembic revision so a fresh install runs
clean. CC's ``users`` / ``customers`` / ``products`` / ``orders``
tables are NOT created — CC reads from Hypershop's existing ones.

Tables:
- cc_agent_profile             — per-user CC state (PK=user_id)
- cc_customer_profile          — per-customer CC state (PK=customer_id)
- cc_conversations             — agent ↔ customer threads
- cc_messages                  — per-message log
- cc_followups                 — drip campaign tracker
- cc_dead_letters              — failed background job entries
- cc_webhook_idempotency       — dedupe on (channel, message_id)
- cc_csat_surveys              — CSAT scoring
- cc_sla_policies              — first-response + resolution policies
- cc_gdpr_deletion_requests    — privacy-policy deletion queue
- cc_knowledge_documents       — RAG source docs
- cc_knowledge_chunks          — RAG chunks + embeddings
- cc_checkout_events           — external storefront webhook ingest
- cc_payment_events            — external payment gateway webhook ingest
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0047_customer_care"
down_revision: str | Sequence[str] | None = "0046_seller_wallet_ledger"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ---- cc_agent_profile ----
    op.create_table(
        "cc_agent_profile",
        sa.Column(
            "user_id", UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("status", sa.String(30), nullable=False, server_default="offline"),
        sa.Column("max_active_chats", sa.Integer, nullable=False, server_default="300"),
        sa.Column("current_active_chats", sa.Integer, nullable=False, server_default="0"),
        sa.Column("language_skill", sa.String(40), nullable=False, server_default="bangla"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # ---- cc_customer_profile ----
    op.create_table(
        "cc_customer_profile",
        sa.Column(
            "customer_id", UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("preferred_language", sa.String(40), nullable=False, server_default="bangla"),
        sa.Column("consent_status", sa.String(40), nullable=False, server_default="allowed"),
        sa.Column(
            "assigned_agent_id", UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("last_interest", sa.String(255), nullable=True),
        sa.Column("full_address", sa.Text, nullable=True),
        sa.Column("location_link", sa.Text, nullable=True),
        sa.Column("cc_status", sa.String(40), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # ---- cc_conversations ----
    op.create_table(
        "cc_conversations",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "customer_id", UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False, index=True,
        ),
        sa.Column(
            "agent_id", UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True, index=True,
        ),
        sa.Column("channel", sa.String(40), nullable=False, server_default="whatsapp"),
        sa.Column("status", sa.String(40), nullable=False, server_default="open"),
        sa.Column("source", sa.String(40), nullable=False, server_default="whatsapp"),
        sa.Column("last_message", sa.Text, nullable=True),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("handover_required", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("handover_reason", sa.Text, nullable=True),
        sa.Column("priority", sa.String(40), nullable=False, server_default="normal"),
        sa.Column("first_response_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sla_first_response_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sla_resolution_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sla_first_response_breached", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("sla_resolution_breached", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column(
            "order_id", UUID(as_uuid=True),
            sa.ForeignKey("orders.id", ondelete="SET NULL"),
            nullable=True, index=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # ---- cc_messages ----
    op.create_table(
        "cc_messages",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "conversation_id", UUID(as_uuid=True),
            sa.ForeignKey("cc_conversations.id", ondelete="CASCADE"),
            nullable=False, index=True,
        ),
        sa.Column("sender_type", sa.String(40), nullable=False),
        sa.Column("message_type", sa.String(40), nullable=False, server_default="text"),
        sa.Column("message_body", sa.Text, nullable=True),
        sa.Column("media_url", sa.Text, nullable=True),
        sa.Column("channel", sa.String(40), nullable=False, server_default="whatsapp"),
        sa.Column("whatsapp_message_id", sa.String(255), nullable=True, index=True),
        sa.Column("ai_confidence", sa.Numeric(5, 2), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # ---- cc_followups ----
    op.create_table(
        "cc_followups",
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
            "product_id", UUID(as_uuid=True),
            sa.ForeignKey("products.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("campaign_name", sa.String(160), nullable=False),
        sa.Column("stage", sa.Integer, nullable=False, server_default="1"),
        sa.Column("last_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_followup_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(40), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # ---- cc_dead_letters ----
    op.create_table(
        "cc_dead_letters",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("source", sa.String(80), nullable=False, index=True),
        sa.Column("operation", sa.String(120), nullable=False),
        sa.Column("payload", sa.Text, nullable=True),
        sa.Column("error_class", sa.String(120), nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("traceback", sa.Text, nullable=True),
        sa.Column("request_id", sa.String(64), nullable=True),
        sa.Column("status", sa.String(40), nullable=False, server_default="pending", index=True),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_cc_dead_letters_status_created",
        "cc_dead_letters",
        ["status", "created_at"],
    )

    # ---- cc_webhook_idempotency ----
    op.create_table(
        "cc_webhook_idempotency",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("channel", sa.String(40), nullable=False, index=True),
        sa.Column("channel_message_id", sa.String(255), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("channel", "channel_message_id", name="uq_cc_webhook_idem_channel_msgid"),
    )

    # ---- cc_csat_surveys ----
    op.create_table(
        "cc_csat_surveys",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "conversation_id", UUID(as_uuid=True),
            sa.ForeignKey("cc_conversations.id", ondelete="CASCADE"),
            nullable=False, index=True,
        ),
        sa.Column(
            "customer_id", UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False, index=True,
        ),
        sa.Column(
            "agent_id", UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("survey_token", sa.String(64), nullable=False, unique=True, index=True),
        sa.Column("score", sa.Integer, nullable=True),
        sa.Column("comment", sa.Text, nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(40), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # ---- cc_sla_policies ----
    op.create_table(
        "cc_sla_policies",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("priority", sa.String(40), nullable=False, server_default="normal"),
        sa.Column("first_response_minutes", sa.Integer, nullable=False, server_default="15"),
        sa.Column("resolution_minutes", sa.Integer, nullable=False, server_default="240"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # ---- cc_gdpr_deletion_requests ----
    op.create_table(
        "cc_gdpr_deletion_requests",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "customer_id", UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("customer_phone", sa.String(40), nullable=True, index=True),
        sa.Column(
            "requested_by", UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("status", sa.String(40), nullable=False, server_default="pending"),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # ---- cc_knowledge_documents ----
    op.create_table(
        "cc_knowledge_documents",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("source_type", sa.String(40), nullable=False, server_default="text"),
        sa.Column("source_url", sa.Text, nullable=True),
        sa.Column("body", sa.Text, nullable=True),
        sa.Column("body_sha256", sa.String(64), nullable=True, index=True),
        sa.Column("language", sa.String(8), nullable=True),
        sa.Column("chunk_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("embedding_model", sa.String(80), nullable=True),
        sa.Column("embedding_dim", sa.Integer, nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # ---- cc_knowledge_chunks ----
    op.create_table(
        "cc_knowledge_chunks",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "document_id", UUID(as_uuid=True),
            sa.ForeignKey("cc_knowledge_documents.id", ondelete="CASCADE"),
            nullable=False, index=True,
        ),
        sa.Column("position", sa.Integer, nullable=False, server_default="0"),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("text_hash", sa.String(64), nullable=False, index=True),
        sa.Column("token_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("embedding", sa.Text, nullable=True),
        sa.Column("embedding_model", sa.String(80), nullable=True),
        sa.Column("embedding_dim", sa.Integer, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # ---- cc_checkout_events ----
    op.create_table(
        "cc_checkout_events",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("provider", sa.String(40), nullable=False, server_default="storefront", index=True),
        sa.Column("event_id", sa.String(120), nullable=False, index=True),
        sa.Column("event_type", sa.String(40), nullable=False, index=True),
        sa.Column("status", sa.String(40), nullable=True),
        sa.Column("customer_phone", sa.String(40), nullable=True, index=True),
        sa.Column("order_external_id", sa.String(120), nullable=True, index=True),
        sa.Column("raw_payload", sa.Text, nullable=True),
        sa.Column("processed", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("provider", "event_id", name="uq_cc_checkout_event_provider_eventid"),
    )

    # ---- cc_payment_events ----
    op.create_table(
        "cc_payment_events",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("provider", sa.String(40), nullable=False, server_default="generic", index=True),
        sa.Column("event_id", sa.String(120), nullable=False, index=True),
        sa.Column("event_type", sa.String(40), nullable=False, index=True),
        sa.Column("status", sa.String(40), nullable=True),
        sa.Column("order_external_id", sa.String(120), nullable=True, index=True),
        sa.Column("customer_phone", sa.String(40), nullable=True, index=True),
        sa.Column("amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("currency", sa.String(8), nullable=True),
        sa.Column("raw_payload", sa.Text, nullable=True),
        sa.Column("processed", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("provider", "event_id", name="uq_cc_payment_event_provider_eventid"),
    )


def downgrade() -> None:
    for tbl in (
        "cc_payment_events",
        "cc_checkout_events",
        "cc_knowledge_chunks",
        "cc_knowledge_documents",
        "cc_gdpr_deletion_requests",
        "cc_sla_policies",
        "cc_csat_surveys",
        "cc_webhook_idempotency",
        "cc_dead_letters",
        "cc_followups",
        "cc_messages",
        "cc_conversations",
        "cc_customer_profile",
        "cc_agent_profile",
    ):
        op.drop_table(tbl)
