"""Abstract :class:`PaymentProvider` port + DTOs.

Every gateway adapter implements this interface. The service layer
calls the bound provider via ``providers.get_provider()`` and never
touches adapter-specific logic.

The DTOs are deliberately gateway-agnostic — they map to fields the
service writes back into ``payment_intents`` regardless of which
gateway responded.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True)
class CreatePaymentRequest:
    intent_id: str          # our internal payment_intents.id (string)
    order_code: str         # our human order code (HSO-...) for gateway display
    amount: Decimal
    currency: str           # ISO 4217 (BDT, USD, ...)
    customer_email: str | None
    customer_phone: str | None
    customer_name: str | None
    success_url: str
    failure_url: str
    cancel_url: str
    # Webhook URL the gateway should call back. Same per environment.
    webhook_url: str
    # Optional: extra free-form metadata to round-trip back via webhook.
    metadata: dict[str, str] | None = None


@dataclass(frozen=True)
class CreatePaymentResult:
    """What the gateway returns from "start a checkout session"."""

    provider_payment_id: str
    # The URL we tell the customer's browser to redirect to.
    checkout_url: str
    expires_at: datetime | None
    # Verbatim gateway response (sanitised — no raw card numbers).
    raw: dict


@dataclass(frozen=True)
class ExecutePaymentResult:
    """Some gateways (Bkash) need a follow-up call after the user
    completes the redirect to actually capture the payment. Others
    (SSLCommerz) capture during the callback directly. Adapters that
    don't need an explicit execute step return this with status='captured'
    and zero work needed.
    """

    status: str  # 'authorized' | 'captured' | 'failed' | 'cancelled'
    amount_captured: Decimal
    error_code: str | None = None
    error_message: str | None = None
    raw: dict | None = None


@dataclass(frozen=True)
class QueryPaymentResult:
    status: str
    amount_captured: Decimal
    amount_refunded: Decimal
    raw: dict


@dataclass(frozen=True)
class RefundProviderRequest:
    intent_id: str
    provider_payment_id: str
    amount: Decimal
    currency: str
    reason: str | None
    # Idempotency key for the gateway. Use payment_refunds.id.
    refund_request_id: str


@dataclass(frozen=True)
class RefundProviderResult:
    status: str  # 'succeeded' | 'failed'
    provider_refund_id: str | None
    error_code: str | None = None
    error_message: str | None = None
    raw: dict | None = None


@dataclass(frozen=True)
class WebhookEvent:
    """What the adapter parses out of a raw webhook body. The service
    uses these fields to resolve the intent and apply the state change.
    """

    event_id: str  # gateway-supplied or hash-derived; used for idempotency
    provider_payment_id: str
    status: str  # 'captured' | 'failed' | 'cancelled' | 'expired' | 'refunded' | ...
    amount_captured: Decimal | None = None
    amount_refunded: Decimal | None = None
    error_code: str | None = None
    error_message: str | None = None


class PaymentProvider(ABC):
    """Capability port. Adapters implement against their REST API."""

    name: str = "abstract"

    @abstractmethod
    async def create_payment(
        self, req: CreatePaymentRequest,
    ) -> CreatePaymentResult: ...

    @abstractmethod
    async def execute_payment(
        self, *, intent_id: str, provider_payment_id: str,
    ) -> ExecutePaymentResult: ...

    @abstractmethod
    async def query_payment(
        self, *, provider_payment_id: str,
    ) -> QueryPaymentResult: ...

    @abstractmethod
    async def refund(
        self, req: RefundProviderRequest,
    ) -> RefundProviderResult: ...

    @abstractmethod
    def parse_webhook(
        self, *, body: bytes, headers: dict[str, str],
    ) -> WebhookEvent:
        """Parse + signature-verify a raw webhook payload.

        Implementations MUST raise ``app.core.errors.IntegrationError``
        with ``details={"reason":"signature_failed"}`` (or similar) on
        verification failure. The service treats any exception here as
        a webhook rejection, logs it, and returns 400 to the gateway
        WITHOUT mutating any intent.
        """
