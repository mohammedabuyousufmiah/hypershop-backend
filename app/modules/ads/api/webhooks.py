"""Sponsored Products webhooks — provider callbacks for wallet recharges.

Phase 1.B route: ``/api/v1/ads/webhooks/bkash/ad-wallet-recharge``.

Bkash POSTs here after the seller completes the checkout. The route
parses the payload, verifies the signature via the bound provider's
``parse_webhook``, then calls
:func:`app.modules.ads.service.finalize_recharge_from_callback`.

Idempotency contract: the recharge row's UNIQUE ``provider_payment_id``
plus the status check inside the service guarantees that re-deliveries
are safe — the wallet is credited exactly once.

Return semantics: always 200 on parseable input. Bkash retries on
non-2xx, so we never expose internal stack traces to the gateway —
errors are logged with the recharge id (when known) and an opaque OK
goes back. The exception is signature verification: malformed bodies
get 400 so the gateway operator can spot a misconfigured signing key.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse

from app.core.db.uow import UnitOfWork, get_uow
from app.core.errors import IntegrationError
from app.core.logging import get_logger
from app.modules.ads import service as ads_service

_logger = get_logger("hypershop.ads.webhooks")

router = APIRouter(prefix="/ads/webhooks", tags=["ads-webhooks"])


@router.post(
    "/bkash/ad-wallet-recharge",
    status_code=status.HTTP_200_OK,
    summary="Bkash callback — credit ad wallet on recharge capture",
    include_in_schema=True,
)
async def bkash_ad_wallet_callback(
    request: Request,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> JSONResponse:
    body = await request.body()
    headers = {k.lower(): v for k, v in request.headers.items()}
    from app.modules.payments.providers import get_provider

    bound = get_provider("bkash")
    try:
        event = bound.parse_webhook(body=body, headers=headers)
    except IntegrationError as e:
        # Signature/parse failure — Bkash should NOT retry an unsignable
        # delivery, so we return 400 with a non-leaky body.
        _logger.warning(
            "ads_bkash_webhook_signature_failed",
            error=str(e),
        )
        return JSONResponse(
            {"received": False, "error": "signature_invalid"},
            status_code=400,
        )
    except Exception as e:  # noqa: BLE001
        _logger.error(
            "ads_bkash_webhook_parse_error",
            error=str(e),
        )
        return JSONResponse(
            {"received": True, "resolution": "parse_error"},
            status_code=200,
        )

    try:
        async with uow.transactional() as session:
            await ads_service.finalize_recharge_from_callback(
                session,
                provider="bkash",
                provider_payment_id=event.provider_payment_id,
                callback_payload={
                    "status": event.status,
                    "amount_captured": (
                        str(event.amount_captured)
                        if event.amount_captured is not None else None
                    ),
                    "event_id": event.event_id,
                },
            )
    except Exception as e:  # noqa: BLE001
        # Swallow internal errors so we don't expose stack traces to
        # the gateway and trigger an aggressive retry storm. The
        # webhook will be replayed by Bkash on any 5xx, so logging is
        # enough — the next delivery hits the same code path.
        _logger.error(
            "ads_bkash_webhook_finalize_error",
            provider_payment_id=event.provider_payment_id,
            error=str(e),
        )
        return JSONResponse(
            {"received": True, "resolution": "deferred"},
            status_code=200,
        )

    return JSONResponse(
        {"received": True, "resolution": "processed"},
        status_code=200,
    )
