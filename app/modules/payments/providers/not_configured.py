"""Default payment provider — fails loud.

Bound at startup when ``settings.payment_provider`` is unset (or set to
``none``). Any service-layer call falls through to a clear 502 with a
``missing_setting`` sentinel that ops can grep for in logs.
"""

from __future__ import annotations

from app.core.errors import IntegrationError
from app.modules.payments.providers.base import (
    CreatePaymentRequest,
    CreatePaymentResult,
    ExecutePaymentResult,
    PaymentProvider,
    QueryPaymentResult,
    RefundProviderRequest,
    RefundProviderResult,
    WebhookEvent,
)


def _not_configured(capability: str) -> "IntegrationError":
    return IntegrationError(
        message=(
            f"Payment capability '{capability}' is not configured. Set "
            "PAYMENT_PROVIDER and the matching credentials in env "
            "(e.g. PAYMENT_PROVIDER=bkash + BKASH_APP_KEY=... + "
            "BKASH_APP_SECRET=... + BKASH_USERNAME=... + BKASH_PASSWORD=...) "
            "and restart so the lifespan rebinds the provider."
        ),
        details={
            "capability": capability,
            "missing_setting": "PAYMENT_PROVIDER",
        },
    )


class NotConfiguredPaymentProvider(PaymentProvider):
    name = "not_configured"

    async def create_payment(
        self, req: CreatePaymentRequest,
    ) -> CreatePaymentResult:
        raise _not_configured("create_payment")

    async def execute_payment(
        self, *, intent_id: str, provider_payment_id: str,
    ) -> ExecutePaymentResult:
        raise _not_configured("execute_payment")

    async def query_payment(
        self, *, provider_payment_id: str,
    ) -> QueryPaymentResult:
        raise _not_configured("query_payment")

    async def refund(
        self, req: RefundProviderRequest,
    ) -> RefundProviderResult:
        raise _not_configured("refund")

    def parse_webhook(
        self, *, body: bytes, headers: dict[str, str],
    ) -> WebhookEvent:
        raise _not_configured("parse_webhook")
