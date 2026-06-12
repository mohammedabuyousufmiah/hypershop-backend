"""ORM models for CC inbox + voice call + CSAT survey (migration 0073).

Kept separate from ``models.py`` (Module 47 legacy CC tables) so the
new inbox / voice surface stays grep-able as a unit.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db.base import Base


class CCThread(Base):
    __tablename__ = "hypershop_cc_threads"

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    channel_thread_id: Mapped[str] = mapped_column(String(128), nullable=False)
    customer_user_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), nullable=True,
    )
    customer_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    customer_email: Mapped[str | None] = mapped_column(String(256), nullable=True)
    customer_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    subject: Mapped[str | None] = mapped_column(String(256), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'open'"),
    )
    assigned_agent_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), nullable=True,
    )
    priority: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'normal'"),
    )
    ai_confidence: Mapped[Decimal | None] = mapped_column(
        Numeric(3, 2), nullable=True,
    )
    last_message_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    last_inbound_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    last_agent_response_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    tags: Mapped[Any] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("(now() AT TIME ZONE 'UTC')"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("(now() AT TIME ZONE 'UTC')"),
    )

    __table_args__ = (
        CheckConstraint(
            "channel IN ('whatsapp','email','sms','live_chat')",
            name="ck_hypershop_cc_threads_channel",
        ),
        CheckConstraint(
            "status IN ('open','assigned','awaiting_customer',"
            "'resolved','closed','spam')",
            name="ck_hypershop_cc_threads_status",
        ),
        CheckConstraint(
            "priority IN ('low','normal','high','urgent')",
            name="ck_hypershop_cc_threads_priority",
        ),
        UniqueConstraint(
            "channel", "channel_thread_id",
            name="uq_hypershop_cc_threads_channel_chid",
        ),
        Index(
            "ix_hypershop_cc_threads_open_at",
            "status", text("last_message_at DESC"),
            postgresql_where=text(
                "status IN ('open','assigned','awaiting_customer')",
            ),
        ),
        Index(
            "ix_hypershop_cc_threads_agent_status_at",
            "assigned_agent_id", "status", text("last_message_at DESC"),
        ),
        Index(
            "ix_hypershop_cc_threads_customer_at",
            "customer_user_id", text("created_at DESC"),
            postgresql_where=text("customer_user_id IS NOT NULL"),
        ),
    )


class CCMessage(Base):
    __tablename__ = "hypershop_cc_messages"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True,
    )
    thread_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("hypershop_cc_threads.id", ondelete="CASCADE"),
        nullable=False,
    )
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    author_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    author_user_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), nullable=True,
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    channel_message_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True,
    )
    attachments: Mapped[Any] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb"),
    )
    ai_meta: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("(now() AT TIME ZONE 'UTC')"),
    )

    __table_args__ = (
        CheckConstraint(
            "direction IN ('inbound','outbound')",
            name="ck_hypershop_cc_messages_direction",
        ),
        CheckConstraint(
            "author_kind IN ('customer','agent','ai','system')",
            name="ck_hypershop_cc_messages_author_kind",
        ),
        Index(
            "ix_hypershop_cc_messages_thread_at",
            "thread_id", text("created_at DESC"),
        ),
        Index(
            "ix_hypershop_cc_messages_channel_mid",
            "channel_message_id",
            postgresql_where=text("channel_message_id IS NOT NULL"),
        ),
    )


class VoiceCallSession(Base):
    __tablename__ = "hypershop_voice_call_sessions"

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    channel_call_id: Mapped[str] = mapped_column(
        String(128), nullable=False, unique=True,
    )
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    caller_phone: Mapped[str] = mapped_column(String(32), nullable=False)
    callee_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    customer_user_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), nullable=True,
    )
    assigned_agent_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), nullable=True,
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
    answered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    recording_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    transcript: Mapped[str | None] = mapped_column(Text, nullable=True)
    transcript_lang: Mapped[str | None] = mapped_column(String(5), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_action: Mapped[str | None] = mapped_column(String(64), nullable=True)
    next_action_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    csat_score: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("(now() AT TIME ZONE 'UTC')"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("(now() AT TIME ZONE 'UTC')"),
    )

    __table_args__ = (
        CheckConstraint(
            "direction IN ('inbound','outbound')",
            name="ck_hypershop_voice_call_direction",
        ),
        CheckConstraint(
            "status IN ('ringing','live','wrap_up','completed',"
            "'missed','failed','voicemail')",
            name="ck_hypershop_voice_call_status",
        ),
        CheckConstraint(
            "csat_score IS NULL OR (csat_score BETWEEN 1 AND 5)",
            name="ck_hypershop_voice_call_csat_range",
        ),
        Index(
            "ix_hypershop_voice_call_status_at",
            "status", text("started_at DESC"),
        ),
        Index(
            "ix_hypershop_voice_call_agent_status_at",
            "assigned_agent_id", "status", text("started_at DESC"),
        ),
        Index(
            "ix_hypershop_voice_call_customer_at",
            "customer_user_id", text("started_at DESC"),
            postgresql_where=text("customer_user_id IS NOT NULL"),
        ),
    )


class CSATSurvey(Base):
    __tablename__ = "hypershop_cc_csat_surveys"

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    thread_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("hypershop_cc_threads.id", ondelete="SET NULL"),
        nullable=True,
    )
    voice_call_session_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey(
            "hypershop_voice_call_sessions.id", ondelete="SET NULL",
        ),
        nullable=True,
    )
    customer_user_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), nullable=True,
    )
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    responded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    score: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'pending'"),
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("(now() AT TIME ZONE 'UTC')"),
    )

    __table_args__ = (
        CheckConstraint(
            "score IS NULL OR (score BETWEEN 1 AND 5)",
            name="ck_hypershop_cc_csat_score_range",
        ),
        CheckConstraint(
            "status IN ('pending','sent','responded','expired')",
            name="ck_hypershop_cc_csat_status",
        ),
        CheckConstraint(
            "thread_id IS NOT NULL OR voice_call_session_id IS NOT NULL",
            name="ck_hypershop_cc_csat_parent_present",
        ),
        Index(
            "ix_hypershop_cc_csat_pending_at",
            "status", "sent_at",
            postgresql_where=text("status IN ('pending','sent')"),
        ),
    )


__all__ = ["CCThread", "CCMessage", "VoiceCallSession", "CSATSurvey"]
