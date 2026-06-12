from __future__ import annotations

from app.modules.deliveries.models import DeliveryAssignment, Rider
from app.modules.deliveries.schemas import (
    DeliveryAssignmentResponse,
    DeliveryAssignmentSummary,
    DeliveryStatusEntry,
    RiderResponse,
)


def rider_to_response(r: Rider) -> RiderResponse:
    return RiderResponse.model_validate(r)


def assignment_to_response(a: DeliveryAssignment) -> DeliveryAssignmentResponse:
    return DeliveryAssignmentResponse(
        id=a.id,
        order_id=a.order_id,
        rider_id=a.rider_id,
        status=a.status,
        assigned_at=a.assigned_at,
        assigned_by=a.assigned_by,
        picked_up_at=a.picked_up_at,
        delivered_at=a.delivered_at,
        completed_at=a.completed_at,
        cancelled_at=a.cancelled_at,
        cancellation_reason=a.cancellation_reason,
        failed_at=a.failed_at,
        failure_reason=a.failure_reason,
        payment_method=a.payment_method,
        cod_expected=a.cod_expected,
        cod_collected=a.cod_collected,
        cod_status=a.cod_status,
        cod_reconciled_at=a.cod_reconciled_at,
        cod_reconciled_by=a.cod_reconciled_by,
        cod_resolution_notes=a.cod_resolution_notes,
        pod_photo_path=a.pod_photo_path,
        pod_signature_path=a.pod_signature_path,
        pod_otp_verified_at=a.pod_otp_verified_at,
        pod_recipient_name=a.pod_recipient_name,
        pod_notes=a.pod_notes,
        history=[
            DeliveryStatusEntry(
                from_status=h.from_status,
                to_status=h.to_status,
                transitioned_at=h.transitioned_at,
                transitioned_by=h.transitioned_by,
                reason=h.reason,
            )
            for h in a.history
        ],
        created_at=a.created_at,
        updated_at=a.updated_at,
    )


def assignment_to_summary(a: DeliveryAssignment) -> DeliveryAssignmentSummary:
    return DeliveryAssignmentSummary(
        id=a.id,
        order_id=a.order_id,
        rider_id=a.rider_id,
        status=a.status,
        payment_method=a.payment_method,
        cod_expected=a.cod_expected,
        cod_status=a.cod_status,
        assigned_at=a.assigned_at,
    )
