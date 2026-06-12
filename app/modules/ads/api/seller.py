"""Seller-scoped Sponsored Products endpoints.

Phase 1.B: wallet endpoints (balance / recharge / transactions /
auto-recharge) are real.
Phase 1.C: campaign + ad-group endpoints are real. Performance metrics
return zeros until Phase 1.D wires impressions/clicks.
"""
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.db.uow import UnitOfWork, get_uow
from app.core.errors import ValidationError
from app.core.security.rbac import requires_permission
from app.modules.ads import repository as ads_repo
from app.modules.ads import service as ads_service
from app.modules.ads.schemas import (
    AdCampaignCreate,
    AdCampaignRead,
    AdCampaignUpdate,
    AdGroupCreate,
    AdGroupRead,
    AdGroupUpdate,
    AdWalletBalanceRead,
    AdWalletRechargeRequest,
    AdWalletRechargeResponse,
    AdWalletTxnListResponse,
    AdWalletTxnRead,
    AutoRechargeUpdate,
)
from app.modules.sellers.deps import get_current_seller_id

router = APIRouter(prefix="/seller/ads", tags=["seller-ads"])

_PERM = "ads.manage"


# ─── Helpers ────────────────────────────────────────────────────────


async def _load_own_campaign(
    session, campaign_id: UUID, seller_id: UUID,
):
    """Fetch campaign + enforce seller-scope. Raises 404 on miss/foreign."""
    campaign = await ads_repo.get_campaign(session, campaign_id)
    if campaign is None or campaign.seller_id != seller_id:
        # Mask foreign-seller campaigns as 404 — don't leak existence.
        raise HTTPException(status_code=404, detail="Campaign not found.")
    return campaign


async def _load_own_ad_group(
    session, ad_group_id: UUID, seller_id: UUID,
):
    """Fetch ad-group + enforce seller-scope via parent campaign."""
    group = await ads_repo.get_ad_group(session, ad_group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="Ad group not found.")
    campaign = await ads_repo.get_campaign(session, group.campaign_id)
    if campaign is None or campaign.seller_id != seller_id:
        raise HTTPException(status_code=404, detail="Ad group not found.")
    return group, campaign


# ─── Campaign CRUD (Phase 1.C real) ─────────────────────────────────


