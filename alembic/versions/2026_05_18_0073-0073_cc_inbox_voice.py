"""0073 cc_inbox_voice — WhatsApp inbox + voice-call sessions + CSAT surveys.

Four new tables (all prefixed ``hypershop_``):
  hypershop_cc_threads             — WhatsApp / email / sms conversation header
  hypershop_cc_messages            — messages inside a thread (BIGINT id)
  hypershop_voice_call_sessions    — SIM-gateway / Twilio inbound/outbound calls
  hypershop_cc_csat_surveys        — post-interaction satisfaction surveys
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID

revision: str = "0073_cc_inbox_voice"
down_revision: str | Sequence[str] | None = "0072_disputes_module"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ─── hypershop_cc_threads ─────────────────────────────────────
    op.create_table(
        "hypershop_cc_threads",
        sa.Column(
            "id",
            PgUUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("channel_thread_id", sa.String(128), nullable=False),
        sa.Column("customer_user_id", PgUUID(as_uuid=True), nullable=True),
        sa.Column("customer_phone", sa.String(32), nullable=True),
        sa.Column("customer_email", sa.String(256), nullable=True),
        sa.Column("customer_name", sa.String(120), nullable=True),
        sa.Column("subject", sa.String(256), nullable=True),
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'open'"),
        ),
        sa.Column("assigned_agent_id", PgUUID(as_uuid=True), nullable=True),
        sa.Column(
            "priority",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'normal'"),
        ),
        sa.Column("ai_confidence", sa.Numeric(3, 2), nullable=True),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_inbound_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "last_agent_response_at", sa.DateTime(timezone=True), nullable=True,
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "tags",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.CheckConstraint(
            "channel IN ('whatsapp','email','sms','live_chat')",
            name="ck_hypershop_cc_threads_channel",
        ),
        sa.CheckConstraint(
            "status IN ('open','assigned','awaiting_customer',"
            "'resolved','closed','spam')",
            name="ck_hypershop_cc_threads_status",
        ),
        sa.CheckConstraint(
            "priority IN ('low','normal','high','urgent')",
            name="ck_hypershop_cc_threads_priority",
        ),
        sa.UniqueConstraint(
            "channel", "channel_thread_id",
            name="uq_hypershop_cc_threads_channel_chid",
        ),
    )
    op.create_index(
        "ix_hypershop_cc_threads_open_at",
        "hypershop_cc_threads",
        ["status", sa.text("last_message_at DESC")],
        postgresql_where=sa.text(
            "status IN ('open','assigned','awaiting_customer')",
        ),
    )
    op.create_index(
        "ix_hypershop_cc_threads_agent_status_at",
        "hypershop_cc_threads",
        ["assigned_agent_id", "status", sa.text("last_message_at DESC")],
    )
    op.create_index(
        "ix_hypershop_cc_threads_customer_at",
        "hypershop_cc_threads",
        ["customer_user_id", sa.text("created_at DESC")],
        postgresql_where=sa.text("customer_user_id IS NOT NULL"),
    )

    # ─── hypershop_cc_messages ────────────────────────────────────
    op.create_table(
        "hypershop_cc_messages",
        sa.Column(
            "id", sa.BigInteger(), primary_key=True, autoincrement=True,
        ),
        sa.Column(
            "thread_id",
            PgUUID(as_uuid=True),
            sa.ForeignKey("hypershop_cc_threads.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("direction", sa.String(8), nullable=False),
        sa.Column("author_kind", sa.String(16), nullable=False),
        sa.Column("author_user_id", PgUUID(as_uuid=True), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("channel_message_id", sa.String(128), nullable=True),
        sa.Column(
            "attachments",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("ai_meta", JSONB, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.CheckConstraint(
            "direction IN ('inbound','outbound')",
            name="ck_hypershop_cc_messages_direction",
        ),
        sa.CheckConstraint(
            "author_kind IN ('customer','agent','ai','system')",
            name="ck_hypershop_cc_messages_author_kind",
        ),
    )
    op.create_index(
        "ix_hypershop_cc_messages_thread_at",
        "hypershop_cc_messages",
        ["thread_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_hypershop_cc_messages_channel_mid",
        "hypershop_cc_messages",
        ["channel_message_id"],
        postgresql_where=sa.text("channel_message_id IS NOT NULL"),
    )

    # ─── hypershop_voice_call_sessions ────────────────────────────
    op.create_table(
        "hypershop_voice_call_sessions",
        sa.Column(
            "id",
            PgUUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "channel_call_id", sa.String(128), nullable=False, unique=True,
        ),
        sa.Column("direction", sa.String(8), nullable=False),
        sa.Column("caller_phone", sa.String(32), nullable=False),
        sa.Column("callee_phone", sa.String(32), nullable=True),
        sa.Column("customer_user_id", PgUUID(as_uuid=True), nullable=True),
        sa.Column("assigned_agent_id", PgUUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("recording_url", sa.Text(), nullable=True),
        sa.Column("transcript", sa.Text(), nullable=True),
        sa.Column("transcript_lang", sa.String(5), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("next_action", sa.String(64), nullable=True),
        sa.Column("next_action_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("csat_score", sa.SmallInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.CheckConstraint(
            "direction IN ('inbound','outbound')",
            name="ck_hypershop_voice_call_direction",
        ),
        sa.CheckConstraint(
            "status IN ('ringing','live','wrap_up','completed',"
            "'missed','failed','voicemail')",
            name="ck_hypershop_voice_call_status",
        ),
        sa.CheckConstraint(
            "csat_score IS NULL OR (csat_score BETWEEN 1 AND 5)",
            name="ck_hypershop_voice_call_csat_range",
        ),
    )
    op.create_index(
        "ix_hypershop_voice_call_status_at",
        "hypershop_voice_call_sessions",
        ["status", sa.text("started_at DESC")],
    )
    op.create_index(
        "ix_hypershop_voice_call_agent_status_at",
        "hypershop_voice_call_sessions",
        ["assigned_agent_id", "status", sa.text("started_at DESC")],
    )
    op.create_index(
        "ix_hypershop_voice_call_customer_at",
        "hypershop_voice_call_sessions",
        ["customer_user_id", sa.text("started_at DESC")],
        postgresql_where=sa.text("customer_user_id IS NOT NULL"),
    )

    # ─── hypershop_cc_csat_surveys ────────────────────────────────
    op.create_table(
        "hypershop_cc_csat_surveys",
        sa.Column(
            "id",
            PgUUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "thread_id",
            PgUUID(as_uuid=True),
            sa.ForeignKey("hypershop_cc_threads.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "voice_call_session_id",
            PgUUID(as_uuid=True),
            sa.ForeignKey(
                "hypershop_voice_call_sessions.id", ondelete="SET NULL",
            ),
            nullable=True,
        ),
        sa.Column("customer_user_id", PgUUID(as_uuid=True), nullable=True),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("score", sa.SmallInteger(), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.CheckConstraint(
            "score IS NULL OR (score BETWEEN 1 AND 5)",
            name="ck_hypershop_cc_csat_score_range",
        ),
        sa.CheckConstraint(
            "status IN ('pending','sent','responded','expired')",
            name="ck_hypershop_cc_csat_status",
        ),
        sa.CheckConstraint(
            "thread_id IS NOT NULL OR voice_call_session_id IS NOT NULL",
            name="ck_hypershop_cc_csat_parent_present",
        ),
    )
    op.create_index(
        "ix_hypershop_cc_csat_pending_at",
        "hypershop_cc_csat_surveys",
        ["status", "sent_at"],
        postgresql_where=sa.text("status IN ('pending','sent')"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_hypershop_cc_csat_pending_at",
        table_name="hypershop_cc_csat_surveys",
    )
    op.drop_table("hypershop_cc_csat_surveys")

    op.drop_index(
        "ix_hypershop_voice_call_customer_at",
        table_name="hypershop_voice_call_sessions",
    )
    op.drop_index(
        "ix_hypershop_voice_call_agent_status_at",
        table_name="hypershop_voice_call_sessions",
    )
    op.drop_index(
        "ix_hypershop_voice_call_status_at",
        table_name="hypershop_voice_call_sessions",
    )
    op.drop_table("hypershop_voice_call_sessions")

    op.drop_index(
        "ix_hypershop_cc_messages_channel_mid",
        table_name="hypershop_cc_messages",
    )
    op.drop_index(
        "ix_hypershop_cc_messages_thread_at",
        table_name="hypershop_cc_messages",
    )
    op.drop_table("hypershop_cc_messages")

    op.drop_index(
        "ix_hypershop_cc_threads_customer_at",
        table_name="hypershop_cc_threads",
    )
    op.drop_index(
        "ix_hypershop_cc_threads_agent_status_at",
        table_name="hypershop_cc_threads",
    )
    op.drop_index(
        "ix_hypershop_cc_threads_open_at",
        table_name="hypershop_cc_threads",
    )
    op.drop_table("hypershop_cc_threads")
