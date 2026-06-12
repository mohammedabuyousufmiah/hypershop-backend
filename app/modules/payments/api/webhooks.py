"""Gateway webhook endpoints — UNAUTHENTICATED, signature-verified.

These routes are open to the internet because the gateway is the
caller (Bkash / SSLCommerz / Nagad / Rocket servers POST here). The
adapter's ``parse_webhook`` does signature verification BEFORE we
trust any body field. The service layer's ``ingest_webhook`` makes
ingestion idempotent on (provider, event_id) so duplicate deliveries
are safe.

Reverse-proxy / firewall note:
  These endpoints accept any source IP. If you want IP allow-listing,
  do it at the LB / Caddy layer. Path is stable:
      /api/v1/payments/webhooks/bkash
      /api/v1/payments/webhooks/sslcommerz
      /api/v1/payments/webhooks/nagad
      /api/v1/payments/webhooks/rocket
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.core.db.uow import UnitOfWork, get_uow
from app.modules.payments.codes import ALL_PROVIDERS
from app.modules.payments.schemas import WebhookIngestResponse
from app.modules.payments.service import PaymentService

router = APIRouter(prefix="/payments/webhooks", tags=["payments-webhooks"])


async def _ingest(
    *,
    provider_name: str,
    request: Request,
    uow: UnitOfWork,
) -> WebhookIngestResponse:
    body = await request.body()
    headers = {k.lower(): v for k, v in request.headers.items()}
    ip = request.client.host if request.client else None
    async with uow.transactional() as session:
        svc = PaymentService(session)
        wb, resolution = await svc.ingest_webhook(
            provider_name=provider_name,
            body=body,
            headers=headers,
            ip_address=ip,
        )
        # Ad wallet recharge dispatch — runs in the same transaction so
        # the credit is rolled back if anything below fails. Idempotent
        # on the bkash provider_payment_id (see ads.service).
        if resolution in ("processed", "duplicate") and provider_name == "bkash":
            try:
                from app.modules.payments.providers import get_provider
                event = get_provider(provider_name).parse_webhook(
                    body=body, headers=headers,
                )
                from app.modules.ads import service as ads_service
                await ads_service.handle_recharge_webhook(
                    session,
                    payment_id=event.provider_payment_id,
                    status=event.status,
                    amount_minor=(
                        int(event.amount_captured * 100)
                        if event.amount_captured is not None else None
                    ),
                    raw_payload=None,
                )
            except Exception:  # noqa: BLE001
                # Never let an ad-side dispatch failure roll back the
                # primary payment ingest; the wallet credit can be
                # backfilled by replaying the webhook.
                pass
    return WebhookIngestResponse(
        received=True,
        resolution=resolution,
        intent_id=wb.intent_id,
    )


@router.post(
    "/bkash",
    response_model=WebhookIngestResponse,
    summary="Bkash IPN webhook",
    include_in_schema=True,  # advertise in OpenAPI for ops debugging
)
async def webhook_bkash(
    request: Request,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> WebhookIngestResponse:
    return await _ingest(provider_name="bkash", request=request, uow=uow)


@router.post(
    "/sslcommerz",
    response_model=WebhookIngestResponse,
    summary="SSLCommerz IPN webhook (form-encoded)",
)
async def webhook_sslcommerz(
    request: Request,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> WebhookIngestResponse:
    return await _ingest(provider_name="sslcommerz", request=request, uow=uow)


@router.post(
    "/nagad",
    response_model=WebhookIngestResponse,
    summary="Nagad IPN webhook",
)
async def webhook_nagad(
    request: Request,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> WebhookIngestResponse:
    return await _ingest(provider_name="nagad", request=request, uow=uow)


@router.post(
    "/rocket",
    response_model=WebhookIngestResponse,
    summary="Rocket IPN webhook (HMAC-SHA256 signed)",
)
async def webhook_rocket(
    request: Request,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> WebhookIngestResponse:
    return await _ingest(provider_name="rocket", request=request, uow=uow)


# Dev-mode fake provider webhook (added 2026-05-13).
#
# Mirrors the bkash/sslcommerz/nagad/rocket handlers but for the
# in-process FakePaymentProvider. The fake provider doesn't sign its
# callbacks (nothing to sign — it's a dev shim), so this endpoint
# accepts the self-posted JSON from ``/checkout/fake-pay/page.tsx``
# and runs it through the same ``_ingest`` path as the real providers.
# That means order state transitions (PENDING → PAID), inventory
# decrement, audit log, and outbox-triggered downstream events all
# happen identically to a real Bkash capture — only the gateway side
# is faked.
#
# In production this endpoint stays mounted but the factory refuses
# to bind the fake adapter (``ENVIRONMENT=production`` guard), so any
# inbound POST returns an audit-logged "provider not bound" error.
@router.post(
    "/fake",
    response_model=WebhookIngestResponse,
    summary="Dev-fake provider webhook (dev/CI only — never real money)",
)
async def webhook_fake(
    request: Request,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> WebhookIngestResponse:
    return await _ingest(provider_name="fake", request=request, uow=uow)


# Hint to the operator: these are the URLs you put in each gateway's
# dashboard as the IPN / webhook target.
_ = ALL_PROVIDERS
