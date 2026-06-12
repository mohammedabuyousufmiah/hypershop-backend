from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field

from app.core.validation import StrictModel


class OpenSessionRequest(StrictModel):
    notes: str | None = Field(default=None, max_length=2048)
    # Optional body-level order_id — allows clients that prefer to send
    # the order via JSON body instead of the ``?order_id=`` query string.
    # The endpoint accepts either; explicit query takes precedence.
    order_id: UUID | None = Field(default=None)


class ScanRequest(StrictModel):
    barcode: str = Field(..., min_length=1, max_length=64)
    batch_id: UUID
    notes: str | None = Field(default=None, max_length=512)


class OverrideScanRequest(StrictModel):
    """Same as :class:`ScanRequest` — the calling user is the supervisor.

    The endpoint requires a different permission so RBAC alone gates the
    override capability; we don't need an extra credential field here.
    """

    barcode: str = Field(..., min_length=1, max_length=64)
    batch_id: UUID
    line_id: UUID
    reason: str = Field(..., min_length=1, max_length=255)


class CancelSessionRequest(StrictModel):
    reason: str = Field(..., min_length=1, max_length=512)


# ---------------- Responses ----------------


class PackingScanResponse(StrictModel):
    id: UUID
    session_id: UUID
    session_line_id: UUID | None
    scanned_barcode: str
    matched_variant_id: UUID | None
    scanned_batch_id: UUID | None
    outcome: str
    is_supervisor_override: bool
    supervisor_user_id: UUID | None
    scanned_by: UUID | None
    scanned_at: datetime
    notes: str | None


class PackingLineResponse(StrictModel):
    id: UUID
    order_line_id: UUID
    variant_id: UUID
    expected_batch_id: UUID
    expected_quantity: int
    scanned_quantity: int
    status: str
    accepted_batch_id: UUID | None


class PackingSessionResponse(StrictModel):
    id: UUID
    order_id: UUID
    status: str
    opened_at: datetime
    opened_by: UUID | None
    completed_at: datetime | None
    completed_by: UUID | None
    cancelled_at: datetime | None
    cancellation_reason: str | None
    notes: str | None
    lines: list[PackingLineResponse]


class ScanResultResponse(StrictModel):
    """Returned to the picker after a scan attempt — tells them whether to
    proceed, escalate, or grab a different unit.
    """

    outcome: str
    accepted: bool
    line_id: UUID | None
    line_status: str | None
    line_scanned_quantity: int | None
    line_expected_quantity: int | None
    session_status: str
    session_completed: bool
    can_supervisor_override: bool
    message: str
