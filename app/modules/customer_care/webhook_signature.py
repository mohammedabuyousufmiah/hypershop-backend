"""Meta WhatsApp Cloud API webhook signature verification.

Meta signs every incoming POST with X-Hub-Signature-256 = sha256=<hex>
where the HMAC is computed over the raw request body using the WhatsApp
App Secret (NOT the verify token, NOT the access token).
"""
from __future__ import annotations

import hashlib
import hmac
import logging

from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)


def compute_signature(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


async def verify_meta_signature(request: Request, app_secret: str | None) -> bytes:
    body = await request.body()

    if not app_secret:
        if request.app.state.is_production:
            logger.error("webhook_signature_skipped_in_production: app_secret missing")
            raise HTTPException(status_code=503, detail="Webhook secret not configured")
        logger.warning("webhook_signature_skipped_in_dev: app_secret missing")
        return body

    sent_sig = request.headers.get("x-hub-signature-256") or request.headers.get(
        "X-Hub-Signature-256"
    )
    if not sent_sig:
        raise HTTPException(status_code=401, detail="Missing X-Hub-Signature-256")

    expected = compute_signature(app_secret, body)
    if not hmac.compare_digest(expected, sent_sig):
        logger.warning("webhook_signature_mismatch")
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    return body
