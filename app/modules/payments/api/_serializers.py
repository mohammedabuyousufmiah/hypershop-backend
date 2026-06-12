"""Plain ORM → response-DTO mappers used by every router in this module."""

from __future__ import annotations

from app.modules.payments.models import PaymentIntent, PaymentRefund
from app.modules.payments.schemas import (
    PaymentIntentResponse,
    RefundResponse,
)


def intent_to_response(i: PaymentIntent) -> PaymentIntentResponse:
    return PaymentIntentResponse(
        id=i.id,
        order_id=i.order_id,
        provider=i.provider,
        provider_payment_id=i.provider_payment_id,
        status=i.status,
        currency=i.currency,
        amount=i.amount,
        amount_captured=i.amount_captured,
        amount_refunded=i.amount_refunded,
        checkout_url=i.checkout_url,
        initiated_at=i.initiated_at,
        authorized_at=i.authorized_at,
        captured_at=i.captured_at,
        failed_at=i.failed_at,
        cancelled_at=i.cancelled_at,
        expires_at=i.expires_at,
        last_error_code=i.last_error_code,
        last_error_message=i.last_error_message,
    )


def refund_to_response(r: PaymentRefund) -> RefundResponse:
    return RefundResponse(
        id=r.id,
        intent_id=r.intent_id,
        amount=r.amount,
        currency=r.currency,
        status=r.status,
        reason=r.reason,
        provider_refund_id=r.provider_refund_id,
        succeeded_at=r.succeeded_at,
        error_code=r.error_code,
        error_message=r.error_message,
        created_at=r.created_at,
    )
