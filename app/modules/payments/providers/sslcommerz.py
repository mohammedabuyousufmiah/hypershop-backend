"""SSLCommerz adapter (Bangladesh-only multi-rail PSP).

References:
  - Sandbox: https://developer.sslcommerz.com/doc/v4/
  - Sandbox base: https://sandbox.sslcommerz.com
  - Live base:    https://securepay.sslcommerz.com
  - Initiate:  POST /gwprocess/v4/api.php  (form-encoded)
  - Validate:  GET  /validator/api/validationserverAPI.php
  - Refund:    GET  /validator/api/merchantTransIDvalidationAPI.php (w/ refund params)

Flow:
  1. POST /gwprocess/v4/api.php (form) → status=SUCCESS + GatewayPageURL
     We send the customer to GatewayPageURL.
  2. Customer pays at SSLCommerz hosted page, bounces back to success_url.
  3. Webhook (IPN) POSTs form-encoded data to our webhook URL.
  4. We MUST GET /validator/api/validationserverAPI.php?val_id=<from IPN>
     to confirm the transaction (signature verification = the val_id
     belongs to the same store + amount matches).
  5. SSLCommerz refund: GET the refund endpoint with bank_tran_id +
     refund_amount + refund_remarks.

Webhook signature:
  SSLCommerz IPN includes a `verify_sign` (or `verify_sign_sha2`) field
  that is an MD5/SHA256 of all other fields concatenated with the
  store_passwd. We verify this BEFORE trusting the body.
"""

from __future__ import annotations

import hashlib
import json
import urllib.parse
from decimal import Decimal
from typing import Any

from app.core.errors import IntegrationError
from app.core.logging import get_logger
from app.modules.payments.providers._http import get_json, post_form
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

_logger = get_logger("hypershop.payments.sslcommerz")


# SSLCommerz status_code values → our internal enum
_SSL_STATUS_MAP = {
    "VALID": "captured",
    "VALIDATED": "captured",
    "PENDING": "authorized",
    "FAILED": "failed",
    "CANCELLED": "cancelled",
    "EXPIRED": "expired",
    "UNATTEMPTED": "initiated",
    "INVALID_TRANSACTION": "failed",
}


def _map_status(s: str) -> str:
    return _SSL_STATUS_MAP.get((s or "").upper(), "failed")


