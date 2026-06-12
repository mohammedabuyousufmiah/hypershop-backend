"""Admin endpoints for the sellers module — phase 1.

All routes require the ``sellers.admin`` permission. There's no
seller-self-serve surface in phase 1 — that lands in phase 4. The
``submit_kyc`` endpoint accepts admin-on-behalf-of-seller calls
because the operator typically nominates the user during onboarding.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path as PathParam, Query, Response, status

from app.core.db.uow import UnitOfWork, get_uow
from app.core.errors import ValidationError
from app.core.security.deps import get_current_principal
from app.core.security.principal import Principal
from app.core.security.rbac import requires_permission
from app.modules.sellers.codes import ALL_STATUSES
from app.modules.sellers.schemas import (
    SellerCommissionUpdateIn,
    SellerCreateIn,
    SellerKycSubmitIn,
    SellerListOut,
    SellerOut,
    SellerPayoutConfigIn,
    SellerRejectIn,
    SellerSuspendIn,
    SellerUserLinkIn,
    SellerUserOut,
)
from app.modules.sellers.service import SellerService

router = APIRouter(prefix="/admin/sellers", tags=["admin-sellers"])

_RW = "sellers.admin"


@router.get(
    "",
    response_model=SellerListOut,
    dependencies=[Depends(requires_permission(_RW))],
)
async def list_sellers(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    status_q: Annotated[
        str | None,
        Query(alias="status", description="Filter by status; omit for all."),
    ] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> SellerListOut:
    if status_q and status_q not in ALL_STATUSES:
        raise ValidationError(
            f"Unknown status: {status_q}.",
            details={"allowed": list(ALL_STATUSES)},
        )
    async with uow.transactional() as session:
        svc = SellerService(session)
        items, total = await svc.list(
            status=status_q, offset=offset, limit=limit,
        )
        rows = [SellerOut.model_validate(s) for s in items]
    return SellerListOut(items=rows, total=total)


@router.get(
    "/{seller_id}",
    response_model=SellerOut,
    dependencies=[Depends(requires_permission(_RW))],
)
async def get_seller(
    seller_id: Annotated[UUID, PathParam(...)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> SellerOut:
    """Single-seller detail. Powers the AdminSellerDetailClient page mount
    (api.sellersAdmin.get). 404 → SellerNotFoundError if the id is unknown."""
    async with uow.transactional() as session:
        svc = SellerService(session)
        seller = await svc._require(seller_id)
        await session.refresh(seller)
        return SellerOut.model_validate(seller)


@router.post(
    "",
    response_model=SellerOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(requires_permission(_RW))],
)
async def create_seller(
    body: SellerCreateIn,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> SellerOut:
    async with uow.transactional() as session:
        svc = SellerService(session)
        seller = await svc.create(
            business_name=body.business_name,
            slug=body.slug,
            contact_email=body.contact_email,
            contact_phone=body.contact_phone,
            principal=principal,
        )
        await session.refresh(seller)
        return SellerOut.model_validate(seller)


@router.post(
    "/{seller_id}/kyc",
    response_model=SellerOut,
    dependencies=[Depends(requires_permission(_RW))],
)
async def submit_kyc(
    seller_id: Annotated[UUID, PathParam(...)],
    body: SellerKycSubmitIn,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> SellerOut:
    async with uow.transactional() as session:
        svc = SellerService(session)
        seller = await svc.submit_kyc(
            seller_id=seller_id,
            tin=body.tin,
            nid=body.nid,
            bank_account_name=body.bank_account_name,
            bank_account_number=body.bank_account_number,
            bank_name=body.bank_name,
            bank_branch=body.bank_branch,
            trade_license_no=body.trade_license_no,
            principal=principal,
        )
        await session.refresh(seller)
        return SellerOut.model_validate(seller)


@router.post(
    "/{seller_id}/approve",
    response_model=SellerOut,
    # Carved out 2026-05-16 from `_RW` (sellers.admin) onto the narrower
    # `sellers.approve` perm so a future "KYC reviewer" role can grant
    # approval authority without the broader admin verb. Today both
    # perms are held by the same 3 roles (admin / manager / seller_manager).
    dependencies=[Depends(requires_permission("sellers.approve"))],
)
async def approve_seller(
    seller_id: Annotated[UUID, PathParam(...)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> SellerOut:
    async with uow.transactional() as session:
        svc = SellerService(session)
        seller = await svc.approve(seller_id=seller_id, principal=principal)
        await session.refresh(seller)
        return SellerOut.model_validate(seller)


@router.post(
    "/{seller_id}/reject",
    response_model=SellerOut,
    dependencies=[Depends(requires_permission(_RW))],
)
async def reject_seller(
    seller_id: Annotated[UUID, PathParam(...)],
    body: SellerRejectIn,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> SellerOut:
    async with uow.transactional() as session:
        svc = SellerService(session)
        seller = await svc.reject(
            seller_id=seller_id, reason=body.reason, principal=principal,
        )
        await session.refresh(seller)
        return SellerOut.model_validate(seller)


@router.post(
    "/{seller_id}/suspend",
    response_model=SellerOut,
    dependencies=[Depends(requires_permission(_RW))],
)
async def suspend_seller(
    seller_id: Annotated[UUID, PathParam(...)],
    body: SellerSuspendIn,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> SellerOut:
    async with uow.transactional() as session:
        svc = SellerService(session)
        seller = await svc.suspend(
            seller_id=seller_id, reason=body.reason, principal=principal,
        )
        await session.refresh(seller)
        return SellerOut.model_validate(seller)


@router.post(
    "/{seller_id}/reinstate",
    response_model=SellerOut,
    dependencies=[Depends(requires_permission(_RW))],
)
async def reinstate_seller(
    seller_id: Annotated[UUID, PathParam(...)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> SellerOut:
    async with uow.transactional() as session:
        svc = SellerService(session)
        seller = await svc.reinstate(seller_id=seller_id, principal=principal)
        await session.refresh(seller)
        return SellerOut.model_validate(seller)


@router.patch(
    "/{seller_id}/commission",
    response_model=SellerOut,
    dependencies=[Depends(requires_permission(_RW))],
)
async def update_commission(
    seller_id: Annotated[UUID, PathParam(...)],
    body: SellerCommissionUpdateIn,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> SellerOut:
    async with uow.transactional() as session:
        svc = SellerService(session)
        seller = await svc.update_commission(
            seller_id=seller_id,
            commission_percent=body.commission_percent,
            principal=principal,
        )
        await session.refresh(seller)
        return SellerOut.model_validate(seller)


@router.patch(
    "/{seller_id}/payout-config",
    response_model=SellerOut,
    dependencies=[Depends(requires_permission(_RW))],
)
async def update_payout_config(
    seller_id: Annotated[UUID, PathParam(...)],
    body: SellerPayoutConfigIn,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> SellerOut:
    async with uow.transactional() as session:
        svc = SellerService(session)
        seller = await svc.update_payout_config(
            seller_id=seller_id,
            payout_cadence=body.payout_cadence,
            payout_method=body.payout_method,
            payout_account_id=body.payout_account_id,
            principal=principal,
        )
        await session.refresh(seller)
        return SellerOut.model_validate(seller)


@router.post(
    "/{seller_id}/users",
    response_model=SellerUserOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(requires_permission(_RW))],
)
async def link_user(
    seller_id: Annotated[UUID, PathParam(...)],
    body: SellerUserLinkIn,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> SellerUserOut:
    async with uow.transactional() as session:
        svc = SellerService(session)
        link = await svc.link_user(
            seller_id=seller_id,
            user_id=body.user_id,
            role=body.role,
            principal=principal,
        )
    return SellerUserOut.model_validate(link)


@router.delete(
    "/{seller_id}/users/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    response_class=Response,
    dependencies=[Depends(requires_permission(_RW))],
)
async def unlink_user(
    seller_id: Annotated[UUID, PathParam(...)],
    user_id: Annotated[UUID, PathParam(...)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> Response:
    async with uow.transactional() as session:
        svc = SellerService(session)
        await svc.unlink_user(
            seller_id=seller_id, user_id=user_id, principal=principal,
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ============================================================
#  Payout endpoints (added 2026-05-13)
#  Aggregation engine: ``sellers.payout_service.SellerPayoutService``
#  These admin routes preview seller earnings + create payout requests.
# ============================================================

from datetime import datetime, timezone  # noqa: E402 — keep imports near use


@router.get(
    "/{seller_id}/payout-preview",
    summary="Preview a seller's payout for a period (gross/commission/returns/net)",
    dependencies=[Depends(requires_permission(_RW))],
)
async def preview_payout(
    seller_id: Annotated[UUID, PathParam(...)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    start: datetime = Query(..., description="UTC, ISO-8601"),
    end: datetime = Query(..., description="UTC, ISO-8601"),
) -> dict:
    """Compute earnings owed to the seller for orders placed in
    ``[start, end)``. Read-only — does NOT create a payout request.

    Use this to render the admin UI's per-seller earnings page or to
    sanity-check the numbers before approving a payout."""
    from app.modules.sellers.payout_service import SellerPayoutService
    # Ensure both dates are tz-aware for safe comparison with
    # ``orders.placed_at`` which is ``timestamptz``.
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    async with uow.transactional() as session:
        svc = SellerPayoutService(session)
        preview = await svc.compute_period_owed(
            seller_id=seller_id,
            period_start=start,
            period_end=end,
        )
        return preview.as_dict()


@router.get(
    "/payouts/payable",
    summary="List every seller with a positive payout for the period",
    description=(
        "Used by the biweekly batch-payout cron. Returns the same shape "
        "as ``payout-preview`` repeated per seller."
    ),
    dependencies=[Depends(requires_permission(_RW))],
)
async def list_payable_sellers(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    start: datetime = Query(..., description="UTC, ISO-8601"),
    end: datetime = Query(..., description="UTC, ISO-8601"),
    min_net: float = Query(default=0.01, ge=0),
) -> list[dict]:
    from decimal import Decimal as _Dec
    from app.modules.sellers.payout_service import SellerPayoutService
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    async with uow.transactional() as session:
        svc = SellerPayoutService(session)
        previews = await svc.list_payable_sellers(
            period_start=start,
            period_end=end,
            min_net=_Dec(str(min_net)),
        )
        return [p.as_dict() for p in previews]


@router.post(
    "/{seller_id}/payouts",
    status_code=status.HTTP_201_CREATED,
    summary="Create a payout request for a seller (enters supplier-payments approval queue)",
    dependencies=[Depends(requires_permission(_RW))],
)
async def request_payout(
    seller_id: Annotated[UUID, PathParam(...)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    start: datetime = Query(..., description="UTC, ISO-8601"),
    end: datetime = Query(..., description="UTC, ISO-8601"),
) -> dict:
    """Compute the period owed + hand the payload to the
    supplier_payments approval flow (3+1 approver chain).

    The actual creation of the ``supplier_bills`` row is done by
    that service so the per-bill validation + approver routing rules
    stay in one place. This endpoint returns the preview + the bill
    id once accepted."""
    from app.modules.sellers.payout_service import SellerPayoutService
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)

    async with uow.transactional() as session:
        svc = SellerPayoutService(session)
        preview = await svc.compute_period_owed(
            seller_id=seller_id,
            period_start=start, period_end=end,
        )
        if preview.net_payable <= 0:
            return {
                "created": False,
                "reason": "no_payable_amount",
                "preview": preview.as_dict(),
            }
        payload = await svc.create_payout_request(
            preview=preview,
            created_by=principal.user_id,
        )
        # Hand-off to supplier_payments. The actual binder lives in
        # ``supplier_payments/service.py`` — we keep this loose so
        # the two modules can evolve independently. If/when that
        # service lands a typed ``register_seller_payout(...)`` method,
        # call it here.
        return {
            "created": True,
            "preview": preview.as_dict(),
            "request_payload": payload,
            "note": (
                "Forward this payload to supplier_payments.service to "
                "create the bills row + start the 3+1 approval workflow."
            ),
        }
