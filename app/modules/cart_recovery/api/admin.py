"""Admin endpoints for cart_recovery — KPI rollup, audit lists, suppressions."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.uow import UnitOfWork, get_uow
from app.core.security.rbac import requires_permission
from app.modules.cart_recovery.models import (
    HypershopCartRecoveryDispatch,
    HypershopCartRecoverySuppression,
)
from app.modules.cart_recovery.schemas import (
    DispatchRead,
    SuppressionCreate,
    SuppressionRead,
)

router = APIRouter(prefix="/admin/cart-recovery", tags=["admin-cart-recovery"])

_PERM_VIEW = "cart_recovery.view"
_PERM_MANAGE = "cart_recovery.manage"


# ─── Summary ──────────────────────────────────────────────────────


@router.get(
    "/summary",
    dependencies=[Depends(requires_permission(_PERM_VIEW))],
    summary="KPI rollup: dispatched / sent / recovered + by-milestone + by-channel",
)
async def summary(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    window_days: Annotated[int, Query(ge=1, le=365)] = 30,
) -> dict:
    since = datetime.now(timezone.utc) - timedelta(days=window_days)
    async with uow.transactional() as session:
        totals_sql = text(
            """
            SELECT
              COUNT(*) AS total_dispatched,
              COUNT(*) FILTER (WHERE status IN ('sent','log_only')) AS total_sent,
              COUNT(*) FILTER (WHERE status = 'suppressed') AS total_suppressed,
              COUNT(*) FILTER (WHERE status = 'failed') AS total_failed,
              COUNT(*) FILTER (WHERE recovered_at IS NOT NULL) AS total_recovered,
              COALESCE(SUM(
                (SELECT (o.grand_total * 100)::bigint FROM orders o
                  WHERE o.id = d.recovered_order_id)
              ), 0) AS total_revenue_minor
            FROM hypershop_cart_recovery_dispatches d
            WHERE created_at >= :since
            """
        )
        t = (await session.execute(totals_sql, {"since": since})).mappings().one()

        ms_sql = text(
            """
            SELECT milestone,
                   COUNT(*) AS dispatched,
                   COUNT(*) FILTER (WHERE recovered_at IS NOT NULL) AS recovered
              FROM hypershop_cart_recovery_dispatches
             WHERE created_at >= :since
             GROUP BY milestone
            """
        )
        ms = (await session.execute(ms_sql, {"since": since})).mappings().all()

        ch_sql = text(
            """
            SELECT channel,
                   COUNT(*) AS dispatched,
                   COUNT(*) FILTER (WHERE recovered_at IS NOT NULL) AS recovered
              FROM hypershop_cart_recovery_dispatches
             WHERE created_at >= :since
             GROUP BY channel
            """
        )
        ch = (await session.execute(ch_sql, {"since": since})).mappings().all()

        daily_sql = text(
            """
            SELECT date_trunc('day', created_at)::date AS d,
                   COUNT(*) AS dispatched,
                   COUNT(*) FILTER (WHERE recovered_at IS NOT NULL) AS recovered
              FROM hypershop_cart_recovery_dispatches
             WHERE created_at >= :since
             GROUP BY 1 ORDER BY 1
            """
        )
        daily = (await session.execute(daily_sql, {"since": since})).mappings().all()

    total_sent = int(t["total_sent"] or 0)
    total_recovered = int(t["total_recovered"] or 0)
    rate = round(total_recovered / total_sent, 4) if total_sent > 0 else 0.0

    def _rate(rec: int, disp: int) -> float:
        return round(rec / disp, 4) if disp > 0 else 0.0

    return {
        "window_days": window_days,
        "total_dispatched": int(t["total_dispatched"] or 0),
        "total_sent": total_sent,
        "total_suppressed": int(t["total_suppressed"] or 0),
        "total_failed": int(t["total_failed"] or 0),
        "total_recovered": total_recovered,
        "total_recovered_revenue_minor": int(t["total_revenue_minor"] or 0),
        "recovery_rate": rate,
        "by_milestone": [
            {
                "milestone": r["milestone"],
                "dispatched": int(r["dispatched"]),
                "recovered": int(r["recovered"]),
                "rate": _rate(int(r["recovered"]), int(r["dispatched"])),
            }
            for r in ms
        ],
        "by_channel": [
            {
                "channel": r["channel"],
                "dispatched": int(r["dispatched"]),
                "recovered": int(r["recovered"]),
                "rate": _rate(int(r["recovered"]), int(r["dispatched"])),
            }
            for r in ch
        ],
        "daily_series": [
            {
                "date": r["d"].isoformat(),
                "dispatched": int(r["dispatched"]),
                "recovered": int(r["recovered"]),
            }
            for r in daily
        ],
    }


# ─── Dispatches ──────────────────────────────────────────────────


@router.get(
    "/dispatches",
    dependencies=[Depends(requires_permission(_PERM_VIEW))],
    summary="Paginated audit list of dispatched recoveries",
)
async def list_dispatches(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    milestone: Annotated[str | None, Query()] = None,
    channel: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    async with uow.transactional() as session:
        stmt = select(HypershopCartRecoveryDispatch)
        if status_filter is not None:
            stmt = stmt.where(HypershopCartRecoveryDispatch.status == status_filter)
        if milestone is not None:
            stmt = stmt.where(HypershopCartRecoveryDispatch.milestone == milestone)
        if channel is not None:
            stmt = stmt.where(HypershopCartRecoveryDispatch.channel == channel)
        total = (
            await session.execute(
                select(func.count()).select_from(stmt.subquery()),
            )
        ).scalar_one()
        rows = list(
            (
                await session.execute(
                    stmt.order_by(HypershopCartRecoveryDispatch.created_at.desc())
                    .limit(limit).offset(offset),
                )
            ).scalars().all()
        )
    return {
        "items": [DispatchRead.model_validate(r).model_dump(mode="json") for r in rows],
        "total": int(total),
        "limit": limit,
        "offset": offset,
    }


# ─── Suppressions ────────────────────────────────────────────────


@router.get(
    "/suppressions",
    dependencies=[Depends(requires_permission(_PERM_VIEW))],
    summary="Paginated suppression list (email/phone masked)",
)
async def list_suppressions(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    channel: Annotated[str | None, Query()] = None,
    reason: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    async with uow.transactional() as session:
        stmt = select(HypershopCartRecoverySuppression)
        if channel is not None:
            stmt = stmt.where(HypershopCartRecoverySuppression.channel == channel)
        if reason is not None:
            stmt = stmt.where(HypershopCartRecoverySuppression.reason == reason)
        total = (
            await session.execute(
                select(func.count()).select_from(stmt.subquery()),
            )
        ).scalar_one()
        rows = list(
            (
                await session.execute(
                    stmt.order_by(
                        HypershopCartRecoverySuppression.created_at.desc(),
                    )
                    .limit(limit).offset(offset),
                )
            ).scalars().all()
        )
    return {
        "items": [
            SuppressionRead.from_orm_masked(r).model_dump(mode="json") for r in rows
        ],
        "total": int(total),
        "limit": limit,
        "offset": offset,
    }


@router.post(
    "/suppressions",
    dependencies=[Depends(requires_permission(_PERM_MANAGE))],
    summary="Add a manual suppression row",
    status_code=status.HTTP_201_CREATED,
)
async def create_suppression(
    body: SuppressionCreate,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict:
    async with uow.transactional() as session:
        row = HypershopCartRecoverySuppression(
            customer_user_id=body.customer_user_id,
            key_email=body.key_email,
            key_phone=body.key_phone,
            channel=body.channel,
            reason=body.reason,
            expires_at=body.expires_at,
        )
        session.add(row)
        await session.flush()
        await session.refresh(row)
    return SuppressionRead.from_orm_masked(row).model_dump(mode="json")


@router.delete(
    "/suppressions/{sup_id}",
    dependencies=[Depends(requires_permission(_PERM_MANAGE))],
    summary="Remove a suppression row",
)
async def delete_suppression(
    sup_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict:
    async with uow.transactional() as session:
        row = await session.get(HypershopCartRecoverySuppression, sup_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Suppression not found.")
        await session.delete(row)
    return {"id": str(sup_id), "deleted": True}


# unused-import guards (for AsyncSession typing if extended later)
_ = AsyncSession
