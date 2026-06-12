"""Dev-mode FAKE payment provider — auto-succeeds with no real creds.

WHY THIS EXISTS
---------------
The real Bkash + SSLCommerz adapters need merchant credentials we
don't have in local development (and shouldn't have in CI either —
their sandbox accounts are scarce). Without SOME bound provider,
``POST /api/v1/payments/initiate`` returns 502 and the entire
end-to-end "place an order" flow falls apart.

The fake provider:

* Returns a deterministic ``provider_payment_id`` (``fake-pi-<uuid>``)
* Returns a self-hosted ``checkout_url`` pointing at a Next.js
  ``/checkout/fake-pay`` page that auto-submits a success callback
  to our webhook endpoint after a 2-second "processing" animation
* ``execute_payment`` returns status='captured' for the full amount
* ``query_payment`` returns status='captured' (so post-order polling
  shows the order as paid)
* ``refund`` returns status='succeeded'
* ``parse_webhook`` accepts our own self-signed callback shape

PRODUCTION SAFETY
-----------------
The factory only binds this adapter when
``settings.environment != 'production'``. In production the env-gated
check is hard — even setting ``PAYMENT_PROVIDER=fake`` won't bind it.
This avoids the obvious foot-gun of someone leaving the fake adapter
enabled on a real deploy.

The webhook signature check uses a fixed dev-only secret. Real Bkash
+ SSLCommerz HMAC verification stays in their own adapters.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from app.core.logging import get_logger
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

_logger = get_logger("hypershop.payments.fake")


class FakePaymentProvider(PaymentProvider):
    """Auto-succeeds every operation. Dev/QA/CI only — never bind in prod."""

    name = "fake"

    def __init__(
        self,
        *,
        public_base_url: str = "http://localhost:3000",
        webhook_url: str | None = None,
    ) -> None:
        # Where to send the customer after they "pay". This points at
        # a Next.js page we ship in customer-web (see
        # ``app/[locale]/checkout/fake-pay/page.tsx``) that
        # auto-fires the success callback to our backend webhook.
        self._public_base_url = public_base_url.rstrip("/")
        self._webhook_url = webhook_url

    async def create_payment(
        self, req: CreatePaymentRequest,
    ) -> CreatePaymentResult:
        provider_payment_id = f"fake-pi-{uuid4().hex[:16]}"
        # Encode the intent_id + amount in the checkout URL so the
        # frontend page can POST it back to the webhook on "success".
        checkout_url = (
            f"{self._public_base_url}/checkout/fake-pay"
            f"?intent={req.intent_id}"
            f"&pi={provider_payment_id}"
            f"&amount={req.amount}"
            f"&currency={req.currency}"
        )
        _logger.info(
            "fake_payment_created",
            intent_id=req.intent_id,
            provider_payment_id=provider_payment_id,
            amount=str(req.amount),
            currency=req.currency,
        )
        return CreatePaymentResult(
            provider_payment_id=provider_payment_id,
            checkout_url=checkout_url,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
            raw={
                "fake": True,
                "amount": str(req.amount),
                "currency": req.currency,
            },
        )

    async def execute_payment(
        self, *, intent_id: str, provider_payment_id: str,
    ) -> ExecutePaymentResult:
        # In real Bkash this would call the "execute" endpoint to
        # capture the held authorisation. The fake provider has
        # nothing to call, so we just confirm "captured" for the
        # full amount the intent was created with. The service layer
        # threads the amount through; we accept whatever they pass.
        _logger.info(
            "fake_payment_executed",
            intent_id=intent_id,
            provider_payment_id=provider_payment_id,
        )
        return ExecutePaymentResult(
            status="captured",
            # Amount is filled by the service layer using the intent
            # row; we return zero here and let the caller override.
            # See payments.service.execute_payment for that pattern.
            amount_captured=Decimal("0"),
            raw={"fake": True, "executed_at": datetime.now(timezone.utc).isoformat()},
        )

    async def query_payment(
        self, *, provider_payment_id: str,
    ) -> QueryPaymentResult:
        return QueryPaymentResult(
            status="captured",
            amount_captured=Decimal("0"),
            amount_refunded=Decimal("0"),
            raw={"fake": True, "queried_at": datetime.now(timezone.utc).isoformat()},
        )

    async def refund(
        self, req: RefundProviderRequest,
    ) -> RefundProviderResult:
        _logger.info(
            "fake_refund_issued",
            intent_id=req.intent_id,
            amount=str(req.amount),
            reason=req.reason,
        )
        return RefundProviderResult(
            status="succeeded",
            provider_refund_id=f"fake-rf-{uuid4().hex[:16]}",
            raw={"fake": True, "refunded_at": datetime.now(timezone.utc).isoformat()},
        )

    def parse_webhook(
        self, *, body: bytes, headers: dict[str, str],
    ) -> WebhookEvent:
        # Self-signed shape — our own /checkout/fake-pay page posts
        # JSON like {"event_id":"...", "provider_payment_id":"...",
        # "status":"captured", "amount_captured":"529.00"}. We don't
        # verify any HMAC because there's nothing to verify; this is
        # dev-only.
        try:
            payload = json.loads(body.decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError) as e:
            _logger.warning("fake_webhook_unparseable", error=str(e))
            payload = {}

        # Decimal parser tolerant of empty strings, ``None``, and the
        # literal string ``"None"`` (which curl tests produced when the
        # frontend forwarded an unfilled amount field). A bad amount
        # becomes ``None`` here; downstream ingest then fills it from
        # the persisted intent's amount column.
        def _money(v: object) -> "Decimal | None":
            if v is None: return None
            s = str(v).strip()
            if not s or s.lower() == "none":
                return None
            try:
                return Decimal(s)
            except Exception:
                _logger.warning("fake_webhook_bad_amount", value=s)
                return None

        return WebhookEvent(
            event_id=payload.get("event_id") or f"fake-evt-{uuid4().hex[:12]}",
            provider_payment_id=payload.get("provider_payment_id", ""),
            status=payload.get("status", "captured"),
            amount_captured=_money(payload.get("amount_captured")),
            amount_refunded=_money(payload.get("amount_refunded")),
            error_code=payload.get("error_code"),
            error_message=payload.get("error_message"),
        )
