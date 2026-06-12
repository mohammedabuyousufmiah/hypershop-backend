"""FastAPI router for cart endpoints — auth + guest surfaces in one
file because they share most of the request shape.

Routes:
  GET    /cart                              read my open cart
  POST   /cart                              bootstrap (idempotent create-or-get)
  DELETE /cart                              abandon my open cart
  POST   /cart/items                        add an item
  PATCH  /cart/items/{item_id}              update qty
  DELETE /cart/items/{item_id}              remove a line
  POST   /cart/quote                        fresh quote (price/stock diffs)
  POST   /cart/merge                        merge a guest session into mine
  GET    /cart/_limits                      public limits config

  POST   /cart/guest                        create a guest cart
  GET    /cart/guest                        read guest cart by X-Cart-Session
  DELETE /cart/guest                        abandon guest cart
  POST   /cart/guest/items                  add an item to guest cart
  PATCH  /cart/guest/items/{item_id}        update qty (guest)
  DELETE /cart/guest/items/{item_id}        remove a line (guest)
  POST   /cart/guest/quote                  fresh quote (guest)
"""
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, status
from fastapi.responses import Response

from app.core.db.uow import UnitOfWork, get_uow
from app.core.errors import NotFoundError, ValidationError
from app.core.security.deps import get_current_principal
from app.core.security.principal import Principal
from app.modules.cart.schemas import (
    CartCreateIn,
    CartItemAddIn,
    CartItemOut,
    CartItemUpdateIn,
    CartLimitsOut,
    CartMergeIn,
    CartOut,
    CartQuoteOut,
)
from app.modules.cart.service import (
    MAX_LINES_PER_CART,
    MAX_QTY_PER_LINE,
    CartService,
    cart_to_out,
    item_to_out,
)

router = APIRouter(tags=["cart"])


# ----------------- shared helpers -----------------


async def _require_guest_session(
    x_cart_session: Annotated[str | None, Header(alias="X-Cart-Session")] = None,
) -> str:
    if not x_cart_session:
        raise ValidationError(
            "Missing X-Cart-Session header.",
            details={"header": "X-Cart-Session"},
        )
    return x_cart_session


# ----------------- /cart/_limits (public) -----------------


@router.get("/cart/_limits", response_model=CartLimitsOut)
async def cart_limits() -> CartLimitsOut:
    return CartLimitsOut(
        max_qty_per_line=MAX_QTY_PER_LINE,
        max_lines_per_cart=MAX_LINES_PER_CART,
    )


# ----------------- authenticated /cart -----------------


