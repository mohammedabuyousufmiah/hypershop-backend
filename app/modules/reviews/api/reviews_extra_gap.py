"""Gap-filler READ endpoints for the admin ops page (AdminOpsClient).

Fills missing GETs that previously 404'd:
    GET /admin/reviews/reports              -> ReviewReportsPageWire (triage board)
    GET /admin/coupons/{coupon_id}/redemptions -> redemptions page

Boot-safe: text() SQL only, every query try/except → never 500. Registered
centrally in main.py.
"""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from app.core.db.uow import UnitOfWork, get_uow
from app.core.security.rbac import requires_permission

router = APIRouter(tags=["admin-ops-gap"])
_READ = "dashboard.read"  # admins + super_admin hold this


def _jsonable(row: dict) -> dict[str, Any]:
    return {
        k: (v if isinstance(v, (str, int, float, bool)) or v is None else str(v))
        for k, v in row.items()
    }


@router.get(
    "/admin/reviews/reports",
    dependencies=[Depends(requires_permission(_READ))],
)
async def review_reports(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    """Reported-reviews triage board. Tries a review_reports table; if it
    doesn't exist, returns an empty page (UI shows 'no reports')."""
    items: list[dict[str, Any]] = []
    total = 0
    try:
        from sqlalchemy import text

        offset = (page - 1) * page_size
        async with uow.transactional() as session:
            total = (
                await session.execute(text("SELECT count(*) FROM review_reports"))
            ).scalar() or 0
            rows = (
                await session.execute(
                    text(
                        "SELECT * FROM review_reports "
                        "ORDER BY created_at DESC LIMIT :lim OFFSET :off"
                    ),
                    {"lim": page_size, "off": offset},
                )
            ).mappings().all()
            items = [_jsonable(dict(r)) for r in rows]
    except Exception:  # noqa: BLE001 — table may not exist
        items, total = [], 0
    return {"items": items, "page": page, "page_size": page_size, "total": total}


@router.get(
    "/admin/coupons/{coupon_id}/redemptions",
    dependencies=[Depends(requires_permission(_READ))],
)
async def coupon_redemptions(
    coupon_id: str,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    """Per-coupon redemption ledger. Defensive across possible schemas
    (coupon_redemptions / promotion_redemptions). Empty page if none."""
    items: list[dict[str, Any]] = []
    total = 0
    try:
        from sqlalchemy import text

        offset = (page - 1) * page_size
        async with uow.transactional() as session:
            for tbl, col in (
                ("coupon_redemptions", "coupon_id"),
                ("promotion_redemptions", "coupon_id"),
            ):
                try:
                    total = (
                        await session.execute(
                            text(f"SELECT count(*) FROM {tbl} WHERE {col} = :cid"),
                            {"cid": coupon_id},
                        )
                    ).scalar() or 0
                    rows = (
                        await session.execute(
                            text(
                                f"SELECT * FROM {tbl} WHERE {col} = :cid "
                                "ORDER BY created_at DESC LIMIT :lim OFFSET :off"
                            ),
                            {"cid": coupon_id, "lim": page_size, "off": offset},
                        )
                    ).mappings().all()
                    items = [_jsonable(dict(r)) for r in rows]
                    break
                except Exception:  # noqa: BLE001 — try next table
                    continue
    except Exception:  # noqa: BLE001
        items, total = [], 0
    return {
        "items": items,
        "page": page,
        "page_size": page_size,
        "total": total,
        "coupon_id": coupon_id,
    }
