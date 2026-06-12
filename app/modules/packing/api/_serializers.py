from __future__ import annotations

from app.modules.packing.models import PackingSession, PackingSessionLine
from app.modules.packing.schemas import (
    PackingLineResponse,
    PackingSessionResponse,
)


def line_to_response(li: PackingSessionLine) -> PackingLineResponse:
    return PackingLineResponse(
        id=li.id,
        order_line_id=li.order_line_id,
        variant_id=li.variant_id,
        expected_batch_id=li.expected_batch_id,
        expected_quantity=li.expected_quantity,
        scanned_quantity=li.scanned_quantity,
        status=li.status,
        accepted_batch_id=li.accepted_batch_id,
    )


def session_to_response(s: PackingSession) -> PackingSessionResponse:
    return PackingSessionResponse(
        id=s.id,
        order_id=s.order_id,
        status=s.status,
        opened_at=s.opened_at,
        opened_by=s.opened_by,
        completed_at=s.completed_at,
        completed_by=s.completed_by,
        cancelled_at=s.cancelled_at,
        cancellation_reason=s.cancellation_reason,
        notes=s.notes,
        lines=[line_to_response(li) for li in s.lines],
    )
