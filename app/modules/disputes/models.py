"""ORM mappings for disputes module — 4 tables (hypershop_disputes,
hypershop_dispute_messages, hypershop_dispute_evidence, hypershop_escrow_holds).
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db.base import Base


class HypershopDispute(Base):
    __tablename__ = "hypershop_disputes"

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    order_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    order_item_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), nullable=True,
    )
    opened_by_user_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), nullable=False,
    )
    seller_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    dispute_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    resolution: Mapped[str | None] = mapped_column(String(32), nullable=True)
    amount_disputed_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    amount_refunded_minor: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0"),
    )
    subject: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    mediator_user_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), nullable=True,
    )
    decision_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("(now() AT TIME ZONE 'UTC')"),
    )
    last_response_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
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
            "dispute_type IN ('wrong_item','damaged','not_received','quality_issue',"
            "'fake_item','billing_error','other')",
            name="ck_hypershop_disputes_type",
        ),
        CheckConstraint(
            "status IN ('open','awaiting_seller','awaiting_buyer','under_review',"
            "'resolved','closed')",
            name="ck_hypershop_disputes_status",
        ),
        CheckConstraint(
            "resolution IS NULL OR resolution IN ('refund_full','refund_partial',"
            "'replace','decline','customer_withdrew')",
            name="ck_hypershop_disputes_resolution",
        ),
        CheckConstraint(
            "amount_disputed_minor >= 0",
            name="ck_hypershop_disputes_amount_disputed_nonneg",
        ),
        CheckConstraint(
            "amount_refunded_minor >= 0",
            name="ck_hypershop_disputes_amount_refunded_nonneg",
        ),
        Index(
            "ix_hypershop_disputes_seller_status_at",
            "seller_id", "status", text("created_at DESC"),
        ),
        Index(
            "ix_hypershop_disputes_buyer_at",
            "opened_by_user_id", text("created_at DESC"),
        ),
        Index(
            "ix_hypershop_disputes_awaiting_response",
            "status", "last_response_at",
            postgresql_where=text(
                "status IN ('awaiting_seller','awaiting_buyer')",
            ),
        ),
        Index("ix_hypershop_disputes_order_id", "order_id"),
    )


class HypershopDisputeMessage(Base):
    __tablename__ = "hypershop_dispute_messages"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True,
    )
    dispute_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("hypershop_disputes.id", ondelete="CASCADE"),
        nullable=False,
    )
    author_user_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), nullable=True,
    )
    author_role: Mapped[str] = mapped_column(String(16), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    attachments: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb"),
    )
    is_system: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("(now() AT TIME ZONE 'UTC')"),
    )

    __table_args__ = (
        CheckConstraint(
            "author_role IN ('buyer','seller','mediator','system')",
            name="ck_hypershop_dispute_messages_role",
        ),
        Index(
            "ix_hypershop_dispute_messages_dispute_at",
            "dispute_id", text("created_at DESC"),
        ),
    )


class HypershopDisputeEvidence(Base):
    __tablename__ = "hypershop_dispute_evidence"

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    dispute_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("hypershop_disputes.id", ondelete="CASCADE"),
        nullable=False,
    )
    uploaded_by_user_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), nullable=False,
    )
    uploader_role: Mapped[str] = mapped_column(String(16), nullable=False)
    file_url: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    description: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("(now() AT TIME ZONE 'UTC')"),
    )

    __table_args__ = (
        CheckConstraint(
            "uploader_role IN ('buyer','seller','mediator')",
            name="ck_hypershop_dispute_evidence_role",
        ),
        CheckConstraint(
            "size_bytes > 0",
            name="ck_hypershop_dispute_evidence_size_pos",
        ),
        Index(
            "ix_hypershop_dispute_evidence_dispute_at",
            "dispute_id", text("created_at DESC"),
        ),
    )


class HypershopEscrowHold(Base):
    __tablename__ = "hypershop_escrow_holds"

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    dispute_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("hypershop_disputes.id", ondelete="CASCADE"),
        nullable=False,
    )
    seller_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    order_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    held_amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'active'"),
    )
    released_to_buyer_minor: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0"),
    )
    released_to_seller_minor: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0"),
    )
    release_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("(now() AT TIME ZONE 'UTC')"),
    )
    released_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    __table_args__ = (
        UniqueConstraint(
            "dispute_id", name="uq_hypershop_escrow_holds_dispute",
        ),
        CheckConstraint(
            "held_amount_minor >= 0",
            name="ck_hypershop_escrow_holds_held_nonneg",
        ),
        CheckConstraint(
            "status IN ('active','released_to_buyer','released_to_seller',"
            "'split','cancelled')",
            name="ck_hypershop_escrow_holds_status",
        ),
        CheckConstraint(
            "released_to_buyer_minor + released_to_seller_minor "
            "<= held_amount_minor",
            name="ck_hypershop_escrow_holds_release_sum",
        ),
        Index(
            "ix_hypershop_escrow_holds_seller_status",
            "seller_id", "status",
        ),
        Index(
            "ix_hypershop_escrow_holds_active_at",
            "status", "created_at",
            postgresql_where=text("status = 'active'"),
        ),
    )


__all__ = [
    "HypershopDispute",
    "HypershopDisputeMessage",
    "HypershopDisputeEvidence",
    "HypershopEscrowHold",
]
