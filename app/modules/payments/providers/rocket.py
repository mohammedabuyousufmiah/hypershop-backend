"""Rocket (Dutch-Bangla Bank Mobile Banking) adapter.

Rocket does NOT publish a public REST gateway like Bkash or SSLCommerz.
The two production paths to integrate Rocket are:

  1. **Aggregator route (recommended)**: Rocket is offered as a payment
     channel via PSPs like SSLCommerz, ShurjoPay, AamarPay, etc. In
     that case, you do NOT need a Rocket-specific adapter — the
     aggregator handles the rail. Set ``PAYMENT_PROVIDER=sslcommerz``
     and the customer picks "Rocket" on the SSLCommerz hosted page.

  2. **Direct merchant API**: Rocket exposes a Merchant Pay (DBBL
     Merchant Settlement) API only after a signed agreement; the URL,
     credentials and request schemas are issued per-merchant under NDA.
     Common shape:
       - Base: https://test.rocketmerchant.com.bd  (sandbox)
              https://rocketmerchant.com.bd        (live)
       - Init: POST /api/v1/payment/init      (HMAC-SHA256 signed body)
       - Verify: GET /api/v1/payment/verify
       - Refund: POST /api/v1/payment/refund

This adapter implements the **direct** path against the most-common
public shape. Operators on the aggregator path should NOT bind this
adapter — leave ``ROCKET_*`` env unset and use ``sslcommerz`` instead.

Refusal contract:
  Adapter constructor REQUIRES merchant_id, app_key, app_secret, base_url.
  Missing any → IntegrationError("missing_setting": "ROCKET_*"). The
  factory turns that into a NotConfigured binding so the customer never
  sees a 500.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from app.core.errors import IntegrationError
from app.core.logging import get_logger
from app.modules.payments.providers._http import get_json, post_json
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

_logger = get_logger("hypershop.payments.rocket")


_ROCKET_STATUS_MAP = {
    "SUCCESS": "captured",
    "COMPLETED": "captured",
    "PENDING": "initiated",
    "PROCESSING": "authorized",
    "FAILED": "failed",
    "CANCELLED": "cancelled",
    "EXPIRED": "expired",
    "REFUNDED": "refunded",
}


def _map_status(s: str) -> str:
    return _ROCKET_STATUS_MAP.get((s or "").upper(), "failed")


class RocketProvider(PaymentProvider):
    name = "rocket"

    def __init__(
        self, *,
        merchant_id: str,
        app_key: str,
        app_secret: str,
        base_url: str,
        is_sandbox: bool = True,
        webhook_url: str | None = None,
    ) -> None:
        if not all([merchant_id, app_key, app_secret, base_url]):
            raise IntegrationError(
                "RocketProvider requires merchant_id, app_key, "
                "app_secret and base_url. If you're routing Rocket via "
                "an aggregator (SSLCommerz / ShurjoPay), do NOT bind "
                "this provider — switch PAYMENT_PROVIDER=sslcommerz.",
                details={"missing_setting": "ROCKET_*"},
            )
        self._merchant_id = merchant_id
        self._app_key = app_key
        self._app_secret = app_secret
        self._base_url = base_url.rstrip("/")
        self._is_sandbox = is_sandbox
        self._webhook_url = webhook_url or ""

    # ---------------- Signing ----------------

    def _sign(self, body_json: str, ts: str, nonce: str) -> str:
        """HMAC-SHA256 of (HTTP body || timestamp || nonce) with app_secret.
        The signature goes into the X-Signature header; Rocket re-computes
        it using the same recipe + replays it back in webhook calls.
        """
        msg = f"{body_json}|{ts}|{nonce}".encode()
        digest = hmac.new(
            self._app_secret.encode(), msg, hashlib.sha256,
        ).hexdigest()
        return digest

    def _verify(self, body_text: str, ts: str, nonce: str, signature: str) -> bool:
        expected = self._sign(body_text, ts, nonce)
        return hmac.compare_digest(expected, (signature or "").lower())

    def _signed_headers(self, body_json: str) -> dict[str, str]:
        ts = str(int(time.time()))
        import secrets
        nonce = secrets.token_urlsafe(16)
        return {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-Merchant-Id": self._merchant_id,
            "X-App-Key": self._app_key,
            "X-Timestamp": ts,
            "X-Nonce": nonce,
            "X-Signature": self._sign(body_json, ts, nonce),
        }

    # ---------------- Capabilities ----------------

    async def create_payment(
        self, req: CreatePaymentRequest,
    ) -> CreatePaymentResult:
        if req.currency.upper() != "BDT":
            raise IntegrationError(
                f"Rocket only supports BDT, got {req.currency}.",
                details={"currency": req.currency},
            )
        amount_str = f"{req.amount.quantize(Decimal('0.01'))}"
        body: dict[str, Any] = {
            "merchantId": self._merchant_id,
            "merchantOrderId": req.order_code,
            "merchantTxnId": req.intent_id,
            "amount": amount_str,
            "currency": "BDT",
            "customerName": req.customer_name or "",
            "customerPhone": req.customer_phone or "",
            "customerEmail": req.customer_email or "",
            "successUrl": req.success_url,
            "failureUrl": req.failure_url,
            "cancelUrl": req.cancel_url,
            "ipnUrl": req.webhook_url or self._webhook_url,
            "intent": "SALE",
        }
        body_json = json.dumps(body, separators=(",", ":"))
        r = await post_json(
            base_url=self._base_url,
            path="/api/v1/payment/init",
            headers=self._signed_headers(body_json),
            body=body,
        )
        if (r.body.get("status") or "").upper() not in ("SUCCESS", "PENDING"):
            raise IntegrationError(
                f"Rocket init rejected: {r.body.get('message')!r}",
                details={"body": r.body},
            )
        gateway_url = r.body.get("paymentUrl") or r.body.get("redirectUrl")
        rocket_txn_id = r.body.get("rocketTxnId") or r.body.get("transactionId")
        if not gateway_url or not rocket_txn_id:
            raise IntegrationError(
                "Rocket init missing paymentUrl or rocketTxnId.",
                details={"body": r.body},
            )
        # Rocket sessions usually expire ~20 min after init.
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=20)
        return CreatePaymentResult(
            provider_payment_id=str(rocket_txn_id),
            checkout_url=str(gateway_url),
            expires_at=expires_at,
            raw=r.body,
        )

    async def execute_payment(
        self, *, intent_id: str, provider_payment_id: str,
    ) -> ExecutePaymentResult:
        # Rocket captures on successful customer flow + IPN — no
        # separate execute step. Treat as a query.
        q = await self.query_payment(provider_payment_id=provider_payment_id)
        return ExecutePaymentResult(
            status=q.status,
            amount_captured=q.amount_captured,
            raw=q.raw,
        )

    async def query_payment(
        self, *, provider_payment_id: str,
    ) -> QueryPaymentResult:
        r = await get_json(
            base_url=self._base_url,
            path="/api/v1/payment/verify",
            headers=self._signed_headers(""),
            params={
                "merchantId": self._merchant_id,
                "rocketTxnId": provider_payment_id,
            },
        )
        body = r.body
        status_raw = body.get("status") or body.get("paymentStatus") or "FAILED"
        amt_raw = body.get("amount") or "0"
        try:
            amt = Decimal(str(amt_raw))
        except (ArithmeticError, ValueError):
            amt = Decimal("0")
        mapped = _map_status(status_raw)
        return QueryPaymentResult(
            status=mapped,
            amount_captured=amt if mapped == "captured" else Decimal("0"),
            amount_refunded=amt if mapped == "refunded" else Decimal("0"),
            raw=body,
        )

    async def refund(
        self, req: RefundProviderRequest,
    ) -> RefundProviderResult:
        amount_str = f"{req.amount.quantize(Decimal('0.01'))}"
        body = {
            "merchantId": self._merchant_id,
            "rocketTxnId": req.provider_payment_id,
            "merchantRefundId": req.refund_request_id,
            "amount": amount_str,
            "reason": (req.reason or "Refund")[:255],
        }
        body_json = json.dumps(body, separators=(",", ":"))
        r = await post_json(
            base_url=self._base_url,
            path="/api/v1/payment/refund",
            headers=self._signed_headers(body_json),
            body=body,
        )
        if (r.body.get("status") or "").upper() in ("SUCCESS", "PROCESSING"):
            return RefundProviderResult(
                status="succeeded",
                provider_refund_id=str(r.body.get("refundTxnId") or ""),
                raw=r.body,
            )
        return RefundProviderResult(
            status="failed",
            provider_refund_id=None,
            error_code=str(r.body.get("errorCode") or "refund_rejected"),
            error_message=str(r.body.get("message") or "Rocket refund rejected"),
            raw=r.body,
        )

    def parse_webhook(
        self, *, body: bytes, headers: dict[str, str],
    ) -> WebhookEvent:
        ts = headers.get("x-timestamp") or headers.get("X-Timestamp") or ""
        nonce = headers.get("x-nonce") or headers.get("X-Nonce") or ""
        signature = headers.get("x-signature") or headers.get("X-Signature") or ""
        if not (ts and nonce and signature):
            raise IntegrationError(
                "Rocket webhook missing X-Timestamp / X-Nonce / X-Signature.",
                details={"reason": "missing_signature"},
            )
        try:
            body_text = body.decode("utf-8")
        except UnicodeDecodeError as e:
            raise IntegrationError(
                "Rocket webhook body is not UTF-8.",
                details={"reason": "bad_body"},
            ) from e

        # Reject stale requests (timestamp older than 5 min) to neuter
        # replay attacks even if signature happens to match.
        try:
            req_ts = int(ts)
        except ValueError as e:
            raise IntegrationError(
                "Rocket webhook X-Timestamp is not an integer.",
                details={"reason": "bad_timestamp"},
            ) from e
        if abs(int(time.time()) - req_ts) > 300:
            raise IntegrationError(
                "Rocket webhook timestamp drift > 5 minutes — likely replay.",
                details={"reason": "stale_timestamp"},
            )

        if not self._verify(body_text, ts, nonce, signature):
            raise IntegrationError(
                "Rocket webhook signature mismatch.",
                details={"reason": "signature_failed"},
            )

        try:
            payload = json.loads(body_text or "{}")
        except json.JSONDecodeError as e:
            raise IntegrationError(
                "Rocket webhook body is not JSON.",
                details={"reason": "bad_body"},
            ) from e

        rocket_txn_id = (
            payload.get("rocketTxnId")
            or payload.get("transactionId")
            or ""
        )
        status_raw = payload.get("status") or payload.get("paymentStatus") or "FAILED"
        amt_raw = payload.get("amount")
        amt: Decimal | None = None
        if amt_raw is not None:
            try:
                amt = Decimal(str(amt_raw))
            except (ArithmeticError, ValueError):
                amt = None

        # Use eventId if Rocket provides one (some merchants get one),
        # else hash of (rocket_txn_id, status, ts).
        event_id = payload.get("eventId") or hashlib.sha256(
            f"{rocket_txn_id}|{status_raw}|{ts}".encode(),
        ).hexdigest()[:64]
        mapped = _map_status(status_raw)
        return WebhookEvent(
            event_id=str(event_id),
            provider_payment_id=str(rocket_txn_id),
            status=mapped,
            amount_captured=amt if mapped == "captured" else None,
            amount_refunded=amt if mapped == "refunded" else None,
        )
