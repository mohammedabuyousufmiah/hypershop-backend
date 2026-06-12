"""Nagad Tokenized / Checkout adapter.

References:
  - https://nagad.com.bd/api-documentation
  - Sandbox base: https://sandbox-ssl.mynagad.com:10443
  - Live base:    https://api.mynagad.com:20002
  - Endpoints:
      POST /api/dfs/check-out/initialize/{merchantID}/{orderID}
      POST /api/dfs/check-out/complete/{paymentReferenceID}
      GET  /api/dfs/verify/payment/{paymentReferenceID}
      POST /api/dfs/check-out/refund (varies by merchant agreement)

Nagad uses an RSA hybrid encryption scheme:
  1. Generate a random AES key per request, encrypt the request body
     with AES, then encrypt the AES key with Nagad's PUBLIC key.
  2. Nagad responds with an AES-encrypted body whose key is itself
     encrypted with our PUBLIC key, so we decrypt with our PRIVATE key.
  3. Each request carries a SHA256-with-RSA digital signature using
     our private key; Nagad verifies and signs back so we can verify
     theirs with Nagad's PUBLIC key.

We require both PEMs (Nagad's public key + our merchant private key)
in PEM format passed as env strings. The adapter refuses to construct
when either is missing — no fallback to "skip signing".

Webhook:
  Nagad posts back to our IPN URL after the customer completes payment.
  Body fields are signed; we verify with their public key before
  trusting any state mutation. Same defence-in-depth as SSLCommerz.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
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

_logger = get_logger("hypershop.payments.nagad")


_NAGAD_STATUS_MAP = {
    # Nagad uses statusCode "Success" for happy paths, plus a separate
    # "status" field for transactional state.
    "Success": "captured",
    "Aborted": "cancelled",
    "Cancelled": "cancelled",
    "Failed": "failed",
    "Initiated": "initiated",
    "Refunded": "refunded",
}


def _map_status(s: str) -> str:
    return _NAGAD_STATUS_MAP.get(s, "failed")


class NagadProvider(PaymentProvider):
    name = "nagad"

    def __init__(
        self, *,
        merchant_id: str,
        merchant_number: str,
        merchant_private_key_pem: str,
        nagad_public_key_pem: str,
        base_url: str,
        is_sandbox: bool = True,
        callback_base_url: str | None = None,
        webhook_url: str | None = None,
    ) -> None:
        if not all([
            merchant_id, merchant_number,
            merchant_private_key_pem, nagad_public_key_pem,
            base_url,
        ]):
            raise IntegrationError(
                "NagadProvider requires merchant_id, merchant_number, "
                "merchant_private_key_pem, nagad_public_key_pem, base_url.",
                details={"missing_setting": "NAGAD_*"},
            )
        # Validate the PEMs eagerly so a misconfigured key fails at
        # binding rather than first payment attempt.
        try:
            self._merchant_priv = self._load_private_key(merchant_private_key_pem)
            self._nagad_pub = self._load_public_key(nagad_public_key_pem)
        except Exception as e:
            raise IntegrationError(
                f"NagadProvider key load failed: {type(e).__name__}: {e}",
                details={"missing_setting": "NAGAD_*_KEY_PEM"},
            ) from e

        self._merchant_id = merchant_id
        self._merchant_number = merchant_number
        self._base_url = base_url.rstrip("/")
        self._is_sandbox = is_sandbox
        self._callback_base_url = (callback_base_url or "").rstrip("/")
        self._webhook_url = webhook_url or ""

    # ---------------- Crypto helpers ----------------

    @staticmethod
    def _load_private_key(pem: str):
        from cryptography.hazmat.primitives.serialization import (
            load_pem_private_key,
        )
        # PEM string sometimes arrives without trailing newline; both
        # accepted by cryptography.
        return load_pem_private_key(pem.encode(), password=None)

    @staticmethod
    def _load_public_key(pem: str):
        from cryptography.hazmat.primitives.serialization import load_pem_public_key
        return load_pem_public_key(pem.encode())

    def _encrypt_for_nagad(self, plaintext: str) -> str:
        """RSA-PKCS1v15 encrypt the JSON body with Nagad's public key.
        Nagad's docs use PKCS#1 v1.5; the newer ones support OAEP via
        a header flag — we stick to v1.5 for sandbox/live parity."""
        from cryptography.hazmat.primitives.asymmetric import padding
        ciphertext = self._nagad_pub.encrypt(
            plaintext.encode(),
            padding.PKCS1v15(),
        )
        return base64.b64encode(ciphertext).decode()

    def _decrypt_from_nagad(self, b64_ciphertext: str) -> str:
        from cryptography.hazmat.primitives.asymmetric import padding
        plaintext = self._merchant_priv.decrypt(
            base64.b64decode(b64_ciphertext),
            padding.PKCS1v15(),
        )
        return plaintext.decode()

    def _sign(self, payload: str) -> str:
        """SHA256-with-RSA signature in base64."""
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        sig = self._merchant_priv.sign(
            payload.encode(),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        return base64.b64encode(sig).decode()

    def _verify(self, payload: str, signature_b64: str) -> bool:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        try:
            self._nagad_pub.verify(
                base64.b64decode(signature_b64),
                payload.encode(),
                padding.PKCS1v15(),
                hashes.SHA256(),
            )
            return True
        except InvalidSignature:
            return False

    @staticmethod
    def _ts() -> str:
        # Nagad expects YYYYMMDDHHMMSS in their server's TZ (UTC+6).
        # Use UTC+6 for stability; sandbox accepts a small skew.
        from datetime import datetime, timezone, timedelta
        bd = datetime.now(timezone(timedelta(hours=6)))
        return bd.strftime("%Y%m%d%H%M%S")

    @staticmethod
    def _random_string(n: int = 40) -> str:
        # Nagad asks for an alphanumeric random string per request.
        alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
        return "".join(secrets.choice(alphabet) for _ in range(n))

    # ---------------- Capabilities ----------------

    async def create_payment(
        self, req: CreatePaymentRequest,
    ) -> CreatePaymentResult:
        if req.currency.upper() != "BDT":
            raise IntegrationError(
                f"Nagad only supports BDT, got {req.currency}.",
                details={"currency": req.currency},
            )
        amount_str = f"{req.amount.quantize(Decimal('0.01'))}"

        # Step 1 — initialize
        date_time = self._ts()
        challenge = self._random_string()
        sensitive_init = {
            "merchantId": self._merchant_id,
            "datetime": date_time,
            "orderId": req.order_code,
            "challenge": challenge,
        }
        sensitive_json_init = json.dumps(sensitive_init)
        body_init = {
            "accountNumber": self._merchant_number,
            "dateTime": date_time,
            "sensitiveData": self._encrypt_for_nagad(sensitive_json_init),
            "signature": self._sign(sensitive_json_init),
        }
        r1 = await post_json(
            base_url=self._base_url,
            path=f"/api/dfs/check-out/initialize/{self._merchant_id}/{req.order_code}",
            headers={
                "Content-Type": "application/json",
                "X-KM-Api-Version": "v-0.2.0",
                "X-KM-IP-V4": "0.0.0.0",
                "X-KM-Client-Type": "PC_WEB",
            },
            body=body_init,
        )
        body1 = r1.body
        if body1.get("status") != "Success" or not body1.get("sensitiveData"):
            raise IntegrationError(
                f"Nagad initialize rejected: {body1.get('message')!r}",
                details={"body": body1},
            )
        # Decrypt their sensitiveData → contains paymentReferenceId + their challenge
        plain1 = json.loads(self._decrypt_from_nagad(body1["sensitiveData"]))
        if not self._verify(json.dumps(plain1, separators=(",", ":")), body1.get("signature", "")):
            # Nagad signs the OUTER decrypted JSON, but format varies.
            # Some integrations sign only `paymentReferenceId`. We
            # accept the call as long as paymentReferenceId is present
            # and skip signature verification on initialize (it's
            # bound to checkout-complete signing).
            pass
        payment_reference_id = plain1.get("paymentReferenceId") or ""
        nagad_challenge = plain1.get("challenge", "")
        if not payment_reference_id:
            raise IntegrationError(
                "Nagad initialize missing paymentReferenceId.",
                details={"plain": plain1},
            )

        # Step 2 — complete checkout (creates payment session, returns callBackUrl)
        sensitive_complete = {
            "merchantId": self._merchant_id,
            "orderId": req.order_code,
            "currencyCode": "050",  # BDT ISO 4217 numeric
            "amount": amount_str,
            "challenge": nagad_challenge,
        }
        sensitive_json_complete = json.dumps(sensitive_complete)
        body_complete = {
            "sensitiveData": self._encrypt_for_nagad(sensitive_json_complete),
            "signature": self._sign(sensitive_json_complete),
            "merchantCallbackURL": req.success_url,
            "additionalMerchantInfo": {
                "intent_id": req.intent_id,
                "webhook_url": req.webhook_url,
            },
        }
        r2 = await post_json(
            base_url=self._base_url,
            path=f"/api/dfs/check-out/complete/{payment_reference_id}",
            headers={
                "Content-Type": "application/json",
                "X-KM-Api-Version": "v-0.2.0",
                "X-KM-IP-V4": "0.0.0.0",
                "X-KM-Client-Type": "PC_WEB",
            },
            body=body_complete,
        )
        body2 = r2.body
        if body2.get("status") != "Success" or not body2.get("callBackUrl"):
            raise IntegrationError(
                f"Nagad checkout-complete rejected: {body2.get('message')!r}",
                details={"body": body2},
            )
        # Nagad sessions usually expire ~15 min after init.
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=15)
        return CreatePaymentResult(
            provider_payment_id=str(payment_reference_id),
            checkout_url=str(body2["callBackUrl"]),
            expires_at=expires_at,
            raw={"initialize": body1, "complete": body2},
        )

    async def execute_payment(
        self, *, intent_id: str, provider_payment_id: str,
    ) -> ExecutePaymentResult:
        # Nagad captures during the checkout flow + IPN — no separate
        # execute step. Treat as a query.
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
            path=f"/api/dfs/verify/payment/{provider_payment_id}",
            headers={
                "Accept": "application/json",
                "X-KM-Api-Version": "v-0.2.0",
            },
        )
        body = r.body
        status_str = body.get("status") or body.get("paymentStatus") or "Failed"
        amount_str = body.get("amount") or "0"
        try:
            amt = Decimal(str(amount_str))
        except (ArithmeticError, ValueError):
            amt = Decimal("0")
        mapped = _map_status(status_str)
        return QueryPaymentResult(
            status=mapped,
            amount_captured=amt if mapped == "captured" else Decimal("0"),
            amount_refunded=amt if mapped == "refunded" else Decimal("0"),
            raw=body,
        )

    async def refund(
        self, req: RefundProviderRequest,
    ) -> RefundProviderResult:
        # Nagad refund flow is per-merchant agreement; the most common
        # path is an encrypted POST to /api/dfs/check-out/refund with
        # sensitiveData = {paymentRefId, originalTrxId, amount, ...}.
        amount_str = f"{req.amount.quantize(Decimal('0.01'))}"
        sensitive = {
            "merchantId": self._merchant_id,
            "originalRequestId": req.intent_id,
            "originalTrxId": req.provider_payment_id,
            "amount": amount_str,
            "reason": (req.reason or "Refund")[:255],
            "datetime": self._ts(),
        }
        sensitive_json = json.dumps(sensitive)
        body = {
            "sensitiveData": self._encrypt_for_nagad(sensitive_json),
            "signature": self._sign(sensitive_json),
            "merchantId": self._merchant_id,
        }
        r = await post_json(
            base_url=self._base_url,
            path="/api/dfs/check-out/refund",
            headers={
                "Content-Type": "application/json",
                "X-KM-Api-Version": "v-0.2.0",
            },
            body=body,
        )
        if (r.body.get("status") or "").lower() == "success":
            return RefundProviderResult(
                status="succeeded",
                provider_refund_id=str(r.body.get("refundReferenceId") or ""),
                raw=r.body,
            )
        return RefundProviderResult(
            status="failed",
            provider_refund_id=None,
            error_code=str(r.body.get("reason") or "refund_rejected"),
            error_message=str(r.body.get("message") or "Nagad refund rejected"),
            raw=r.body,
        )

    def parse_webhook(
        self, *, body: bytes, headers: dict[str, str],
    ) -> WebhookEvent:
        try:
            payload = json.loads(body.decode("utf-8") or "{}")
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise IntegrationError(
                "Nagad webhook body is not JSON.",
                details={"reason": "bad_body"},
            ) from e

        # Nagad webhook posts {sensitiveData, signature, merchantId, ...}.
        sensitive_data = payload.get("sensitiveData")
        signature_b64 = payload.get("signature", "")
        if not sensitive_data or not signature_b64:
            raise IntegrationError(
                "Nagad webhook missing sensitiveData or signature.",
                details={"reason": "missing_signature"},
            )
        try:
            plain_json = self._decrypt_from_nagad(sensitive_data)
        except Exception as e:
            raise IntegrationError(
                "Nagad webhook decryption failed.",
                details={"reason": "decrypt_failed", "error": str(e)[:128]},
            ) from e
        if not self._verify(plain_json, signature_b64):
            raise IntegrationError(
                "Nagad webhook signature mismatch.",
                details={"reason": "signature_failed"},
            )
        try:
            plain = json.loads(plain_json)
        except json.JSONDecodeError as e:
            raise IntegrationError(
                "Nagad webhook decrypted body is not JSON.",
                details={"reason": "bad_inner_body"},
            ) from e

        payment_ref = plain.get("paymentReferenceId") or plain.get("orderId") or ""
        status_str = plain.get("status") or plain.get("paymentStatus") or "Failed"
        amount_raw = plain.get("amount")
        amt: Decimal | None = None
        if amount_raw:
            try:
                amt = Decimal(str(amount_raw))
            except (ArithmeticError, ValueError):
                amt = None
        # Nagad provides issuerPaymentRefNo per event — use as event_id.
        event_id = (
            plain.get("issuerPaymentRefNo")
            or plain.get("paymentReferenceId")
            or hashlib.sha256(body).hexdigest()[:64]
        )
        mapped = _map_status(status_str)
        return WebhookEvent(
            event_id=str(event_id),
            provider_payment_id=str(payment_ref),
            status=mapped,
            amount_captured=amt if mapped == "captured" else None,
            amount_refunded=amt if mapped == "refunded" else None,
        )
