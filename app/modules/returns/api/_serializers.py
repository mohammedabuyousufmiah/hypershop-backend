from __future__ import annotations

from app.modules.returns.models import ReturnRequest
from app.modules.returns.schemas import (
    ReturnLineResponse,
    ReturnRequestResponse,
    ReturnRequestSummary,
    ReturnStatusEntry,
)


def to_response(rr: ReturnRequest) -> ReturnRequestResponse:
    return ReturnRequestResponse(
        id=rr.id,
        code=rr.code,
        order_id=rr.order_id,
        customer_user_id=rr.customer_user_id,
        status=rr.status,
        reason=rr.reason,
        rejection_reason=rr.rejection_reason,
        cancellation_reason=rr.cancellation_reason,
        notes=rr.notes,
        requested_at=rr.requested_at,
        requested_by=rr.requested_by,
        received_at=rr.received_at,
        received_by=rr.received_by,
        inspected_at=rr.inspected_at,
        inspected_by=rr.inspected_by,
        completed_at=rr.completed_at,
        completed_by=rr.completed_by,
        cancelled_at=rr.cancelled_at,
        lines=[
            ReturnLineResponse(
                id=li.id,
                order_line_id=li.order_line_id,
                variant_id=li.variant_id,
                product_name=li.product_name,
                variant_sku=li.variant_sku,
                requested_quantity=li.requested_quantity,
                inspected_quantity=li.inspected_quantity,
                condition=li.condition,
                target_batch_id=li.target_batch_id,
                target_warehouse_id=li.target_warehouse_id,
                inspection_notes=li.inspection_notes,
                applied_action=li.applied_action,
                applied_at=li.applied_at,
            )
            for li in rr.lines
        ],
        history=[
            ReturnStatusEntry(
                from_status=h.from_status,
                to_status=h.to_status,
                transitioned_at=h.transitioned_at,
                transitioned_by=h.transitioned_by,
                reason=h.reason,
            )
            for h in rr.history
        ],
        created_at=rr.created_at,
        updated_at=rr.updated_at,
    )


def to_summary(rr: ReturnRequest) -> ReturnRequestSummary:
    return ReturnRequestSummary(
        id=rr.id,
        code=rr.code,
        order_id=rr.order_id,
        customer_user_id=rr.customer_user_id,
        status=rr.status,
        requested_at=rr.requested_at,
        line_count=len(rr.lines),
    )