@router.get(
    "/campaigns",
    dependencies=[Depends(requires_permission(_PERM))],
    summary="List own campaigns",
)
async def list_campaigns(
    seller_id: Annotated[UUID, Depends(get_current_seller_id)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    async with uow.transactional() as session:
        rows = await ads_repo.list_campaigns(
            session, seller_id,
            status=status_filter, limit=limit, offset=offset,
        )
    return {
        "items": [AdCampaignRead.model_validate(r) for r in rows],
        "limit": limit,
        "offset": offset,
    }


@router.post(
    "/campaigns",
    dependencies=[Depends(requires_permission(_PERM))],
    summary="Create a campaign",
    status_code=201,
    response_model=AdCampaignRead,
)
async def create_campaign(
    body: AdCampaignCreate,
    seller_id: Annotated[UUID, Depends(get_current_seller_id)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> AdCampaignRead:
    async with uow.transactional() as session:
        campaign = await ads_repo.create_campaign(
            session,
            seller_id=seller_id,
            name=body.name,
            daily_budget_minor=body.daily_budget_minor,
            status=body.status,
            total_budget_minor=body.total_budget_minor,
            start_at=body.start_at,
            end_at=body.end_at,
        )
    return AdCampaignRead.model_validate(campaign)


@router.get(
    "/campaigns/{campaign_id}",
    dependencies=[Depends(requires_permission(_PERM))],
    summary="Read one campaign",
    response_model=AdCampaignRead,
)
async def get_campaign(
    campaign_id: UUID,
    seller_id: Annotated[UUID, Depends(get_current_seller_id)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> AdCampaignRead:
    async with uow.transactional() as session:
        campaign = await _load_own_campaign(session, campaign_id, seller_id)
    return AdCampaignRead.model_validate(campaign)


@router.patch(
    "/campaigns/{campaign_id}",
    dependencies=[Depends(requires_permission(_PERM))],
    summary="Update a campaign",
    response_model=AdCampaignRead,
)
async def update_campaign(
    campaign_id: UUID,
    body: AdCampaignUpdate,
    seller_id: Annotated[UUID, Depends(get_current_seller_id)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> AdCampaignRead:
    async with uow.transactional() as session:
        await _load_own_campaign(session, campaign_id, seller_id)
        fields = body.model_dump(exclude_unset=True, exclude_none=False)
        campaign = await ads_repo.update_campaign(session, campaign_id, **fields)
    return AdCampaignRead.model_validate(campaign)


# ─── Ad Group CRUD (Phase 1.C real) ─────────────────────────────────


@router.post(
    "/campaigns/{campaign_id}/ad-groups",
    dependencies=[Depends(requires_permission(_PERM))],
    summary="Create an ad group under a campaign",
    status_code=201,
    response_model=AdGroupRead,
)
async def create_ad_group(
    campaign_id: UUID,
    body: AdGroupCreate,
    seller_id: Annotated[UUID, Depends(get_current_seller_id)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> AdGroupRead:
    async with uow.transactional() as session:
        await _load_own_campaign(session, campaign_id, seller_id)
        # Pydantic AdTarget → plain dict for JSONB column.
        targets_json = [t.model_dump() for t in body.targets]
        group = await ads_repo.create_ad_group(
            session,
            campaign_id=campaign_id,
            name=body.name,
            default_bid_minor=body.default_bid_minor,
            target_type=body.target_type,
            targets=targets_json,
            product_ids=body.product_ids,
            status=body.status,
        )
    return AdGroupRead.model_validate(group)


@router.get(
    "/campaigns/{campaign_id}/ad-groups",
    dependencies=[Depends(requires_permission(_PERM))],
    summary="List ad groups in a campaign",
)
async def list_ad_groups(
    campaign_id: UUID,
    seller_id: Annotated[UUID, Depends(get_current_seller_id)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict:
    async with uow.transactional() as session:
        await _load_own_campaign(session, campaign_id, seller_id)
        rows = await ads_repo.list_ad_groups(session, campaign_id)
    return {"items": [AdGroupRead.model_validate(r) for r in rows]}


@router.patch(
    "/ad-groups/{ad_group_id}",
    dependencies=[Depends(requires_permission(_PERM))],
    summary="Update an ad group",
    response_model=AdGroupRead,
)
async def update_ad_group(
    ad_group_id: UUID,
    body: AdGroupUpdate,
    seller_id: Annotated[UUID, Depends(get_current_seller_id)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> AdGroupRead:
    async with uow.transactional() as session:
        await _load_own_ad_group(session, ad_group_id, seller_id)
        fields = body.model_dump(exclude_unset=True, exclude_none=False)
        # AdTarget instances → plain dicts for JSONB.
        if "targets" in fields and fields["targets"] is not None:
            fields["targets"] = [
                t if isinstance(t, dict) else t.model_dump()
                for t in fields["targets"]
            ]
        group = await ads_repo.update_ad_group(session, ad_group_id, **fields)
    return AdGroupRead.model_validate(group)


@router.get(
    "/ad-groups/{ad_group_id}/performance",
    dependencies=[Depends(requires_permission(_PERM))],
    summary="Ad-group performance (impressions, clicks, spend)",
)
async def ad_group_performance(
    ad_group_id: UUID,
    seller_id: Annotated[UUID, Depends(get_current_seller_id)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict:
    """Phase 1.D: real CTR + spend aggregated from
    hypershop_ad_impressions + hypershop_ad_clicks."""
    from sqlalchemy import func, select

    from app.modules.ads.models import (
        HypershopAdClick,
        HypershopAdImpression,
    )

    async with uow.transactional() as session:
        group, _campaign = await _load_own_ad_group(
            session, ad_group_id, seller_id,
        )
        impr_count = (
            await session.execute(
                select(func.count())
                .select_from(HypershopAdImpression)
                .where(HypershopAdImpression.ad_group_id == ad_group_id)
            )
        ).scalar_one() or 0
        click_row = (
            await session.execute(
                select(
                    func.count(HypershopAdClick.id),
                    func.coalesce(func.sum(HypershopAdClick.charged_amount_minor), 0),
                )
                .where(
                    HypershopAdClick.ad_group_id == ad_group_id,
                    HypershopAdClick.is_invalid.is_(False),
                )
            )
        ).one()
    click_count = int(click_row[0] or 0)
    spend_minor = int(click_row[1] or 0)
    ctr = (click_count / impr_count) if impr_count > 0 else 0.0
    return {
        "ad_group_id": str(group.id),
        "impressions": int(impr_count),
        "clicks": click_count,
        "ctr": round(ctr, 4),
        "spend_minor": spend_minor,
        "quality_score": float(group.quality_score),
    }


# ── Wallet (Phase 1.B real) ─────────────────────────────────────────


@router.get(
    "/wallet/balance",
    response_model=AdWalletBalanceRead,
    dependencies=[Depends(requires_permission(_PERM))],
    summary="Read own ad-wallet balance",
)
async def wallet_balance(
    seller_id: Annotated[UUID, Depends(get_current_seller_id)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> AdWalletBalanceRead:
    async with uow.transactional() as session:
        data = await ads_service.get_balance(session, seller_id)
    return AdWalletBalanceRead(**data)


@router.post(
    "/wallet/recharge",
    response_model=AdWalletRechargeResponse,
    dependencies=[Depends(requires_permission(_PERM))],
    summary="Top up ad wallet via bkash",
)
async def wallet_recharge(
    body: AdWalletRechargeRequest,
    seller_id: Annotated[UUID, Depends(get_current_seller_id)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> AdWalletRechargeResponse:
    async with uow.transactional() as session:
        try:
            data = await ads_service.initiate_recharge(
                session,
                seller_id=seller_id,
                amount_minor=body.amount_minor,
                bkash_msisdn=body.bkash_msisdn,
            )
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=exc.message) from exc
        except ads_service.AdRechargeProviderError as exc:
            raise HTTPException(
                status_code=502,
                detail="Payment provider unavailable, please try again.",
            ) from exc
    # Map both legacy + new keys onto the AdWalletRechargeResponse shape.
    return AdWalletRechargeResponse(
        payment_id=data["payment_id"],
        bkash_url=data["bkash_url"],
        expires_at=data["expires_at"],
    )


@router.get(
    "/wallet/transactions",
    response_model=AdWalletTxnListResponse,
    dependencies=[Depends(requires_permission(_PERM))],
    summary="Ad-wallet transaction history",
)
async def wallet_transactions(
    seller_id: Annotated[UUID, Depends(get_current_seller_id)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    kind: Annotated[str | None, Query()] = None,
) -> AdWalletTxnListResponse:
    async with uow.transactional() as session:
        data = await ads_service.list_transactions(
            session,
            seller_id=seller_id,
            limit=limit,
            offset=offset,
            kind=kind,
        )
    return AdWalletTxnListResponse(
        items=[AdWalletTxnRead.model_validate(r) for r in data["items"]],
        total=data["total"],
        limit=data["limit"],
        offset=data["offset"],
    )


@router.patch(
    "/wallet/auto-recharge",
    response_model=AdWalletBalanceRead,
    dependencies=[Depends(requires_permission(_PERM))],
    summary="Configure auto-recharge thresholds",
)
async def wallet_auto_recharge(
    body: AutoRechargeUpdate,
    seller_id: Annotated[UUID, Depends(get_current_seller_id)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> AdWalletBalanceRead:
    async with uow.transactional() as session:
        try:
            data = await ads_service.update_auto_recharge(
                session,
                seller_id=seller_id,
                enabled=body.enabled,
                threshold_minor=body.threshold_minor,
                amount_minor=body.amount_minor,
            )
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=exc.message) from exc
    return AdWalletBalanceRead(**data)
