"""Customer-facing wishlist endpoints.

  GET    /wishlist                  — list (paged, denormalised)
  GET    /wishlist/contains/{pid}   — heart-icon state on PDP
  POST   /wishlist                  — add (idempotent)
  DELETE /wishlist/{product_id}     — remove (forgiving)

All paths self-scope to the caller's user_id; there's no way to read
or mutate another user's wishlist through this router.
"""
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status

from app.core.db.uow import UnitOfWork, get_uow
from app.core.security.deps import get_current_principal
from app.core.security.principal import Principal
from app.core.security.rbac import requires_permission
from app.modules.wishlist.schemas import (
    WishlistAddIn,
    WishlistContainsOut,
    WishlistItemOut,
    WishlistOut,
)
from app.modules.wishlist.service import WishlistService

router = APIRouter(prefix="/wishlist", tags=["wishlist"])

# Reuse the customer-self read permission — anyone allowed to read
# their loyalty / referrals can manage their wishlist.
_R = "loyalty.read.self"


@router.get(
    "",
    response_model=WishlistOut,
    dependencies=[Depends(requires_permission(_R))],
    summary="List the caller's wishlist (newest first)",
)
async def list_wishlist(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> WishlistOut:
    async with uow.transactional() as session:
        svc = WishlistService(session)
        items, total = await svc.list_for_user(
            user_id=principal.user_id, offset=offset, limit=limit,
        )
    return WishlistOut(
        items=[WishlistItemOut.model_validate(it) for it in items],
        total=total,
    )


@router.get(
    "/contains/{product_id}",
    response_model=WishlistContainsOut,
    dependencies=[Depends(requires_permission(_R))],
    summary="Whether (caller, product_id) is in the wishlist",
)
async def wishlist_contains(
    product_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> WishlistContainsOut:
    async with uow.transactional() as session:
        svc = WishlistService(session)
        present = await svc.contains(
            user_id=principal.user_id, product_id=product_id,
        )
    return WishlistContainsOut(in_wishlist=present)


@router.post(
    "",
    response_model=WishlistItemOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(requires_permission(_R))],
    summary="Add a product to the caller's wishlist (idempotent)",
)
async def add_to_wishlist(
    payload: WishlistAddIn,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> WishlistItemOut:
    async with uow.transactional() as session:
        svc = WishlistService(session)
        await svc.add(
            user_id=principal.user_id, product_id=payload.product_id,
        )
        # Reload via list_for_user so the denormalised product fields
        # come along — the heart click is the only chance to populate
        # the wishlist cache on the client side.
        items, _ = await svc.list_for_user(
            user_id=principal.user_id, offset=0, limit=1,
        )
        if items:
            return WishlistItemOut.model_validate(items[0])
        # Defensive — service guarantees a row, but stick to a 201.
        from app.core.errors import ConflictError
        raise ConflictError("Wishlist insert succeeded but row not found.")


@router.delete(
    "/{product_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    response_class=Response,
    dependencies=[Depends(requires_permission(_R))],
    summary="Remove a product from the caller's wishlist (forgiving)",
)
async def remove_from_wishlist(
    product_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> Response:
    async with uow.transactional() as session:
        svc = WishlistService(session)
        await svc.remove(
            user_id=principal.user_id, product_id=product_id,
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
