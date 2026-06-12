"""Bkash Tokenized Checkout adapter.

References:
  - https://developer.bka.sh/reference  (Tokenized Checkout v1.2.0-beta)
  - Sandbox base: https://tokenized.sandbox.bka.sh/v1.2.0-beta
  - Live base:    https://tokenized.pay.bka.sh/v1.2.0-beta

Flow:
  1. POST /tokenized/checkout/token/grant       → id_token + refresh_token
  2. POST /tokenized/checkout/create            → paymentID + bkashURL
     (we send the customer's browser to bkashURL)
  3. Customer completes flow on Bkash's site, bounces back to callbackURL
  4. POST /tokenized/checkout/execute           → captures the payment
  5. POST /tokenized/checkout/payment/status    → idempotent state check
  6. POST /tokenized/checkout/payment/refund    → refund
  7. Webhook: Bkash POSTs the same `paymentID` payload to our webhook URL.
     Body is JSON; signature verification = matched paymentID exists in our
     DB AND a fresh status query confirms the reported state.

Token caching:
  Tokens last ~1 hour. We cache the most-recently issued id_token in
  the adapter instance with a 50-minute TTL. The first call after TTL
  re-grants. This is a single-process cache — fine for our compose
  deployment.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from app.core.errors import IntegrationError
from app.core.logging import get_logger
from app.modules.payments.providers._http import post_json
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

_logger = get_logger("hypershop.payments.bkash")


# Bkash status strings → our internal enum (state.py:PaymentStatus)
_BKASH_STATUS_MAP = {
    # transactionStatus values
    "Initiated": "initiated",
    "Authorized": "authorized",
    "Completed": "captured",
    "Cancelled": "cancelled",
    "Failed": "failed",
    "Refunded": "refunded",
    "Reversed": "refunded",
    # statusCode "0000" = success
}


def _map_status(bkash_status: str) -> str:
    return _BKASH_STATUS_MAP.get(bkash_status, "failed")


class BkashProvider(PaymentProvider):
    name = "bkash"

    def __init__(
        self, *,
        app_key: str,
        app_secret: str,
        username: str,
        password: str,
        base_url: str,
        webhook_url: str | None = None,
    ) -> None:
        if not all([app_key, app_secret, username, password, base_url]):
            raise IntegrationError(
                "BkashProvider requires app_key, app_secret, username, "
                "password and base_url.",
                details={"missing_setting": "BKASH_*"},
            )
        self._app_key = app_key
        self._app_secret = app_secret
        self._username = username
        self._password = password
        self._base_url = base_url.rstrip("/")
        self._webhook_url = webhook_url or ""

        self._token_lock = asyncio.Lock()
        self._token: str | None = None
        # Bkash tokens last 3600s; refresh proactively at 50 min.
        self._token_expires_at: float = 0.0

    # ---------------- Token management ----------------

    async def _get_token(self) -> str:
        if self._token and time.time() < self._token_expires_at:
            return self._token
        async with self._token_lock:
            if self._token and time.time() < self._token_expires_at:
                return self._token
            r = await post_json(
                base_url=self._base_url,
                path="/tokenized/checkout/token/grant",
                headers={
                    "username": self._username,
                    "password": self._password,
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                body={
                    "app_key": self._app_key,
                    "app_secret": self._app_secret,
                },
            )
            id_token = r.body.get("id_token")
            if not id_token:
                raise IntegrationError(
                    "Bkash token grant returned no id_token.",
                    details={"body": r.body},
                )
            self._token = str(id_token)
            self._token_expires_at = time.time() + 3000  # 50 min
            return self._token

    async def _auth_headers(self) -> dict[str, str]:
        token = await self._get_token()
        return {
            "Authorization": token,
            "X-App-Key": self._app_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    # ---------------- Capabilities ----------------

    async def create_payment(
        self, req: CreatePaymentRequest,
    ) -> CreatePaymentResult:
        # Bkash expects amount as a string with at most 2 decimals.
        amount_str = f"{req.amount.quantize(Decimal('0.01'))}"
        # Bkash currency: only BDT supported in sandbox/live tokenized.
        if req.currency.upper() != "BDT":
            raise IntegrationError(
                f"Bkash only supports BDT, got {req.currency}.",
                details={"currency": req.currency},
            )
        body: dict[str, Any] = {
            "mode": "0011",  # one-shot tokenized checkout
            "payerReference": req.customer_phone or req.intent_id,
            "callbackURL": req.success_url,  # Bkash bounces here on completion
            "amount": amount_str,
            "currency": "BDT",
            "intent": "sale",
            "merchantInvoiceNumber": req.order_code,
        }
        r = await post_json(
            base_url=self._base_url,
            path="/tokenized/checkout/create",
            headers=await self._auth_headers(),
            body=body,
        )
        if r.body.get("statusCode") != "0000":
            raise IntegrationError(
                f"Bkash create rejected: {r.body.get('statusMessage')!r}",
                details={"body": r.body},
            )
        payment_id = r.body.get("paymentID")
        bkash_url = r.body.get("bkashURL")
        if not payment_id or not bkash_url:
            raise IntegrationError(
                "Bkash create missing paymentID or bkashURL.",
                details={"body": r.body},
            )
        # Bkash sessions usually expire ~30 min after create.
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=30)
        return CreatePaymentResult(
            provider_payment_id=str(payment_id),
            checkout_url=str(bkash_url),
            expires_at=expires_at,
            raw=r.body,
        )

    async def execute_payment(
        self, *, intent_id: str, provider_payment_id: str,
    ) -> ExecutePaymentResult:
        r = await post_json(
            base_url=self._base_url,
            path="/tokenized/checkout/execute",
            headers=await self._auth_headers(),
            body={"paymentID": provider_payment_id},
        )
        body = r.body
        status_code = body.get("statusCode")
        trx_status = body.get("transactionStatus", "")

        if status_code == "0000" and trx_status == "Completed":
            amt = Decimal(str(body.get("amount", "0")))
            return ExecutePaymentResult(
                status="captured",
                amount_captured=amt,
                raw=body,
            )
        # Map any other trx_status, default to failed
        return ExecutePaymentResult(
            status=_map_status(trx_status) if trx_status else "failed",
            amount_captured=Decimal("0"),
            error_code=str(status_code) if status_code else "execute_rejected",
            error_message=str(body.get("statusMessage", "")) or "Bkash execute rejected",
            raw=body,
        )

    async def query_payment(
        self, *, provider_payment_id: str,
    ) -> QueryPaymentResult:
        r = await post_json(
            base_url=self._base_url,
            path="/tokenized/checkout/payment/status",
            headers=await self._auth_headers(),
            body={"paymentID": provider_payment_id},
        )
        body = r.body
        trx_status = body.get("transactionStatus", "")
        amount_captured = Decimal("0")
        amount_refunded = Decimal("0")
        if body.get("amount") and trx_status in ("Completed", "Refunded", "Reversed"):
            amount_captured = Decimal(str(body["amount"]))
        if trx_status in ("Refunded", "Reversed"):
            amount_refunded = amount_captured
        return QueryPaymentResult(
            status=_map_status(trx_status),
            amount_captured=amount_captured,
            amount_refunded=amount_refunded,
            raw=body,
        )

    async def refund(
        self, req: RefundProviderRequest,
    ) -> RefundProviderResult:
        amount_str = f"{req.amount.quantize(Decimal('0.01'))}"
        body = {
            "paymentID": req.provider_payment_id,
            "amount": amount_str,
            "trxID": req.provider_payment_id,  # Bkash wants the trxID; many integrations reuse paymentID
            "sku": "refund",
            "reason": (req.reason or "Refund")[:255],
        }
        r = await post_json(
            base_url=self._base_url,
            path="/tokenized/checkout/payment/refund",
            headers=await self._auth_headers(),
            body=body,
        )
        if r.body.get("statusCode") == "0000":
            return RefundProviderResult(
                status="succeeded",
                provider_refund_id=str(r.body.get("refundTrxID") or ""),
                raw=r.body,
            )
        return RefundProviderResult(
            status="failed",
            provider_refund_id=None,
            error_code=str(r.body.get("statusCode") or "refund_rejected"),
            error_message=str(r.body.get("statusMessage", "")) or "Bkash refund rejected",
            raw=r.body,
        )

    def parse_webhook(
        self, *, body: bytes, headers: dict[str, str],
    ) -> WebhookEvent:
        """Parse Bkash webhook body.

        Bkash's webhook posts a JSON body with `paymentID` and
        `transactionStatus`. There is no HMAC signature in the public
        sandbox docs — security is via "we only accept paymentIDs that
        exist in our DB AND the status matches what we get back from a
        fresh /payment/status query". The service does that follow-up
        query before applying any state mutation.
        """
        try:
            payload = json.loads(body.decode("utf-8") or "{}")
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise IntegrationError(
                "Bkash webhook body is not JSON.",
                details={"reason": "bad_body", "error": str(e)},
            ) from e
        payment_id = payload.get("paymentID") or payload.get("payment_id")
        if not payment_id:
            raise IntegrationError(
                "Bkash webhook missing paymentID.",
                details={"reason": "missing_payment_id", "body": payload},
            )
        trx_status = payload.get("transactionStatus") or payload.get("status") or ""
        # Use SHA256 of body bytes as event_id since Bkash does not
        # supply a stable event ID — gives us idempotent dedup.
        event_id = hashlib.sha256(body).hexdigest()[:64]

        amt: Decimal | None = None
        if payload.get("amount"):
            try:
                amt = Decimal(str(payload["amount"]))
            except (ValueError, ArithmeticError):
                amt = None

        mapped = _map_status(trx_status)
        return WebhookEvent(
            event_id=event_id,
            provider_payment_id=str(payment_id),
            status=mapped,
            amount_captured=amt if mapped in ("captured", "refunded") else None,
            amount_refunded=amt if mapped == "refunded" else None,
        )
