"""Admin rider-KYC review queue — GET /admin/rider-kyc.

Self-contained gap router that backs the admin-panel
``AdminRiderKycClient`` component. The rider mobile compat router
(``app.modules.rider_kyc.api``) already exposes the rider-facing
self-serve KYC endpoints; the admin *review queue* was explicitly
left TBD there. This file fills that gap.

Wire shape (what the FE's ``apiFetch`` reads as ``.data``)::

    GET /api/v1/admin/rider-kyc?status=pending  ->  {"items": [...], "total": N}

Each item matches the ``Kyc`` type in AdminRiderKycClient.tsx exactly.

Register centrally (do NOT edit here)::

    from app.modules.rider_kyc.admin_gap import router as rider_kyc_admin_gap_router
    app.include_router(rider_kyc_admin_gap_router, prefix=settings.api_prefix)

Defensive: any DB/table/column error returns an empty page instead of
500, so the FE renders an empty state rather than breaking.
"""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text

from app.core.db.uow import UnitOfWork, get_uow
from app.core.logging import get_logger
from app.core.security.rbac import requires_permission
from app.modules.iam.permissions import P_DASHBOARD_READ

_log = get_logger("hypershop.rider_kyc.admin")

router = APIRouter(prefix="/admin/rider-kyc", tags=["admin-rider-kyc"])

_ALLOWED_STATUS = {"pending", "verified", "rejected"}

# Raw SQL so the file imports cleanly even if the ORM model ever moves.
# Column list matches the FE `Kyc` type field-for-field.
_SELECT_SQL = """
    SELECT
        id, user_id, full_name, profile_photo_url, nid_number,
        nid_front_url, nid_back_url, mobile_primary, mobile_secondary,
        house_no, flat_no, area, lat, lng,
        reference_name, reference_nid, reference_mobile,
        status, rejection_reason, submitted_at
    FROM rider_kyc_submissions
    WHERE status = :status
    ORDER BY submitted_at DESC NULLS LAST, created_at DESC
    LIMIT :limit OFFSET :offset
"""

_COUNT_SQL = "SELECT count(*) FROM rider_kyc_submissions WHERE status = :status"


def _row_to_item(row: Any) -> dict[str, Any]:
    m = row._mapping  # SQLAlchemy Row -> dict-like
    submitted = m.get("submitted_at")
    return {
        "id": str(m["id"]),
        "user_id": str(m["user_id"]),
        "full_name": m.get("full_name") or "",
        "profile_photo_url": m.get("profile_photo_url"),
        "nid_number": m.get("nid_number"),
        "nid_front_url": m.get("nid_front_url"),
        "nid_back_url": m.get("nid_back_url"),
        "mobile_primary": m.get("mobile_primary") or "",
        "mobile_secondary": m.get("mobile_secondary"),
        "house_no": m.get("house_no"),
        "flat_no": m.get("flat_no"),
        "area": m.get("area"),
        "lat": m.get("lat"),
        "lng": m.get("lng"),
        "reference_name": m.get("reference_name"),
        "reference_nid": m.get("reference_nid"),
        "reference_mobile": m.get("reference_mobile"),
        "status": m.get("status") or "pending",
        "rejection_reason": m.get("rejection_reason"),
        "submitted_at": submitted.isoformat() if submitted is not None else None,
    }


@router.get(
    "",
    summary="Admin rider-KYC review queue (filtered by status)",
    dependencies=[Depends(requires_permission(P_DASHBOARD_READ))],
)
async def list_rider_kyc(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    status: Annotated[str, Query()] = "pending",
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    """Return ``{"items": [...], "total": N}`` for the given status tab.

    Never raises on a missing table/column — returns an empty page so the
    FE connects and renders an empty state instead of erroring.
    """
    st = status if status in _ALLOWED_STATUS else "pending"
    params = {"status": st, "limit": limit, "offset": offset}
    try:
        async with uow.transactional() as session:
            rows = (await session.execute(text(_SELECT_SQL), params)).all()
            total = (
                await session.execute(text(_COUNT_SQL), {"status": st})
            ).scalar_one_or_none() or 0
        items = [_row_to_item(r) for r in rows]
        return {"items": items, "total": int(total)}
    except Exception as e:  # noqa: BLE001 — never 500 the admin queue
        _log.warning("rider_kyc_admin_list_failed", error=str(e), status=st)
        return {"items": [], "total": 0}


__all__ = ["router"]
