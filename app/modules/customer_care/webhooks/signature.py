"""Generic HMAC-SHA256 signature verifier for non-Meta webhooks.

Header format: `X-Webhook-Signature: sha256=<hex>`
Computed over the raw request body using the per-endpoint secret.

A separate timestamp header (`X-Webhook-Timestamp`) is required and validated
against a 5-minute skew window to prevent replay attacks.

Production refuses requests without a configured secret. In dev mode the
verifier logs and lets unsigned requests through (so load testing the parser
+ queue path works without crypto overhead) — set `WEBHOOK_REQUIRE_SIGNATURE=true`
to override and force signature checks in dev.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import time

from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)

MAX_SKEW_SECONDS = 300


def compute_signature(secret: str, timestamp: str, body: bytes) -> str:
    payload = timestamp.encode() + b"." + body
    return "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()


async def verify_webhook_signature(
    request: Request,
    secret: str | None,
    *,
    require_in_dev: bool = False,
) -> bytes:
    """Verify the webhook and return the raw body bytes."""
    body = await request.body()
    is_production = bool(getattr(request.app.state, "is_production", False))

    if not secret:
        if is_production:
            logger.error("webhook_signature_secret_missing_in_production path=%s", request.url.path)
            raise HTTPException(status_code=503, detail="Webhook secret not configured")
        if require_in_dev:
            raise HTTPException(status_code=503, detail="Webhook secret not configured")
        return body  # dev-mode no-secret pass-through

    sent_sig = (
        request.headers.get("x-webhook-signature")
        or request.headers.get("X-Webhook-Signature")
    )
    sent_ts = (
        request.headers.get("x-webhook-timestamp")
        or request.headers.get("X-Webhook-Timestamp")
    )
    if not sent_sig or not sent_ts:
        raise HTTPException(status_code=401, detail="Missing webhook signature/timestamp")

    try:
        ts_int = int(sent_ts)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid timestamp") from exc

    skew = abs(int(time.time()) - ts_int)
    if skew > MAX_SKEW_SECONDS:
        raise HTTPException(status_code=401, detail=f"Timestamp out of range (skew {skew}s)")

    expected = compute_signature(secret, sent_ts, body)
    if not hmac.compare_digest(expected, sent_sig):
        logger.warning("webhook_signature_mismatch path=%s", request.url.path)
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    return body
