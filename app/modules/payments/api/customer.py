"""Customer-facing payment endpoints.

The frontend flow:
  1. Customer places an order with payment_method='online' →
     ``POST /api/v1/orders/...`` returns an order in ``pending_payment``.
  2. Customer picks a gateway on the checkout screen → frontend calls
     ``POST /api/v1/payments/initiate`` with the order_id + provider.
  3. Backend returns ``checkout_url`` → frontend redirects browser.
  4. Customer completes payment at gateway → bounces back to success_url.
  5. Frontend polls ``GET /api/v1/payments/{intent_id}`` for the final
     status (or just trusts the redirect URL the gateway lands them at).
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from app.core.db.uow import UnitOfWork, get_uow
from app.core.security.deps import get_current_principal
from app.core.security.principal import Principal
from app.core.security.rbac import requires_permission
from app.modules.payments.api._serializers import intent_to_response
from app.modules.payments.schemas import (
    InitiatePaymentRequest,
    InitiatePaymentResponse,
    PaymentIntentResponse,
)
from app.modules.payments.service import PaymentService

router = APIRouter(prefix="/payments", tags=["payments"])

# Customers initiate payment for THEIR order. Reuses the same self-update
# permission used elsewhere for customer-owned mutations.
_CUSTOMER = "iam.user.update.self"


def _resolve_urls(
    payload: InitiatePaymentRequest,
) -> tuple[str, str, str, str]:
    """Compute (success_url, failure_url, cancel_url, webhook_url).

    Falls back to env-configured defaults when the customer didn't pass
    overrides. Webhook URL ALWAYS comes from env (never customer-supplied)
    so a malicious caller can't divert webhook traffic to themselves.
    """
    from app.core.config import get_settings
    s = get_settings()
    base = (
        getattr(s, "payment_default_redirect_base_url", None)
        or "http://localhost:8000"
    ).rstrip("/")
    webhook_base = (
        getattr(s, "payment_webhook_base_url", None) or base
    ).rstrip("/")
    success = payload.success_url or f"{base}/checkout/success"
    failure = payload.failure_url or f"{base}/checkout/failure"
    cancel = payload.cancel_url or f"{base}/checkout/cancel"
    # Webhook path matches the routes in webhooks.py
    webhook = f"{webhook_base}/api/v1/payments/webhooks/{payload.provider}"
    return success, failure, cancel, webhook


@router.post(
    "/initiate",
    response_model=InitiatePaymentResponse,
    summary="Initiate an online payment for an order",
    description=(
        "Customer picks an online payment method (bkash | sslcommerz | "
        "nagad | rocket). Backend creates a PaymentIntent, calls the "
        "gateway's create-checkout API, and returns the URL the browser "
        "should redirect to. Idempotent — re-calling for the same order "
        "while a previous intent is still ``initiated`` returns the same "
        "checkout URL instead of creating a duplicate intent."
    ),
    dependencies=[Depends(requires_permission(_CUSTOMER))],
)
async def initiate_payment(
    payload: InitiatePaymentRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> InitiatePaymentResponse:
    success_url, failure_url, cancel_url, webhook_url = _resolve_urls(payload)
    async with uow.transactional() as session:
        svc = PaymentService(session)
        intent = await svc.initiate(
            principal=principal,
            order_id=payload.order_id,
            provider_name=payload.provider,
            success_url=success_url,
            failure_url=failure_url,
            cancel_url=cancel_url,
            webhook_url=webhook_url,
        )
    return InitiatePaymentResponse(
        intent_id=intent.id,
        provider=intent.provider,
        status=intent.status,
        checkout_url=intent.checkout_url or "",
        expires_at=intent.expires_at,
    )


@router.get(
    "/{intent_id}",
    response_model=PaymentIntentResponse,
    summary="Get a payment intent (only the customer who owns it)",
    dependencies=[Depends(requires_permission(_CUSTOMER))],
)
async def get_intent(
    intent_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> PaymentIntentResponse:
    async with uow.transactional() as session:
        svc = PaymentService(session)
        intent = await svc.get_intent_for_customer(
            principal=principal, intent_id=intent_id,
        )
    return intent_to_response(intent)
