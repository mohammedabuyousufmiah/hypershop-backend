"""WhatsApp Cloud API webhook endpoints — UNAUTHENTICATED, signed.

Routes:
  GET  /api/v1/whatsapp/webhook   — Meta verification handshake
  POST /api/v1/whatsapp/webhook   — status receipts (delivered/read/failed)

Both UNAUTHENTICATED because Meta is the caller. Security model:
  - GET handshake: ``hub.verify_token`` must equal env's
    ``META_WHATSAPP_VERIFY_TOKEN``. Constant-time compare.
  - POST receipts: ``X-Hub-Signature-256: sha256=<hex>`` must equal
    HMAC-SHA256 of the raw request body using the App Secret. Verified
    BEFORE any field is read from the body.

Setup in Meta dashboard:
  1. business.facebook.com → WhatsApp → Configuration → Webhooks
  2. Callback URL: https://api.yourdomain.com/api/v1/whatsapp/webhook
  3. Verify token: paste the same value you set in
     ``META_WHATSAPP_VERIFY_TOKEN``.
  4. Subscribe to ``messages`` field (covers status updates).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import PlainTextResponse, Response

from app.core.config import get_settings
from app.core.db.uow import UnitOfWork, get_uow
from app.core.errors import ForbiddenError
from app.core.logging import get_logger
from app.modules.whatsapp_webhook.service import (
    ingest,
    verify_signature,
    verify_subscription,
)

router = APIRouter(prefix="/whatsapp/webhook", tags=["whatsapp-webhook"])

_logger = get_logger("hypershop.whatsapp_webhook.api")


def _secret(value: object) -> str:
    if value is None:
        return ""
    get = getattr(value, "get_secret_value", None)
    if callable(get):
        return str(get() or "")
    return str(value)


@router.get(
    "",
    response_class=PlainTextResponse,
    include_in_schema=True,
    summary="Meta WhatsApp webhook verification handshake",
)
async def webhook_verify(
    hub_mode: Annotated[str | None, Query(alias="hub.mode")] = None,
    hub_verify_token: Annotated[str | None, Query(alias="hub.verify_token")] = None,
    hub_challenge: Annotated[str | None, Query(alias="hub.challenge")] = None,
) -> PlainTextResponse:
    cfg = get_settings()
    expected = _secret(getattr(cfg, "meta_whatsapp_verify_token", None))
    challenge = verify_subscription(
        expected_token=expected,
        mode=hub_mode,
        token=hub_verify_token,
        challenge=hub_challenge,
    )
    if challenge is None:
        # Meta expects 403 on bad token (any non-200 = handshake failed).
        raise ForbiddenError(
            "Webhook verification failed.",
            details={"reason": "verify_token_mismatch_or_bad_mode"},
        )
    _logger.info("whatsapp_webhook_verified")
    # Echo the challenge back as plaintext.
    return PlainTextResponse(challenge, status_code=200)


@router.post(
    "",
    summary="WhatsApp delivery-status receipts (signed)",
    description=(
        "Meta posts status updates here. We verify the SHA256 signature "
        "with the App Secret BEFORE trusting any field. Each "
        "(wamid, status) pair is upserted idempotently — Meta's retries "
        "on transient issues do not duplicate rows."
    ),
)
async def webhook_ingest(
    request: Request,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> Response:
    cfg = get_settings()
    app_secret = _secret(getattr(cfg, "meta_whatsapp_app_secret", None))
    body = await request.body()
    sig = request.headers.get("x-hub-signature-256") or request.headers.get(
        "X-Hub-Signature-256",
    )
    if not verify_signature(
        app_secret=app_secret,
        body_bytes=body,
        header_value=sig,
    ):
        _logger.warning(
            "whatsapp_webhook_signature_failed",
            ip=request.client.host if request.client else None,
            has_secret=bool(app_secret),
            has_sig_header=bool(sig),
        )
        # Return 401 with no body — Meta will retry. Avoid 5xx so it
        # doesn't trigger their alarm-on-error tooling.
        return Response(status_code=401)

    async with uow.transactional() as session:
        outcome = await ingest(session=session, body_bytes=body)

    # Always 200 once the signature passes — even if every status object
    # was a duplicate or unparseable. Meta only cares that we received it.
    return Response(status_code=200)
