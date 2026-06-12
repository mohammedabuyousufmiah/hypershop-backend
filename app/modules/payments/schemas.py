"""Wire schemas (Pydantic v2) for the payments module."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import Field, field_validator

from app.core.validation import StrictModel


# ---------------- Customer-facing ----------------


class InitiatePaymentRequest(StrictModel):
    """Body for ``POST /payments/initiate``.

    The customer has already placed an order with payment_method=online
    and the order is in ``pending_payment``. They now choose which
    online provider to actually pay with.
    """

    order_id: UUID
    provider: str = Field(min_length=1, max_length=32)
    # Optional override URLs the gateway should bounce to. If omitted,
    # service falls back to env defaults (PAYMENT_DEFAULT_*_URL).
    success_url: str | None = Field(default=None, max_length=2048)
    failure_url: str | None = Field(default=None, max_length=2048)
    cancel_url: str | None = Field(default=None, max_length=2048)

    @field_validator("provider")
    @classmethod
    def _provider_lower(cls, v: str) -> str:
        return v.strip().lower()


class InitiatePaymentResponse(StrictModel):
    intent_id: UUID
    provider: str
    status: str
    # The URL the frontend redirects the customer to. Always populated
    # for the redirect-style gateways we support (Bkash + SSLCommerz).
    checkout_url: str
    expires_at: datetime | None = None


class PaymentIntentResponse(StrictModel):
    id: UUID
    order_id: UUID
    provider: str
    provider_payment_id: str | None = None
    status: str
    currency: str
    amount: Decimal
    amount_captured: Decimal
    amount_refunded: Decimal
    checkout_url: str | None = None
    initiated_at: datetime
    authorized_at: datetime | None = None
    captured_at: datetime | None = None
    failed_at: datetime | None = None
    cancelled_at: datetime | None = None
    expires_at: datetime | None = None
    last_error_code: str | None = None
    last_error_message: str | None = None


# ---------------- Admin-facing ----------------


class RefundRequest(StrictModel):
    intent_id: UUID
    amount: Decimal = Field(gt=Decimal("0"))
    reason: str | None = Field(default=None, max_length=512)
    return_request_id: UUID | None = None


class RefundResponse(StrictModel):
    id: UUID
    intent_id: UUID
    amount: Decimal
    currency: str
    status: str
    reason: str | None = None
    provider_refund_id: str | None = None
    succeeded_at: datetime | None = None
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime


class WebhookIngestResponse(StrictModel):
    """What we hand back to the gateway after processing its webhook.

    All gateways accept any 2xx as "delivered" and stop retrying.
    Returning the resolution makes ops triage easier from the gateway
    dashboard.
    """

    received: bool = True
    resolution: str  # 'processed' | 'rejected' | 'duplicate'
    intent_id: UUID | None = None


# ---------------- Settlement reconciliation (admin) ----------------


class SettlementLineIn(StrictModel):
    """One row from a provider's settlement file.

    Operator transforms the bKash / Nagad / SSLCommerz CSV into this
    shape before posting. Provider-agnostic — every gateway publishes
    these three fields somehow.
    """

    provider_ref: str = Field(min_length=1, max_length=128)
    amount: Decimal = Field(gt=Decimal("0"))


class SettlementReconcileRequest(StrictModel):
    """Body for ``POST /admin/payments/reconcile``."""

    provider: str = Field(min_length=1, max_length=32)
    report_date: date
    currency: str = Field(min_length=3, max_length=3)
    lines: list[SettlementLineIn] = Field(min_length=1, max_length=10_000)

    @field_validator("provider")
    @classmethod
    def _provider_lower(cls, v: str) -> str:
        return v.strip().lower()

    @field_validator("currency")
    @classmethod
    def _currency_upper(cls, v: str) -> str:
        return v.strip().upper()


class SettlementMatchResponse(StrictModel):
    id: UUID
    payment_intent_id: UUID | None
    provider_ref: str
    provider_amount: Decimal
    intent_amount: Decimal | None
    drift_amount: Decimal
    status: str  # 'matched' | 'drift' | 'orphan'


class SettlementReportResponse(StrictModel):
    id: UUID
    provider: str
    report_date: date
    currency: str
    submitted_total: Decimal
    submitted_count: int
    status: str  # 'pending' | 'processed' | 'closed'
    matched_count: int
    drift_count: int
    orphan_count: int
    drift_total: Decimal
    processed_at: datetime | None = None
    created_at: datetime


class ReconcileResponse(StrictModel):
    report: SettlementReportResponse
    matches: list[SettlementMatchResponse]
