"""ORM models for the payments module.

Tables:
  - payment_intents:  one per (order, attempt-of-payment); a customer
                      retrying after a failure gets a fresh intent.
  - payment_attempts: per-call audit trail to a gateway (create/execute/
                      query). Lets ops debug "what did we send and get
                      back" without spelunking gateway logs.
  - payment_webhooks: raw webhook ingestion log — verbatim body + headers
                      kept for replay + reconciliation.
  - payment_refunds:  refund requests against captured intents, one row
                      per refund (partial refunds = multiple rows).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db.base import Base
from app.core.db.mixins import TimestampMixin

if TYPE_CHECKING:
    pass


class PaymentIntent(Base, TimestampMixin):
    __tablename__ = "payment_intents"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    order_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="RESTRICT"),
        nullable=False,
    )
    customer_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    # Provider name (bkash | sslcommerz | ...). Routes refund + webhook
    # back to the same adapter that originated the intent.
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    # Provider's own ID for this intent (e.g. Bkash paymentID, SSLCommerz
    # sessionkey). Indexed UNIQUE per provider so webhooks can resolve
    # this intent without scanning.
    provider_payment_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)

    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    amount_captured: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, server_default=text("0"),
    )
    amount_refunded: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, server_default=text("0"),
    )

    # Where the customer is sent to complete the payment (redirect URL
    # for SSLCommerz; checkout URL for Bkash) or null for non-redirect
    # flows. Carried back in the customer-facing response.
    checkout_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)

    # Where the gateway should bounce the customer after success/failure.
    # Set per-intent so we can vary per environment (dev vs prod).
    success_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    failure_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    cancel_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)

    # Provider-specific blob (transaction metadata, raw last response, etc.)
    metadata_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Set when the gateway confirms terminal capture / failure. Kept
    # alongside `status` for fast "show me payments authorized in the
    # last hour but never captured" queries.
    initiated_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=text("(now() AT TIME ZONE 'UTC')"),
    )
    authorized_at: Mapped[datetime | None] = mapped_column(nullable=True)
    captured_at: Mapped[datetime | None] = mapped_column(nullable=True)
    failed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(nullable=True)
    # Optional gateway-supplied expiry (e.g. Bkash sessions expire ~30 min).
    expires_at: Mapped[datetime | None] = mapped_column(nullable=True)

    # Last error surface (for ops triage; do NOT include card numbers etc.).
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_error_message: Mapped[str | None] = mapped_column(
        String(2048), nullable=True,
    )

    attempts: Mapped[list[PaymentAttempt]] = relationship(
        "PaymentAttempt",
        back_populates="intent",
        cascade="all, delete-orphan",
        order_by="PaymentAttempt.created_at",
        lazy="selectin",
    )
    refunds: Mapped[list[PaymentRefund]] = relationship(
        "PaymentRefund",
        back_populates="intent",
        cascade="all, delete-orphan",
        order_by="PaymentRefund.created_at",
        lazy="selectin",
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('initiated','authorized','captured','failed',"
            "'cancelled','expired','refunded','partially_refunded')",
            name="ck_payment_intents_status_enum",
        ),
        CheckConstraint(
            "currency = upper(currency) AND length(currency) = 3",
            name="ck_payment_intents_currency_iso",
        ),
        CheckConstraint("amount > 0", name="ck_payment_intents_amount_pos"),
        CheckConstraint(
            "amount_captured >= 0 AND amount_captured <= amount",
            name="ck_payment_intents_captured_bounds",
        ),
        CheckConstraint(
            "amount_refunded >= 0 AND amount_refunded <= amount_captured",
            name="ck_payment_intents_refund_bounds",
        ),
        UniqueConstraint(
            "provider", "provider_payment_id",
            name="uq_payment_intents_provider_payment_id",
        ),
        Index("ix_payment_intents_order", "order_id"),
        Index("ix_payment_intents_customer_status", "customer_user_id", "status"),
        Index("ix_payment_intents_status_initiated_at", "status", "initiated_at"),
    )


class PaymentAttempt(Base, TimestampMixin):
    """One row per gateway HTTP call. The service writes this BEFORE
    awaiting the provider response (status='pending'), then updates to
    ``ok``/``failed``/``timeout`` after the call returns. This way an
    intent's history is preserved even if the worker dies mid-call.
    """

    __tablename__ = "payment_attempts"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    intent_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("payment_intents.id", ondelete="CASCADE"),
        nullable=False,
    )
    # 'create' | 'execute' | 'query' | 'refund'  — provider-agnostic verb.
    operation: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    http_status: Mapped[int | None] = mapped_column(nullable=True)
    request_summary: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    response_summary: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(nullable=True)

    intent: Mapped[PaymentIntent] = relationship(
        "PaymentIntent", back_populates="attempts",
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('ok','failed','timeout','pending')",
            name="ck_payment_attempts_status_enum",
        ),
        Index("ix_payment_attempts_intent_created", "intent_id", "created_at"),
    )


class PaymentWebhook(Base, TimestampMixin):
    """Verbatim webhook ingestion log. Appended once per inbound request
    BEFORE we trust the body — signature verification happens during
    processing, not at receipt time. Status reflects whether processing
    succeeded.
    """

    __tablename__ = "payment_webhooks"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    # Provider's webhook event ID if any (used for idempotent dedup).
    # Falls back to a hash of the body when the provider doesn't supply one.
    event_id: Mapped[str] = mapped_column(String(128), nullable=False)
    intent_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("payment_intents.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    # Raw HTTP request — body parsed-as-JSON when possible, else stored
    # under {"raw": "<base64>"}; headers as {name: value}. Kept for replay.
    raw_body: Mapped[dict] = mapped_column(JSONB, nullable=False)
    raw_headers: Mapped[dict] = mapped_column(JSONB, nullable=False)
    # Why we rejected/duplicated, if applicable.
    rejection_reason: Mapped[str | None] = mapped_column(String(512), nullable=True)
    received_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=text("(now() AT TIME ZONE 'UTC')"),
    )
    processed_at: Mapped[datetime | None] = mapped_column(nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('received','processed','rejected','duplicate')",
            name="ck_payment_webhooks_status_enum",
        ),
        UniqueConstraint(
            "provider", "event_id",
            name="uq_payment_webhooks_provider_event_id",
        ),
        Index("ix_payment_webhooks_intent", "intent_id"),
        Index(
            "ix_payment_webhooks_status_received",
            "status", "received_at",
        ),
    )


class PaymentRefund(Base, TimestampMixin):
    """Refund operation against a captured intent. Multiple rows allowed
    when refunds happen in tranches (partial refunds → returns module
    issues one per accepted return line).
    """

    __tablename__ = "payment_refunds"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    intent_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("payment_intents.id", ondelete="RESTRICT"),
        nullable=False,
    )
    # Optional link back to the originating return request. Null when an
    # admin issued a goodwill refund outside the returns flow.
    return_request_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("return_requests.id", ondelete="SET NULL"),
        nullable=True,
    )
    requested_by_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # Provider-side refund ID (Bkash refundTrxID, SSLCommerz refund_ref).
    provider_refund_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True,
    )
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    succeeded_at: Mapped[datetime | None] = mapped_column(nullable=True)

    intent: Mapped[PaymentIntent] = relationship(
        "PaymentIntent", back_populates="refunds",
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('requested','succeeded','failed')",
            name="ck_payment_refunds_status_enum",
        ),
        CheckConstraint(
            "currency = upper(currency) AND length(currency) = 3",
            name="ck_payment_refunds_currency_iso",
        ),
        CheckConstraint("amount > 0", name="ck_payment_refunds_amount_pos"),
        Index("ix_payment_refunds_intent", "intent_id"),
        Index("ix_payment_refunds_status_created", "status", "created_at"),
    )


# ---------------------------------------------------------------------------
# Settlement reconciliation (added 2026-05-16)
# ---------------------------------------------------------------------------
# When the gateway (bKash / Nagad / SSLCommerz / Visa Acquirer) closes a
# business day they publish a settlement report listing every txn they
# moved into our merchant bank account. The operator uploads those rows
# here; ``SettlementReconciliationService`` matches each line against a
# ``PaymentIntent`` by ``provider_payment_id``, flags drift > 1 unit of
# the report currency, and saves a per-line ``SettlementMatch`` row.
#
# Drift summary lives on the parent ``SettlementReport`` row. A downstream
# finance worker picks up the ``payment.reconciliation.completed`` outbox
# event and posts the drift summary as a finance ``JournalEntry`` — that
# split keeps the payments module from coupling to finance internals.


class SettlementReport(Base, TimestampMixin):
    """One row per (provider, business date). Header for a batch of
    settlement lines uploaded by ops."""

    __tablename__ = "payment_settlement_reports"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    report_date: Mapped[date] = mapped_column(Date, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)

    # Totals from the operator-submitted file. Stored separately from the
    # sum of matches so we can detect if rows were dropped/added during
    # parsing.
    submitted_total: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False,
    )
    submitted_count: Mapped[int] = mapped_column(Integer, nullable=False)

    # Computed during reconcile().
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'pending'"),
    )
    matched_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"),
    )
    drift_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"),
    )
    orphan_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"),
    )
    drift_total: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, server_default=text("0"),
    )

    processed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    processed_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    matches: Mapped[list[SettlementMatch]] = relationship(
        "SettlementMatch",
        back_populates="report",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    __table_args__ = (
        UniqueConstraint(
            "provider", "report_date",
            name="uq_payment_settlement_reports_provider_date",
        ),
        CheckConstraint(
            "status IN ('pending','processed','closed')",
            name="ck_payment_settlement_reports_status_enum",
        ),
        CheckConstraint(
            "currency = upper(currency) AND length(currency) = 3",
            name="ck_payment_settlement_reports_currency_iso",
        ),
        CheckConstraint(
            "submitted_count >= 0",
            name="ck_payment_settlement_reports_count_nonneg",
        ),
        Index("ix_payment_settlement_reports_date", "report_date"),
        Index(
            "ix_payment_settlement_reports_status",
            "status", "report_date",
        ),
    )


class SettlementMatch(Base):
    """One row per settlement line. ``payment_intent_id`` is NULL when
    the provider sent us a ref we can't find — an ``orphan``."""

    __tablename__ = "payment_settlement_matches"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    settlement_report_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("payment_settlement_reports.id", ondelete="CASCADE"),
        nullable=False,
    )
    payment_intent_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("payment_intents.id", ondelete="RESTRICT"),
        nullable=True,
    )
    provider_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    provider_amount: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False,
    )
    intent_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(14, 2), nullable=True,
    )
    drift_amount: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, server_default=text("0"),
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=text("(now() AT TIME ZONE 'UTC')"),
    )

    report: Mapped[SettlementReport] = relationship(
        "SettlementReport", back_populates="matches",
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('matched','drift','orphan')",
            name="ck_payment_settlement_matches_status_enum",
        ),
        # A given (report, provider_ref) is unique — the same ref can't
        # appear twice in a settlement file.
        UniqueConstraint(
            "settlement_report_id", "provider_ref",
            name="uq_payment_settlement_matches_report_ref",
        ),
        Index(
            "ix_payment_settlement_matches_report_status",
            "settlement_report_id", "status",
        ),
        Index(
            "ix_payment_settlement_matches_intent",
            "payment_intent_id",
        ),
    )
