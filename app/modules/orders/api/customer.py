from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.core.db.uow import UnitOfWork, get_uow
from app.core.pagination import Page, PageParams
from app.core.security.deps import get_current_principal
from app.core.security.principal import Principal
from app.core.security.rbac import requires_permission
from app.modules.orders.api._serializers import order_to_response, order_to_summary
from app.modules.orders.repository import OrderRepository
from app.modules.orders.schemas import (
    CancelOrderRequest,
    OrderResponse,
    OrderSummary,
    PlaceOrderRequest,
)
from app.modules.orders.service import OrderService

router = APIRouter(prefix="/orders", tags=["orders"])

_PLACE = "order.place"
_READ_SELF = "order.read.self"


@router.post(
    "",
    response_model=OrderResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Place a new order",
    description=(
        "Snapshot the cart at checkout time, validate every line against the "
        "current product catalogue, and create the order. For COD the order "
        "advances immediately to ``payment_confirmed``; for online payment it "
        "starts in ``pending_payment`` awaiting the gateway hook."
    ),
    dependencies=[Depends(requires_permission(_PLACE))],
)
async def place_order(
    payload: PlaceOrderRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> OrderResponse:
    async with uow.transactional() as session:
        svc = OrderService(session)
        order = await svc.place_order(
            principal=principal,
            items=[item.model_dump() for item in payload.items],
            payment_method=payload.payment_method,
            delivery_address=payload.delivery_address.model_dump(),
            notes=payload.notes,
            currency=payload.currency,
        )
        return order_to_response(order)


@router.get(
    "",
    response_model=Page[OrderSummary],
    summary="List the calling customer's own orders",
    dependencies=[Depends(requires_permission(_READ_SELF))],
)
async def list_my_orders(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    page: int = Query(default=1, ge=1, le=10_000),
    size: int = Query(default=20, ge=1, le=100),
    status_filter: str | None = Query(default=None, alias="status"),
) -> Page[OrderSummary]:
    params = PageParams(page=page, size=size)
    async with uow.transactional() as session:
        repo = OrderRepository(session)
        rows, total = await repo.list_for_customer(
            customer_user_id=principal.user_id,
            offset=params.offset,
            limit=params.limit,
            status=status_filter,
        )
    return Page.build(
        items=[order_to_summary(o) for o in rows], total=total, params=params,
    )


@router.get(
    "/{order_id}",
    response_model=OrderResponse,
    summary="Get one of the calling customer's orders",
    dependencies=[Depends(requires_permission(_READ_SELF))],
)
async def get_my_order(
    order_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> OrderResponse:
    async with uow.transactional() as session:
        svc = OrderService(session)
        order = await svc.get_for_customer(principal=principal, order_id=order_id)
        return order_to_response(order)


@router.post(
    "/{order_id}/cancel",
    response_model=OrderResponse,
    summary="Customer-initiated order cancellation",
    description=(
        "Allowed only while the order is still in an early state (before "
        "packing). After packing, an admin must perform the cancellation."
    ),
    dependencies=[Depends(requires_permission(_READ_SELF))],
)
async def cancel_my_order(
    order_id: UUID,
    payload: CancelOrderRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> OrderResponse:
    async with uow.transactional() as session:
        svc = OrderService(session)
        order = await svc.cancel_by_customer(
            principal=principal, order_id=order_id, reason=payload.reason,
        )
        return order_to_response(order)
