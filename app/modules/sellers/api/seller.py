"""Seller-scoped self-serve endpoints — phase 3.

All routes mounted under ``/api/v1/seller/*``. The
``get_current_seller_id`` dependency enforces:
  - caller is authenticated
  - caller is linked to a seller via ``seller_users``
  - that seller's status is ``approved``

Object-level isolation: every read filters by the resolved
seller_id. There is no admin-bypass on this surface — admins use
``/api/v1/admin/sellers/*`` instead. That separation prevents an
admin from accidentally writing to a real seller's row through the
self-serve UI.

Phase-3 scope:
  GET /seller/me              — own seller profile + KYC status
  GET /seller/products        — paginated catalog owned by this seller
  GET /seller/orders          — paginated orders containing seller items
  GET /seller/videos          — paginated product videos owned by this seller
  GET /seller/payouts         — 501 Not Implemented (phase 5 ships this)
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import distinct, func, nulls_last, select

from app.core.db.uow import UnitOfWork, get_uow
from app.core.security.rbac import requires_permission
from app.modules.catalog.models import Product, ProductVariant
from app.modules.orders.models import Order, OrderLine
from app.modules.product_videos.models import ProductVideo
from app.modules.sellers.deps import get_current_seller_id
from app.modules.sellers.models import Seller
from app.modules.sellers.schemas import SellerOut

router = APIRouter(prefix="/seller", tags=["seller-self-serve"])

_SELLER_PERM = "sellers.read"


@router.get(
    "/me",
    response_model=SellerOut,
    dependencies=[Depends(requires_permission(_SELLER_PERM))],
)
async def get_my_seller_profile(
    seller_id: Annotated[UUID, Depends(get_current_seller_id)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> SellerOut:
    async with uow.transactional() as session:
        seller = await session.get(Seller, seller_id)
        # If the seller existed at dep-resolve time but vanished mid-
        # request, surface a generic 404 — racing admin deletes are
        # rare but real on a single-host stack.
        if seller is None:
            raise HTTPException(status_code=404, detail="Seller not found.")
    return SellerOut.model_validate(seller)


# ────────── /seller/products ──────────


from pydantic import BaseModel, ConfigDict
from datetime import datetime


class _SellerProductRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    slug: str
    name: str
    mother_sku: str
    status: str
    created_at: datetime
    updated_at: datetime


class _SellerProductList(BaseModel):
    items: list[_SellerProductRow]
    total: int


@router.get(
    "/products",
    response_model=_SellerProductList,
    dependencies=[Depends(requires_permission(_SELLER_PERM))],
)
async def list_my_products(
    seller_id: Annotated[UUID, Depends(get_current_seller_id)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> _SellerProductList:
    """Catalog rows owned by the calling seller. The seller_id filter
    is non-bypassable — there is no ``?seller_id=`` query parameter."""
    async with uow.transactional() as session:
        items_stmt = (
            select(Product)
            .where(Product.seller_id == seller_id)
            .order_by(Product.created_at.desc())
            .offset(offset).limit(limit)
        )
        rows = (await session.execute(items_stmt)).scalars().all()
        total = int(
            (await session.execute(
                select(func.count())
                .select_from(Product)
                .where(Product.seller_id == seller_id),
            )).scalar_one()
        )
    return _SellerProductList(
        items=[_SellerProductRow.model_validate(p) for p in rows],
        total=total,
    )


# ────────── /seller/orders ──────────


class _SellerOrderRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    code: str
    status: str
    grand_total: str
    placed_at: datetime | None
    completed_at: datetime | None


class _SellerOrderList(BaseModel):
    items: list[_SellerOrderRow]
    total: int


@router.get(
    "/orders",
    response_model=_SellerOrderList,
    dependencies=[Depends(requires_permission(_SELLER_PERM))],
)
async def list_my_orders(
    seller_id: Annotated[UUID, Depends(get_current_seller_id)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> _SellerOrderList:
    """Orders containing at least one line for a product owned by
    this seller. Cross-joins ``orders → order_lines → variants → products``
    and filters by ``products.seller_id``. ``DISTINCT`` is required —
    a single order can have multiple lines from the same seller.
    """
    async with uow.transactional() as session:
        base = (
            select(Order)
            .join(OrderLine, OrderLine.order_id == Order.id)
            .join(ProductVariant, ProductVariant.id == OrderLine.variant_id)
            .join(Product, Product.id == ProductVariant.product_id)
            .where(Product.seller_id == seller_id)
            .distinct()
        )
        items_stmt = (
            base.order_by(nulls_last(Order.placed_at.desc()))
            .offset(offset).limit(limit)
        )
        rows = list((await session.execute(items_stmt)).scalars().all())

        count_stmt = (
            select(func.count(distinct(Order.id)))
            .select_from(Order)
            .join(OrderLine, OrderLine.order_id == Order.id)
            .join(ProductVariant, ProductVariant.id == OrderLine.variant_id)
            .join(Product, Product.id == ProductVariant.product_id)
            .where(Product.seller_id == seller_id)
        )
        total = int((await session.execute(count_stmt)).scalar_one())

    return _SellerOrderList(
        items=[
            _SellerOrderRow(
                id=o.id, code=o.code, status=o.status,
                grand_total=str(o.grand_total),
                placed_at=o.placed_at, completed_at=o.completed_at,
            )
            for o in rows
        ],
        total=total,
    )


# ────────── /seller/videos ──────────


class _SellerVideoRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    product_id: UUID
    title: str | None
    status: str
    hls_url: str | None
    thumbnail_url: str | None
    created_at: datetime


class _SellerVideoList(BaseModel):
    items: list[_SellerVideoRow]
    total: int


@router.get(
    "/videos",
    response_model=_SellerVideoList,
    dependencies=[Depends(requires_permission(_SELLER_PERM))],
)
async def list_my_videos(
    seller_id: Annotated[UUID, Depends(get_current_seller_id)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> _SellerVideoList:
    """Product videos owned by this seller. The Module 35 schema
    already has ``seller_id`` on ``product_videos``; phase 2 wired
    the upload flow to populate it from the principal."""
    async with uow.transactional() as session:
        items_stmt = (
            select(ProductVideo)
            .where(ProductVideo.seller_id == seller_id)
            .order_by(ProductVideo.created_at.desc())
            .offset(offset).limit(limit)
        )
        rows = list((await session.execute(items_stmt)).scalars().all())
        total = int(
            (await session.execute(
                select(func.count())
                .select_from(ProductVideo)
                .where(ProductVideo.seller_id == seller_id),
            )).scalar_one()
        )
    return _SellerVideoList(
        items=[_SellerVideoRow.model_validate(v) for v in rows],
        total=total,
    )


# ────────── /seller/payouts (phase 5 placeholder) ──────────


@router.get(
    "/payouts",
    dependencies=[Depends(requires_permission(_SELLER_PERM))],
)
async def list_my_payouts(
    seller_id: Annotated[UUID, Depends(get_current_seller_id)],
) -> dict:
    """Phase 5 will ship the real payout ledger + commission engine.

    Returning 501 lets seller-app developers stub against the URL
    today without faking the response shape — when phase 5 lands,
    the existing route gains real bodies and clients keep working.
    """
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail={
            "code": "feature_not_implemented",
            "message": "Payout ledger ships in sellers phase 5.",
            "seller_id": str(seller_id),
        },
    )
