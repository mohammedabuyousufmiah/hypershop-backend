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
    StatusTransitionRequest,
)
from app.modules.orders.service import OrderService

router = APIRouter(prefix="/admin/orders", tags=["admin-orders"])

_READ = "orders.view"        # canonical short-form (2026-05-16); formal alias "order.read.any" still in catalog
_FULFILL = "order.fulfill"   # workflow-state transitions (confirm-payment / approve-rx / pack / dispatch / complete) — stays formal, no short-form equivalent
_CANCEL = "orders.cancel"    # canonical short-form (2026-05-16); formal alias "order.cancel.any" still in catalog


@router.get(
    "",
    response_model=Page[OrderSummary],
    summary="List orders (admin/staff)",
    dependencies=[Depends(requires_permission(_READ))],
)
async def admin_list_orders(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    page: int = Query(default=1, ge=1, le=10_000),
    # Hard upper bound (200) preserved here so a missing config row
    # doesn't let callers blow the cap. The runtime cap from
    # ``orders.max_page_size`` then clamps DOWN inside the handler.
    size: int = Query(default=20, ge=1, le=200),
    status_filter: str | None = Query(default=None, alias="status"),
    customer_user_id: UUID | None = Query(default=None),
) -> Page[OrderSummary]:
    from app.modules.admin_config.service import ModuleConfigService
    async with uow.transactional() as session:
        cfg = ModuleConfigService(session)
        # PageParams hard-caps at 100 internally (BaseModel validator),
        # so clamp BEFORE construction. The module setting is allowed
        # to be HIGHER than the schema cap — endpoint operators can
        # raise the runtime cap up to the Query() upper bound (200)
        # without redeploying.
        cap = await cfg.get_int("orders", "max_page_size", default=100)
        effective_size = min(size, cap, 100)
        params = PageParams(page=page, size=effective_size)
        repo = OrderRepository(session)
        rows, total = await repo.list_admin(
            offset=params.offset,
            limit=params.limit,
            status=status_filter,
            customer_user_id=customer_user_id,
        )
    return Page.build(
        items=[order_to_summary(o) for o in rows], total=total, params=params,
    )


@router.get(
    "/{order_id}",
    response_model=OrderResponse,
    dependencies=[Depends(requires_permission(_READ))],
)
async def admin_get_order(
    order_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> OrderResponse:
    async with uow.transactional() as session:
        svc = OrderService(session)
        return order_to_response(await svc.get_admin(order_id))


@router.post(
    "/{order_id}/confirm-payment",
    response_model=OrderResponse,
    summary="Confirm online-payment receipt for an order",
    description=(
        "Used by the payments module (or admin override) to advance an "
        "online-payment order from ``pending_payment`` to ``payment_confirmed``. "
        "Triggers FEFO reservation inline; on success the order auto-advances "
        "to ``stock_reserved`` and then ``approved``. On insufficient stock "
        "the order moves to ``failed`` so finance can refund."
    ),
    dependencies=[Depends(requires_permission(_FULFILL))],
)
async def admin_confirm_payment(
    order_id: UUID,
    payload: StatusTransitionRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> OrderResponse:
    async with uow.transactional() as session:
        svc = OrderService(session)
        order = await svc.confirm_payment(
            principal=principal, order_id=order_id, reason=payload.reason,
        )
        return order_to_response(order)


@router.post(
    "/{order_id}/start-packing",
    response_model=OrderResponse,
    dependencies=[Depends(requires_permission(_FULFILL))],
)
async def admin_start_packing(
    order_id: UUID,
    payload: StatusTransitionRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> OrderResponse:
    async with uow.transactional() as session:
        svc = OrderService(session)
        order = await svc.start_packing(
            principal=principal, order_id=order_id, reason=payload.reason,
        )
        return order_to_response(order)


@router.post(
    "/{order_id}/dispatch",
    response_model=OrderResponse,
    summary="Hand off to rider — packing → out_for_delivery",
    dependencies=[Depends(requires_permission(_FULFILL))],
)
async def admin_dispatch(
    order_id: UUID,
    payload: StatusTransitionRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> OrderResponse:
    async with uow.transactional() as session:
        svc = OrderService(session)
        order = await svc.dispatch(
            principal=principal, order_id=order_id, reason=payload.reason,
        )
        return order_to_response(order)


@router.post(
    "/{order_id}/complete",
    response_model=OrderResponse,
    summary="Mark order delivered — emits ``orders.order.completed`` to inventory",
    dependencies=[Depends(requires_permission(_FULFILL))],
)
async def admin_complete(
    order_id: UUID,
    payload: StatusTransitionRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> OrderResponse:
    async with uow.transactional() as session:
        svc = OrderService(session)
        order = await svc.complete(
            principal=principal, order_id=order_id, reason=payload.reason,
        )
        return order_to_response(order)


@router.post(
    "/{order_id}/cancel",
    response_model=OrderResponse,
    summary="Admin cancellation — allowed from any non-terminal state",
    dependencies=[Depends(requires_permission(_CANCEL))],
)
async def admin_cancel(
    order_id: UUID,
    payload: CancelOrderRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> OrderResponse:
    async with uow.transactional() as session:
        svc = OrderService(session)
        order = await svc.cancel_by_admin(
            principal=principal, order_id=order_id, reason=payload.reason,
        )
        return order_to_response(order)
