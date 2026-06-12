from __future__ import annotations

from app.modules.orders.models import Order
from app.modules.orders.schemas import (
    DeliveryAddress,
    OrderLineResponse,
    OrderResponse,
    OrderStatusEntry,
    OrderSummary,
)


def order_to_response(o: Order) -> OrderResponse:
    return OrderResponse(
        id=o.id,
        code=o.code,
        customer_user_id=o.customer_user_id,
        status=o.status,
        payment_method=o.payment_method,
        requires_prescription=o.requires_prescription,
        currency=o.currency,
        subtotal=o.subtotal,
        tax_total=o.tax_total,
        shipping_total=o.shipping_total,
        discount_total=o.discount_total,
        grand_total=o.grand_total,
        delivery_address=DeliveryAddress.model_validate(o.delivery_address),
        notes=o.notes,
        placed_at=o.placed_at,
        payment_confirmed_at=o.payment_confirmed_at,
        approved_at=o.approved_at,
        dispatched_at=o.dispatched_at,
        completed_at=o.completed_at,
        cancelled_at=o.cancelled_at,
        cancellation_reason=o.cancellation_reason,
        assigned_pharmacist_id=o.assigned_pharmacist_id,
        lines=[
            OrderLineResponse(
                id=line.id,
                variant_id=line.variant_id,
                product_name=line.product_name,
                variant_sku=line.variant_sku,
                requires_prescription=line.requires_prescription,
                quantity=line.quantity,
                unit_price=line.unit_price,
                line_total=line.line_total,
            )
            for line in o.lines
        ],
        history=[
            OrderStatusEntry(
                from_status=h.from_status,
                to_status=h.to_status,
                transitioned_at=h.transitioned_at,
                transitioned_by=h.transitioned_by,
                reason=h.reason,
            )
            for h in o.history
        ],
        created_at=o.created_at,
        updated_at=o.updated_at,
    )


def order_to_summary(o: Order) -> OrderSummary:
    return OrderSummary(
        id=o.id,
        code=o.code,
        status=o.status,
        payment_method=o.payment_method,
        requires_prescription=o.requires_prescription,
        grand_total=o.grand_total,
        currency=o.currency,
        placed_at=o.placed_at,
        customer_user_id=o.customer_user_id,
    )
