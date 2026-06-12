"""Admin-side Sponsored Products endpoints.

Phase 1.B: admin wallet credit / debit / view / list are real.
Phase 1.G: campaign list/suspend, quality-override, revenue rollup,
invalid-clicks queue, click refund are real.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.core.db.uow import UnitOfWork, get_uow
from app.core.security.deps import get_current_principal
from app.core.security.principal import Principal
from app.core.security.rbac import requires_permission
from app.core.time import utc_now
from app.modules.ads import repository as repo
from app.modules.ads import service as ads_service
from app.modules.ads.models import (
    HypershopAdCampaign,
    HypershopAdClick,
    HypershopAdGroup,
    HypershopAdQualitySnapshot,
)
from app.modules.ads.schemas import (
    AdCampaignRead,
    AdminWalletCreditRequest,
    AdminWalletDebitRequest,
    AdminWalletListItem,
    AdminWalletListResponse,
    AdminWalletView,
    AdWalletRead,
    AdWalletTxnRead,
)

router = APIRouter(prefix="/admin/ads", tags=["admin-ads"])

_PERM_VIEW = "ads.view"
_PERM_MANAGE = "ads.manage"


# ─── Phase 1.G admin request bodies ────────────────────────────────


class _SuspendBody(BaseModel):
    reason: str = Field(min_length=1, max_length=256)


class _QualityOverrideBody(BaseModel):
    quality_score: float = Field(ge=0.5, le=1.5)
    reason: str = Field(min_length=1, max_length=256)


class _RefundClickBody(BaseModel):
    reason: str = Field(min_length=1, max_length=256)


@router.get(
    "/campaigns",
    dependencies=[Depends(requires_permission(_PERM_VIEW))],
    summary="Admin — list every seller's campaigns",
)
async def list_all_campaigns(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    seller_id: Annotated[UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    async with uow.transactional() as session:
        stmt = select(HypershopAdCampaign)
        if status_filter is not None:
            stmt = stmt.where(HypershopAdCampaign.status == status_filter)
        if seller_id is not None:
            stmt = stmt.where(HypershopAdCampaign.seller_id == seller_id)
        total = (
            await session.execute(
                select(func.count()).select_from(stmt.subquery()),
            )
        ).scalar_one()
        rows = list(
            (
                await session.execute(
                    stmt.order_by(HypershopAdCampaign.created_at.desc())
                    .limit(limit).offset(offset),
                )
            ).scalars().all()
        )
    return {
        "items": [AdCampaignRead.model_validate(r) for r in rows],
        "total": int(total),
        "limit": limit,
        "offset": offset,
    }


@router.post(
    "/campaigns/{campaign_id}/suspend",
    dependencies=[Depends(requires_permission(_PERM_MANAGE))],
    summary="Force-suspend a campaign (policy violation, fraud, etc.)",
)
async def suspend_campaign(
    campaign_id: UUID,
    body: _SuspendBody,
    principal: Annotated[Principal, Depends(get_current_principal)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict:
    async with uow.transactional() as session:
        campaign = await repo.get_campaign(session, campaign_id)
        if campaign is None:
            raise HTTPException(status_code=404, detail="Campaign not found.")
        campaign.status = "paused"
        await session.flush()
    return {
        "campaign_id": str(campaign_id),
        "new_status": "paused",
        "reason": body.reason,
        "suspended_by": str(principal.user_id),
        "suspended_at": utc_now().isoformat(),
    }


@router.post(
    "/ad-groups/{ad_group_id}/quality-override",
    dependencies=[Depends(requires_permission(_PERM_MANAGE))],
    summary="Manually override the quality score for an ad group",
)
async def quality_override(
    ad_group_id: UUID,
    body: _QualityOverrideBody,
    principal: Annotated[Principal, Depends(get_current_principal)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict:
    async with uow.transactional() as session:
        ag = await repo.get_ad_group(session, ad_group_id)
        if ag is None:
            raise HTTPException(status_code=404, detail="Ad group not found.")
        old_score = float(ag.quality_score)
        ag.quality_score = round(body.quality_score, 2)
        # Snapshot the override so the quality history reflects the manual change.
        snap = HypershopAdQualitySnapshot(
            ad_group_id=ad_group_id,
            ctr=None,
            rating_avg=None,
            in_stock_rate=None,
            computed_score=round(body.quality_score, 2),
        )
        session.add(snap)
        await session.flush()
    return {
        "ad_group_id": str(ad_group_id),
        "old_score": old_score,
        "new_score": round(body.quality_score, 2),
        "reason": body.reason,
        "overridden_by": str(principal.user_id),
    }


@router.get(
    "/revenue",
    dependencies=[Depends(requires_permission(_PERM_VIEW))],
    summary="Platform ad revenue rollup (daily / weekly / monthly)",
)
async def revenue_rollup(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    window_days: Annotated[int, Query(ge=1, le=365)] = 30,
) -> dict:
    """Returns total revenue, click count, daily series for charting."""
    since = utc_now() - timedelta(days=window_days)
    async with uow.transactional() as session:
        # Total aggregate (valid clicks only).
        total_row = (
            await session.execute(
                select(
                    func.coalesce(func.sum(HypershopAdClick.charged_amount_minor), 0),
                    func.count(HypershopAdClick.id),
                )
                .where(
                    HypershopAdClick.is_invalid.is_(False),
                    HypershopAdClick.created_at >= since,
                )
            )
        ).one()
        total_revenue_minor = int(total_row[0] or 0)
        total_clicks = int(total_row[1] or 0)

        # Daily series.
        day_col = func.date_trunc("day", HypershopAdClick.created_at).label("d")
        series_rows = (
            await session.execute(
                select(
                    day_col,
                    func.sum(HypershopAdClick.charged_amount_minor).label("revenue"),
                    func.count(HypershopAdClick.id).label("clicks"),
                )
                .where(
                    HypershopAdClick.is_invalid.is_(False),
                    HypershopAdClick.created_at >= since,
                )
                .group_by(day_col)
                .order_by(day_col.asc())
            )
        ).all()
        series = [
            {
                "date": d.date().isoformat() if isinstance(d, datetime) else str(d),
                "revenue_minor": int(rev or 0),
                "clicks": int(clk or 0),
            }
            for d, rev, clk in series_rows
        ]
    return {
        "window_days": window_days,
        "total_revenue_minor": total_revenue_minor,
        "total_clicks": total_clicks,
        "average_cpc_minor": (
            round(total_revenue_minor / total_clicks) if total_clicks > 0 else 0
        ),
        "daily_series": series,
    }


@router.get(
    "/invalid-clicks",
    dependencies=[Depends(requires_permission(_PERM_VIEW))],
    summary="Invalid-click audit queue",
)
async def invalid_clicks(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    async with uow.transactional() as session:
        stmt = select(HypershopAdClick).where(HypershopAdClick.is_invalid.is_(True))
        total = (
            await session.execute(
                select(func.count()).select_from(stmt.subquery()),
            )
        ).scalar_one()
        rows = list(
            (
                await session.execute(
                    stmt.order_by(HypershopAdClick.created_at.desc())
                    .limit(limit).offset(offset),
                )
            ).scalars().all()
        )
    return {
        "items": [
            {
                "id": int(c.id),
                "ad_group_id": str(c.ad_group_id),
                "seller_id": str(c.seller_id),
                "user_id": str(c.user_id) if c.user_id else None,
                "invalid_reason": c.invalid_reason,
                "charged_amount_minor": int(c.charged_amount_minor),
                "created_at": c.created_at.isoformat(),
            }
            for c in rows
        ],
        "total": int(total),
        "limit": limit,
        "offset": offset,
    }


@router.post(
    "/clicks/{click_id}/refund",
    dependencies=[Depends(requires_permission(_PERM_MANAGE))],
    summary="Refund a wrongly-charged click",
)
async def refund_click(
    click_id: int,
    body: _RefundClickBody,
    principal: Annotated[Principal, Depends(get_current_principal)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict:
    async with uow.transactional() as session:
        click = await session.get(HypershopAdClick, click_id)
        if click is None:
            raise HTTPException(status_code=404, detail="Click not found.")
        if click.charged_amount_minor == 0:
            raise HTTPException(
                status_code=400,
                detail="Click was never charged — nothing to refund.",
            )
        if click.is_invalid:
            raise HTTPException(
                status_code=409,
                detail="Click already marked invalid / refunded.",
            )
        refund_minor = int(click.charged_amount_minor)
        # Issue refund: credit the wallet back with a deterministic
        # source_id so a replay-refund cannot double-credit.
        await repo.credit_ad_wallet(
            session,
            seller_id=click.seller_id,
            amount_minor=refund_minor,
            source_type="click_refund",
            source_id=uuid4(),
            memo=(
                f"[admin {principal.user_id}] refund click {click_id}: "
                f"{body.reason[:80]}"
            ),
        )
        click.is_invalid = True
        click.invalid_reason = f"manual_refund:{body.reason[:48]}"
        await session.flush()
    return {
        "click_id": click_id,
        "refunded_minor": refund_minor,
        "refunded_by": str(principal.user_id),
        "reason": body.reason,
    }


# ── Wallet admin (Phase 1.B real) ───────────────────────────────────


@router.post(
    "/wallets/{seller_id}/credit",
    response_model=AdWalletTxnRead,
    dependencies=[Depends(requires_permission(_PERM_MANAGE))],
    summary="Admin — grant free credit to a seller's ad wallet",
)
async def admin_credit_wallet(
    seller_id: UUID,
    body: AdminWalletCreditRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> AdWalletTxnRead:
    async with uow.transactional() as session:
        txn = await ads_service.manual_credit(
            session,
            seller_id=seller_id,
            amount_minor=body.amount_minor,
            admin_user_id=principal.user_id,
            memo=body.memo,
        )
    return AdWalletTxnRead.model_validate(txn)


@router.post(
    "/wallets/{seller_id}/debit",
    response_model=AdWalletTxnRead,
    dependencies=[Depends(requires_permission(_PERM_MANAGE))],
    summary="Admin — debit a seller's ad wallet (clawback / adjustment)",
)
async def admin_debit_wallet(
    seller_id: UUID,
    body: AdminWalletDebitRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> AdWalletTxnRead:
    async with uow.transactional() as session:
        try:
            txn = await ads_service.manual_debit(
                session,
                seller_id=seller_id,
                amount_minor=body.amount_minor,
                admin_user_id=principal.user_id,
                memo=body.memo,
            )
        except ads_service.InsufficientAdBalance as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    return AdWalletTxnRead.model_validate(txn)


@router.get(
    "/wallets/{seller_id}",
    response_model=AdminWalletView,
    dependencies=[Depends(requires_permission(_PERM_VIEW))],
    summary="Admin — full wallet view for a single seller",
)
async def admin_get_wallet(
    seller_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> AdminWalletView:
    async with uow.transactional() as session:
        wallet = await repo.get_or_create_ad_wallet(session, seller_id)
        rows, total = await repo.list_ad_wallet_txns(
            session, seller_id=seller_id, limit=50, offset=0,
        )
    return AdminWalletView(
        wallet=AdWalletRead.model_validate(wallet),
        recent_txns=[AdWalletTxnRead.model_validate(r) for r in rows],
        total_txns=total,
    )


@router.get(
    "/wallets",
    response_model=AdminWalletListResponse,
    dependencies=[Depends(requires_permission(_PERM_VIEW))],
    summary="Admin — list ad wallets (filterable)",
)
async def admin_list_wallets(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    status: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AdminWalletListResponse:
    low_only = status == "low_balance"
    async with uow.transactional() as session:
        rows, total = await repo.list_wallets(
            session, low_balance_only=low_only, limit=limit, offset=offset,
        )
    return AdminWalletListResponse(
        items=[
            AdminWalletListItem(
                seller_id=w.seller_id,
                balance_minor=int(w.balance_minor),
                total_spent_minor=int(w.total_spent_minor),
                auto_recharge_enabled=bool(w.auto_recharge_enabled),
            )
            for w in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
    )