class SSLCommerzProvider(PaymentProvider):
    name = "sslcommerz"

    def __init__(
        self, *,
        store_id: str,
        store_passwd: str,
        base_url: str,
        is_sandbox: bool = True,
        webhook_url: str | None = None,
    ) -> None:
        if not all([store_id, store_passwd, base_url]):
            raise IntegrationError(
                "SSLCommerzProvider requires store_id, store_passwd, base_url.",
                details={"missing_setting": "SSLCOMMERZ_*"},
            )
        self._store_id = store_id
        self._store_passwd = store_passwd
        self._base_url = base_url.rstrip("/")
        self._is_sandbox = is_sandbox
        self._webhook_url = webhook_url or ""

    # ---------------- Capabilities ----------------

    async def create_payment(
        self, req: CreatePaymentRequest,
    ) -> CreatePaymentResult:
        if req.currency.upper() != "BDT":
            raise IntegrationError(
                f"SSLCommerz BD-only deployment supports BDT, got {req.currency}.",
                details={"currency": req.currency},
            )
        amount_str = f"{req.amount.quantize(Decimal('0.01'))}"
        form: dict[str, str] = {
            "store_id": self._store_id,
            "store_passwd": self._store_passwd,
            "total_amount": amount_str,
            "currency": "BDT",
            "tran_id": req.intent_id,  # MUST be unique per attempt — we use intent UUID
            "success_url": req.success_url,
            "fail_url": req.failure_url,
            "cancel_url": req.cancel_url,
            "ipn_url": req.webhook_url or self._webhook_url,
            "shipping_method": "NO",  # logical/digital fulfilment; physical handled separately
            "product_name": f"Order {req.order_code}",
            "product_category": "Retail",
            "product_profile": "general",
            "cus_name": req.customer_name or "Customer",
            "cus_email": req.customer_email or "noreply@hypershop.local",
            "cus_phone": req.customer_phone or "00000000000",
            "cus_add1": "N/A",
            "cus_city": "N/A",
            "cus_country": "Bangladesh",
            # Round-trip our intent_id back via the value_a slot — IPN
            # echoes it so we can resolve quickly.
            "value_a": req.intent_id,
        }
        r = await post_form(
            base_url=self._base_url,
            path="/gwprocess/v4/api.php",
            headers={"Accept": "application/json"},
            form=form,
        )
        body = r.body
        if body.get("status") != "SUCCESS":
            raise IntegrationError(
                f"SSLCommerz init rejected: {body.get('failedreason')!r}",
                details={"body": body},
            )
        gateway_page_url = body.get("GatewayPageURL")
        sessionkey = body.get("sessionkey")
        if not gateway_page_url or not sessionkey:
            raise IntegrationError(
                "SSLCommerz init missing GatewayPageURL or sessionkey.",
                details={"body": body},
            )
        return CreatePaymentResult(
            provider_payment_id=str(sessionkey),
            checkout_url=str(gateway_page_url),
            expires_at=None,  # SSLCommerz session lifetime is ~24h; not exposed precisely
            raw=body,
        )

    async def execute_payment(
        self, *, intent_id: str, provider_payment_id: str,
    ) -> ExecutePaymentResult:
        """SSLCommerz captures during the IPN/redirect flow — there is
        no separate execute step. Service should never call this; if it
        does (e.g. some recovery code path), we treat it as a query."""
        q = await self.query_payment(provider_payment_id=provider_payment_id)
        return ExecutePaymentResult(
            status=q.status,
            amount_captured=q.amount_captured,
            raw=q.raw,
        )

    async def query_payment(
        self, *, provider_payment_id: str,
    ) -> QueryPaymentResult:
        """Validate a transaction by sessionkey OR val_id. The validator
        endpoint accepts either; we pass `tran_id` (our intent id) which
        we know is the same as what we sent in `tran_id` at create time.
        Caller must pass the intent's UUID as ``provider_payment_id`` if
        no real transaction has been issued yet — but the only sane
        callsite is post-IPN, after a real val_id has been seen.
        """
        params = {
            "store_id": self._store_id,
            "store_passwd": self._store_passwd,
            "tran_id": provider_payment_id,
            "format": "json",
        }
        r = await get_json(
            base_url=self._base_url,
            path="/validator/api/merchantTransIDvalidationAPI.php",
            headers={"Accept": "application/json"},
            params=params,
        )
        body = r.body
        # SSLCommerz returns either a single object or {element: [...]}.
        elements = body.get("element") if isinstance(body, dict) else None
        if isinstance(elements, list) and elements:
            txn = elements[0]
        elif isinstance(body, dict) and body.get("status"):
            txn = body
        else:
            return QueryPaymentResult(
                status="initiated",
                amount_captured=Decimal("0"),
                amount_refunded=Decimal("0"),
                raw=body,
            )
        status = _map_status(txn.get("status", ""))
        amount = Decimal(str(txn.get("amount", "0") or "0"))
        return QueryPaymentResult(
            status=status,
            amount_captured=amount if status in ("captured",) else Decimal("0"),
            amount_refunded=Decimal("0"),  # refund tracking via separate API
            raw=body,
        )

    async def refund(
        self, req: RefundProviderRequest,
    ) -> RefundProviderResult:
        # SSLCommerz refund needs the gateway-side `bank_tran_id`. We
        # stash that under `provider_payment_id` after the IPN is
        # processed (service writes it). If it's still the sessionkey,
        # the refund will fail with a clear message — caller must
        # retry once IPN has populated it.
        params = {
            "bank_tran_id": req.provider_payment_id,
            "refund_amount": f"{req.amount.quantize(Decimal('0.01'))}",
            "refund_remarks": (req.reason or "Refund")[:255],
            "refe_id": req.refund_request_id,
            "store_id": self._store_id,
            "store_passwd": self._store_passwd,
            "format": "json",
        }
        r = await get_json(
            base_url=self._base_url,
            path="/validator/api/merchantTransIDvalidationAPI.php",
            headers={"Accept": "application/json"},
            params=params,
        )
        body = r.body
        api_status = (body.get("APIConnect") or "").upper()
        if api_status == "DONE" and (body.get("status") or "").lower() in (
            "success", "processing",
        ):
            return RefundProviderResult(
                status="succeeded",
                provider_refund_id=str(body.get("refund_ref_id") or ""),
                raw=body,
            )
        return RefundProviderResult(
            status="failed",
            provider_refund_id=None,
            error_code=str(body.get("errorReason") or "refund_rejected"),
            error_message=str(body.get("errorReason", "")) or "SSLCommerz refund rejected",
            raw=body,
        )

    def parse_webhook(
        self, *, body: bytes, headers: dict[str, str],
    ) -> WebhookEvent:
        """SSLCommerz IPN posts application/x-www-form-urlencoded.

        We verify ``verify_sign`` (MD5) before trusting any field. The
        IPN echoes back ``tran_id`` (our intent id), ``val_id`` (the
        gateway transaction id), and ``status``.
        """
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError as e:
            raise IntegrationError(
                "SSLCommerz IPN body is not UTF-8.",
                details={"reason": "bad_body"},
            ) from e

        # Try form-encoded first; fall back to JSON.
        fields: dict[str, str]
        ct = (headers.get("content-type") or headers.get("Content-Type") or "").lower()
        if "application/x-www-form-urlencoded" in ct:
            parsed = urllib.parse.parse_qs(text, keep_blank_values=True)
            fields = {k: v[0] if v else "" for k, v in parsed.items()}
        else:
            try:
                fields = json.loads(text or "{}")
            except json.JSONDecodeError as e:
                raise IntegrationError(
                    "SSLCommerz IPN body is not parseable.",
                    details={"reason": "bad_body"},
                ) from e

        verify_sign = fields.get("verify_sign", "")
        verify_key = fields.get("verify_key", "")
        if not verify_sign or not verify_key:
            raise IntegrationError(
                "SSLCommerz IPN missing verify_sign / verify_key.",
                details={"reason": "missing_signature"},
            )

        # Build the signature: only the field names listed in `verify_key`
        # (comma-separated), sorted alphabetically, plus store_passwd
        # (MD5'd). Format: key=value&key=value... + store_passwd=MD5(...).
        keys = [k.strip() for k in verify_key.split(",") if k.strip()]
        keys.sort()
        sig_parts = [f"{k}={fields.get(k, '')}" for k in keys]
        store_passwd_md5 = hashlib.md5(self._store_passwd.encode()).hexdigest()  # noqa: S324
        sig_parts.append(f"store_passwd={store_passwd_md5}")
        sig_parts.sort()
        sig_string = "&".join(sig_parts)
        expected = hashlib.md5(sig_string.encode()).hexdigest()  # noqa: S324
        if verify_sign.lower() != expected.lower():
            raise IntegrationError(
                "SSLCommerz IPN signature mismatch.",
                details={
                    "reason": "signature_failed",
                    "store_id": fields.get("store_id"),
                    "tran_id": fields.get("tran_id"),
                },
            )

        # Build the WebhookEvent
        tran_id = fields.get("tran_id") or fields.get("value_a") or ""
        val_id = fields.get("val_id") or ""
        ssl_status = fields.get("status") or ""
        mapped = _map_status(ssl_status)
        amt: Decimal | None = None
        if fields.get("amount"):
            try:
                amt = Decimal(str(fields["amount"]))
            except (ValueError, ArithmeticError):
                amt = None

        # Use val_id as event_id when present (gateway-supplied, stable).
        # Fall back to a hash if not.
        event_id = val_id or hashlib.sha256(text.encode()).hexdigest()[:64]
        # `provider_payment_id` should be the bank_tran_id when present
        # (needed for refund), else fall back to tran_id (our intent UUID).
        bank_tran_id = fields.get("bank_tran_id") or tran_id

        return WebhookEvent(
            event_id=event_id,
            provider_payment_id=str(bank_tran_id),
            status=mapped,
            amount_captured=amt if mapped == "captured" else None,
            amount_refunded=amt if mapped == "refunded" else None,
        )

    # Helper exposed for tests + ad-hoc validation.
    @staticmethod
    def _verify_signature_for_test(  # pragma: no cover
        store_passwd: str, fields: dict[str, str],
    ) -> str:
        """Compute the MD5 signature SSLCommerz expects in `verify_sign`.
        Useful for building test fixtures matching their format.
        """
        verify_key = fields.get("verify_key", "")
        keys = sorted(k.strip() for k in verify_key.split(",") if k.strip())
        sig_parts = [f"{k}={fields.get(k, '')}" for k in keys]
        store_passwd_md5 = hashlib.md5(store_passwd.encode()).hexdigest()  # noqa: S324
        sig_parts.append(f"store_passwd={store_passwd_md5}")
        sig_parts.sort()
        sig_string = "&".join(sig_parts)
        return hashlib.md5(sig_string.encode()).hexdigest()  # noqa: S324
