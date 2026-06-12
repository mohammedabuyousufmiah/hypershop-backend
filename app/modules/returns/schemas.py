from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field

from app.core.validation import StrictModel


# ---------------- Customer request ----------------


class ReturnLineRequest(StrictModel):
    order_line_id: UUID
    quantity: int = Field(..., ge=1, le=10_000)


class CreateReturnRequest(StrictModel):
    order_id: UUID
    reason: str = Field(..., min_length=1, max_length=2048)
    lines: list[ReturnLineRequest] = Field(..., min_length=1, max_length=200)
    notes: str | None = Field(default=None, max_length=2048)


class CancelReturnRequest(StrictModel):
    reason: str = Field(..., min_length=1, max_length=512)


# ---------------- Admin transitions ----------------


class ReceiveRequest(StrictModel):
    notes: str | None = Field(default=None, max_length=2048)


class InspectLineRequest(StrictModel):
    line_id: UUID
    inspected_quantity: int = Field(..., ge=0, le=10_000)
    condition: str = Field(
        ..., pattern=r"^(sealed|opened|cold_chain_broken|expired)$",
    )
    target_batch_id: UUID
    target_warehouse_code: str = Field(default="MAIN", max_length=32)
    inspection_notes: str | None = Field(default=None, max_length=1024)


class InspectReturnRequest(StrictModel):
    lines: list[InspectLineRequest] = Field(..., min_length=1, max_length=200)


class RejectReturnRequest(StrictModel):
    reason: str = Field(..., min_length=1, max_length=512)


# ---------------- Responses ----------------


class ReturnLineResponse(StrictModel):
    id: UUID
    order_line_id: UUID
    variant_id: UUID
    product_name: str
    variant_sku: str
    requested_quantity: int
    inspected_quantity: int | None
    condition: str | None
    target_batch_id: UUID | None
    target_warehouse_id: UUID | None
    inspection_notes: str | None
    applied_action: str | None
    applied_at: datetime | None


class ReturnStatusEntry(StrictModel):
    from_status: str | None
    to_status: str
    transitioned_at: datetime
    transitioned_by: UUID | None
    reason: str | None


class ReturnRequestResponse(StrictModel):
    id: UUID
    code: str
    order_id: UUID
    customer_user_id: UUID
    status: str
    reason: str
    rejection_reason: str | None
    cancellation_reason: str | None
    notes: str | None
    requested_at: datetime
    requested_by: UUID | None
    received_at: datetime | None
    received_by: UUID | None
    inspected_at: datetime | None
    inspected_by: UUID | None
    completed_at: datetime | None
    completed_by: UUID | None
    cancelled_at: datetime | None
    lines: list[ReturnLineResponse]
    history: list[ReturnStatusEntry]
    created_at: datetime
    updated_at: datetime


class ReturnRequestSummary(StrictModel):
    id: UUID
    code: str
    order_id: UUID
    customer_user_id: UUID
    status: str
    requested_at: datetime
    line_count: int