@router.get("/cart", response_model=CartOut)
async def get_my_cart(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> CartOut:
    async with uow.transactional() as session:
        svc = CartService(session)
        cart = await svc.get_for_user(principal.user_id)
        if cart is None:
            # Auto-bootstrap an empty cart so the storefront can attach
            # items without a second round-trip.
            cart = await svc.get_or_create_for_user(
                principal.user_id, currency="BDT", country_code="BD"
            )
        return CartOut.model_validate(cart_to_out(cart))


@router.post("/cart", response_model=CartOut, status_code=status.HTTP_200_OK)
async def bootstrap_my_cart(
    payload: CartCreateIn,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> CartOut:
    async with uow.transactional() as session:
        svc = CartService(session)
        cart = await svc.get_or_create_for_user(
            principal.user_id,
            currency=payload.currency,
            country_code=payload.country_code,
        )
        return CartOut.model_validate(cart_to_out(cart))


@router.delete(
    "/cart", status_code=status.HTTP_204_NO_CONTENT, response_model=None
)
async def clear_my_cart(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> None:
    async with uow.transactional() as session:
        svc = CartService(session)
        cart = await svc.get_for_user(principal.user_id)
        if cart is None:
            return None
        await svc.clear(cart)
        return None


@router.post("/cart/items", response_model=CartItemOut, status_code=status.HTTP_201_CREATED)
async def add_to_my_cart(
    payload: CartItemAddIn,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> CartItemOut:
    async with uow.transactional() as session:
        svc = CartService(session)
        cart = await svc.get_or_create_for_user(
            principal.user_id, currency="BDT", country_code="BD"
        )
        item = await svc.add_item(
            cart, offer_id=payload.offer_id, quantity=payload.quantity
        )
        return CartItemOut.model_validate(item_to_out(item))


@router.patch("/cart/items/{item_id}", response_model=CartItemOut)
async def update_my_cart_item(
    item_id: UUID,
    payload: CartItemUpdateIn,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> CartItemOut:
    async with uow.transactional() as session:
        svc = CartService(session)
        cart = await svc.get_for_user(principal.user_id)
        if cart is None:
            raise NotFoundError("Cart not found.")
        item = await svc.update_item(
            cart, item_id=item_id, quantity=payload.quantity
        )
        return CartItemOut.model_validate(item_to_out(item))


@router.delete(
    "/cart/items/{item_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
async def remove_my_cart_item(
    item_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> None:
    async with uow.transactional() as session:
        svc = CartService(session)
        cart = await svc.get_for_user(principal.user_id)
        if cart is None:
            raise NotFoundError("Cart not found.")
        await svc.remove_item(cart, item_id=item_id)
        return None


@router.post("/cart/quote", response_model=CartQuoteOut)
async def quote_my_cart(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> CartQuoteOut:
    async with uow.transactional() as session:
        svc = CartService(session)
        cart = await svc.get_for_user(principal.user_id)
        if cart is None:
            cart = await svc.get_or_create_for_user(
                principal.user_id, currency="BDT", country_code="BD"
            )
        return CartQuoteOut.model_validate(await svc.quote(cart))


@router.post("/cart/merge", response_model=CartOut)
async def merge_guest_cart(
    payload: CartMergeIn,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> CartOut:
    async with uow.transactional() as session:
        svc = CartService(session)
        cart = await svc.merge_guest_into_user(
            user_id=principal.user_id, session_token=payload.session_token
        )
        return CartOut.model_validate(cart_to_out(cart))


# ----------------- guest /cart/guest -----------------


@router.post(
    "/cart/guest", response_model=CartOut, status_code=status.HTTP_201_CREATED
)
async def create_guest_cart(
    payload: CartCreateIn,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> CartOut:
    async with uow.transactional() as session:
        svc = CartService(session)
        cart = await svc.create_guest(
            currency=payload.currency, country_code=payload.country_code
        )
        return CartOut.model_validate(cart_to_out(cart))


@router.get("/cart/guest", response_model=CartOut)
async def get_guest_cart(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    session_token: Annotated[str, Depends(_require_guest_session)],
) -> CartOut:
    async with uow.transactional() as session:
        svc = CartService(session)
        cart = await svc.get_for_session(session_token)
        return CartOut.model_validate(cart_to_out(cart))


@router.delete(
    "/cart/guest",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
async def clear_guest_cart(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    session_token: Annotated[str, Depends(_require_guest_session)],
) -> None:
    async with uow.transactional() as session:
        svc = CartService(session)
        cart = await svc.get_for_session(session_token)
        await svc.clear(cart)
        return None


@router.post(
    "/cart/guest/items",
    response_model=CartItemOut,
    status_code=status.HTTP_201_CREATED,
)
async def add_to_guest_cart(
    payload: CartItemAddIn,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    session_token: Annotated[str, Depends(_require_guest_session)],
) -> CartItemOut:
    async with uow.transactional() as session:
        svc = CartService(session)
        cart = await svc.get_for_session(session_token)
        item = await svc.add_item(
            cart, offer_id=payload.offer_id, quantity=payload.quantity
        )
        return CartItemOut.model_validate(item_to_out(item))


@router.patch(
    "/cart/guest/items/{item_id}", response_model=CartItemOut
)
async def update_guest_cart_item(
    item_id: UUID,
    payload: CartItemUpdateIn,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    session_token: Annotated[str, Depends(_require_guest_session)],
) -> CartItemOut:
    async with uow.transactional() as session:
        svc = CartService(session)
        cart = await svc.get_for_session(session_token)
        item = await svc.update_item(
            cart, item_id=item_id, quantity=payload.quantity
        )
        return CartItemOut.model_validate(item_to_out(item))


@router.delete(
    "/cart/guest/items/{item_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
async def remove_guest_cart_item(
    item_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    session_token: Annotated[str, Depends(_require_guest_session)],
) -> None:
    async with uow.transactional() as session:
        svc = CartService(session)
        cart = await svc.get_for_session(session_token)
        await svc.remove_item(cart, item_id=item_id)
        return None


@router.post("/cart/guest/quote", response_model=CartQuoteOut)
async def quote_guest_cart(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    session_token: Annotated[str, Depends(_require_guest_session)],
) -> CartQuoteOut:
    async with uow.transactional() as session:
        svc = CartService(session)
        cart = await svc.get_for_session(session_token)
        return CartQuoteOut.model_validate(await svc.quote(cart))
