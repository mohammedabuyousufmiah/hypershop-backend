from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import Field, field_validator

from app.core.validation import StrictModel

_PHONE_RE = re.compile(r"^\+?[1-9]\d{6,18}$")


def _validate_phone(v: str) -> str:
    if not _PHONE_RE.match(v):
        raise ValueError("phone must be E.164, e.g. +8801911740672")
    return v


# ---------------- Riders ----------------


class RiderCreate(StrictModel):
    code: str | None = Field(default=None, max_length=32, pattern=r"^[A-Z0-9_\-]+$")
    name: str = Field(..., min_length=1, max_length=160)
    phone: str = Field(..., min_length=6, max_length=32)
    vehicle_type: str = Field(..., pattern=r"^(motorcycle|bicycle|van|other)$")
    license_number: str | None = Field(default=None, max_length=64)
    linked_user_id: UUID | None = None
    is_active: bool = True

    @field_validator("phone")
    @classmethod
    def _phone(cls, v: str) -> str:
        return _validate_phone(v)


class RiderUpdate(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    phone: str | None = Field(default=None, min_length=6, max_length=32)
    vehicle_type: str | None = Field(
        default=None, pattern=r"^(motorcycle|bicycle|van|other)$",
    )
    license_number: str | None = Field(default=None, max_length=64)
    linked_user_id: UUID | None = None
    is_active: bool | None = None
    current_status: str | None = Field(
        default=None, pattern=r"^(offline|available|busy)$",
    )

    @field_validator("phone")
    @classmethod
    def _phone(cls, v: str | None) -> str | None:
        return _validate_phone(v) if v else v


class RiderResponse(StrictModel):
    id: UUID
    code: str
    name: str
    phone: str
    vehicle_type: str
    license_number: str | None
    linked_user_id: UUID | None
    is_active: bool
    current_status: str
    created_at: datetime
    updated_at: datetime


# ---------------- Assignment ----------------


class AssignDeliveryRequest(StrictModel):
    order_id: UUID
    rider_id: UUID
    notes: str | None = Field(default=None, max_length=2048)


class CancelAssignmentRequest(StrictModel):
    reason: str = Field(..., min_length=1, max_length=512)


class FailAssignmentRequest(StrictModel):
    reason: str = Field(..., min_length=1, max_length=512)


class PickupRequest(StrictModel):
    notes: str | None = Field(default=None, max_length=2048)


class DeliverRequest(StrictModel):
    """Mark the package as delivered. POD evidence required.

    Provide at least one of:
    - ``pod_otp_verified`` — recipient confirmed via OTP / app
    - or upload a POD photo via the ``/upload-pod`` endpoint *before*
      calling deliver
    - and (always) ``recipient_name`` for the receipt log

    For COD orders, ``cod_collected`` is mandatory.
    """

    recipient_name: str = Field(..., min_length=1, max_length=160)
    pod_otp_verified: bool = False
    cod_collected: Decimal | None = Field(
        default=None, max_digits=14, decimal_places=2, ge=0,
    )
    notes: str | None = Field(default=None, max_length=2048)


class CodReconcileRequest(StrictModel):
    resolution_notes: str = Field(..., min_length=1, max_length=1024)


# ---------------- Responses ----------------


class DeliveryStatusEntry(StrictModel):
    from_status: str | None
    to_status: str
    transitioned_at: datetime
    transitioned_by: UUID | None
    reason: str | None


class DeliveryAssignmentResponse(StrictModel):
    id: UUID
    order_id: UUID
    rider_id: UUID
    status: str
    assigned_at: datetime
    assigned_by: UUID | None
    picked_up_at: datetime | None
    delivered_at: datetime | None
    completed_at: datetime | None
    cancelled_at: datetime | None
    cancellation_reason: str | None
    failed_at: datetime | None
    failure_reason: str | None
    payment_method: str
    cod_expected: Decimal
    cod_collected: Decimal | None
    cod_status: str
    cod_reconciled_at: datetime | None
    cod_reconciled_by: UUID | None
    cod_resolution_notes: str | None
    pod_photo_path: str | None
    pod_signature_path: str | None
    pod_otp_verified_at: datetime | None
    pod_recipient_name: str | None
    pod_notes: str | None
    history: list[DeliveryStatusEntry]
    created_at: datetime
    updated_at: datetime


class DeliveryAssignmentSummary(StrictModel):
    id: UUID
    order_id: UUID
    rider_id: UUID
    status: str
    payment_method: str
    cod_expected: Decimal
    cod_status: str
    assigned_at: datetime


# ---------------- Rider mobile app (Module 18) ----------------


class RiderAvailabilityRequest(StrictModel):
    status: str = Field(..., pattern=r"^(offline|available|busy)$")


class RiderScanVerifyRequest(StrictModel):
    scanned_code: str = Field(..., min_length=1, max_length=64)
    intent: str = Field(..., pattern=r"^(pickup|delivery)$")


class RiderScanVerifyResponse(StrictModel):
    ok: bool
    expected_code: str
    scanned_code: str
    assignment_status: str
    intent: str


class RiderTaskItem(StrictModel):
    """One row in the rider's "today" queue. A subset of the assignment
    fields the rider app actually needs on a list screen — keeps the
    payload tight on slow connections.
    """

    assignment_id: UUID
    order_id: UUID
    order_code: str
    status: str
    payment_method: str
    cod_expected: Decimal
    cod_collected: Decimal | None
    cod_status: str
    recipient_name: str | None
    recipient_phone: str | None
    address_line1: str | None
    city: str | None
    assigned_at: datetime
    picked_up_at: datetime | None
    delivered_at: datetime | None


class RiderTaskListResponse(StrictModel):
    rider_id: UUID
    items: list[RiderTaskItem]
    counts: dict[str, int]  # by status


class RiderCodSummaryResponse(StrictModel):
    rider_id: UUID
    expected_total: Decimal
    deposited_total: Decimal
    outstanding: Decimal
    today_collected_amount: Decimal
    today_collected_count: int
