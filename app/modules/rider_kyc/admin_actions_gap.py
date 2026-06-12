"""Admin rider-KYC review actions — POST /admin/rider-kyc/{id}/{verify,reject}.

Companion to ``app.modules.rider_kyc.admin_gap`` (the GET review-queue gap
router). That file lists submissions; this file lets the admin actually act
on a row — exactly the two mutations the ``AdminRiderKycClient`` component
fires:

    POST /api/v1/admin/rider-kyc/{id}/verify            -> {"ok": true, ...row}
    POST /api/v1/admin/rider-kyc/{id}/reject  {reason}  -> {"ok": true, ...row}

Same prefix and same RBAC gate (``P_DASHBOARD_READ``) as the GET gap router,
so it slots in alongside it without touching any existing file.

Register centrally (do NOT edit here)::

    from app.modules.rider_kyc.admin_actions_gap import (
        router as rider_kyc_admin_actions_gap_router,
    )
    app.include_router(rider_kyc_admin_actions_gap_router, prefix=settings.api_prefix)

Defensive: writes go through raw ``text()`` SQL against the real
``rider_kyc_submissions`` table inside an explicit transaction. If the table
or a column is absent, the endpoint returns a clean
``200 {"ok": false, "reason": "not_available"}`` rather than a 500. A missing
row returns ``404``.
"""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from app.core.db.uow import UnitOfWork, get_uow
from app.core.logging import get_logger
from app.core.security.rbac import requires_permission
from app.modules.iam.permissions import P_DASHBOARD_READ

_log = get_logger("hypershop.rider_kyc.admin.actions")

router = APIRouter(prefix="/admin/rider-kyc", tags=["admin-rider-kyc-actions"])

# Column list mirrors the GET gap router's SELECT so the FE receives the same
# `Kyc` shape after a mutation and can re-render the row in place.
_RETURN_COLS = """
    id, user_id, full_name, profile_photo_url, nid_number,
    nid_front_url, nid_back_url, mobile_primary, mobile_secondary,
    house_no, flat_no, area, lat, lng,
    reference_name, reference_nid, reference_mobile,
    status, rejection_reason, submitted_at
"""

_VERIFY_SQL = f"""
    UPDATE rider_kyc_submissions
       SET status = 'verified',
           rejection_reason = NULL
     WHERE id = :id
 RETURNING {_RETURN_COLS}
"""

_REJECT_SQL = f"""
    UPDATE rider_kyc_submissions
       SET status = 'rejected',
           rejection_reason = :reason
     WHERE id = :id
 RETURNING {_RETURN_COLS}
"""

# Re-used to detect a missing-table/column situation cleanly (never 500).
_MISSING_MARKERS = (
    "undefinedtable",
    "undefinedcolumn",
    "does not exist",
    "no such table",
    "no such column",
    "relation",
    "unknown column",
)


class RejectBody(BaseModel):
    reason: str = ""


def _row_to_item(row: Any) -> dict[str, Any]:
    m = row._mapping  # SQLAlchemy Row -> dict-like
    submitted = m.get("submitted_at")
    return {
        "ok": True,
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


def _is_missing_table(err: Exception) -> bool:
    msg = str(err).lower()
    return any(marker in msg for marker in _MISSING_MARKERS)


async def _apply(
    uow: UnitOfWork,
    sql: str,
    params: dict[str, Any],
    *,
    op: str,
) -> dict[str, Any]:
    """Run a single-row UPDATE in its own transaction.

    Returns the updated row dict, or raises 404 when no row matched. Any
    missing-table/column error degrades to ``{"ok": false, "reason":
    "not_available"}`` with HTTP 200.
    """
    try:
        async with uow.transactional() as session:
            row = (await session.execute(text(sql), params)).first()
        if row is None:
            raise HTTPException(status_code=404, detail="rider_kyc_submission_not_found")
        return _row_to_item(row)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001 — never 500 the admin queue
        if _is_missing_table(e):
            _log.warning("rider_kyc_%s_not_available", op, error=str(e))
            return {"ok": False, "reason": "not_available"}
        _log.warning("rider_kyc_%s_failed", op, error=str(e), id=params.get("id"))
        return {"ok": False, "reason": "error"}


@router.post(
    "/{kyc_id}/verify",
    summary="Verify a rider-KYC submission",
    dependencies=[Depends(requires_permission(P_DASHBOARD_READ))],
)
async def verify_rider_kyc(
    kyc_id: str,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, Any]:
    """Mark a submission ``verified`` and clear any prior rejection reason."""
    return await _apply(uow, _VERIFY_SQL, {"id": kyc_id}, op="verify")


@router.post(
    "/{kyc_id}/reject",
    summary="Reject a rider-KYC submission with a reason",
    dependencies=[Depends(requires_permission(P_DASHBOARD_READ))],
)
async def reject_rider_kyc(
    kyc_id: str,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    body: RejectBody | None = None,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, Any]:
    """Mark a submission ``rejected`` and store the operator's reason."""
    reason = (body.reason if body else "") or ""
    return await _apply(uow, _REJECT_SQL, {"id": kyc_id, "reason": reason}, op="reject")


__all__ = ["router"]
