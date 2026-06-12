"""FastAPI router for /checkout/*.

Routes:
  POST  /checkout/preview                preview from my cart
  GET   /checkout/{id}                   read a session
  POST  /checkout/{id}/confirm           confirm → creates an order
  POST  /checkout/{id}/cancel            cancel a DRAFT session
  POST  /checkout/{id}/apply-loyalty     stub: record points intent
  GET   /checkout/_limits                public limits config
"""
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, status

from app.core.db.uow import UnitOfWork, get_uow
from app.core.errors import NotFoundError, ValidationError
from app.core.security.deps import get_current_principal, get_optional_principal
from app.core.security.principal import Principal
from app.modules.cart.models import Cart, CartStatus
from app.modules.cart.service import CartService
from app.modules.checkout.schemas import (
    CheckoutApplyLoyaltyIn,
    CheckoutCancelIn,
    CheckoutConfirmIn,
    CheckoutLimitsOut,
    CheckoutPreviewIn,
    CheckoutSessionOut,
)
from app.modules.checkout.service import CheckoutService, session_to_out
from app.modules.orders.service import OrderService

router = APIRouter(prefix="/checkout", tags=["checkout"])


@router.get("/_limits", response_model=CheckoutLimitsOut)
async def checkout_limits() -> CheckoutLimitsOut:
    return CheckoutLimitsOut(
        max_session_age_hours=24,
        cod_enabled=True,
        online_enabled=False,  # flip to True once payment provider creds bind
        supported_payment_methods=["cod"],
    )


@router.post(
    "/preview",
    response_model=CheckoutSessionOut,
    status_code=status.HTTP_201_CREATED,
)
async def preview(
    payload: CheckoutPreviewIn,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal | None, Depends(get_optional_principal)] = None,
    x_cart_session: Annotated[str | None, Header(alias="X-Cart-Session")] = None,
) -> CheckoutSessionOut:
    """Build a DRAFT session from the caller's cart.

    Cart resolution:
      * If ``use_cart_id`` is given, use that cart directly.
      * Else if the caller is authenticated, use their open user cart.
      * Else require ``X-Cart-Session`` header (guest path).
    """
    async with uow.transactional() as session:
        cart_svc = CartService(session)
        cart: Cart
        user_id = principal.user_id if principal else None

        if payload.use_cart_id is not None:
            cart_check = await session.get(Cart, payload.use_cart_id)
            if cart_check is None or cart_check.status != CartStatus.OPEN.value:
                raise NotFoundError("Cart not found.")
            cart = cart_check
        elif user_id is not None:
            user_cart = await cart_svc.get_for_user(user_id)
            if user_cart is None:
                raise NotFoundError(
                    "No open cart for this user — add items first."
                )
            cart = user_cart
        elif x_cart_session:
            cart = await cart_svc.get_for_session(x_cart_session)
        else:
            raise ValidationError(
                "Either log in, send X-Cart-Session header, or pass use_cart_id."
            )

        co_svc = CheckoutService(session)
        sess = await co_svc.preview(
            cart=cart,
            user_id=user_id,
            shipping_address=payload.shipping_address.model_dump(),
            payment_method=payload.payment_method,
            note=payload.note,
        )
        return CheckoutSessionOut.model_validate(session_to_out(sess))


@router.get("/{session_id}", response_model=CheckoutSessionOut)
async def get_session(
    session_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal | None, Depends(get_optional_principal)] = None,
) -> CheckoutSessionOut:
    user_id = principal.user_id if principal else None
    async with uow.transactional() as session:
        svc = CheckoutService(session)
        sess = await svc.get(session_id, user_id=user_id)
        return CheckoutSessionOut.model_validate(session_to_out(sess))


@router.post("/{session_id}/cancel", response_model=CheckoutSessionOut)
async def cancel_session(
    session_id: UUID,
    payload: CheckoutCancelIn,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal | None, Depends(get_optional_principal)] = None,
) -> CheckoutSessionOut:
    user_id = principal.user_id if principal else None
    async with uow.transactional() as session:
        svc = CheckoutService(session)
        sess = await svc.get(session_id, user_id=user_id)
        sess = await svc.cancel(sess=sess, reason=payload.reason)
        return CheckoutSessionOut.model_validate(session_to_out(sess))


@router.post("/{session_id}/apply-loyalty", response_model=CheckoutSessionOut)
async def apply_loyalty(
    session_id: UUID,
    payload: CheckoutApplyLoyaltyIn,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> CheckoutSessionOut:
    async with uow.transactional() as session:
        svc = CheckoutService(session)
        sess = await svc.get(session_id, user_id=principal.user_id)
        sess = await svc.apply_loyalty(sess=sess, points=payload.points)
        return CheckoutSessionOut.model_validate(session_to_out(sess))


@router.post("/{session_id}/confirm", response_model=dict)
async def confirm(
    session_id: UUID,
    payload: CheckoutConfirmIn,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> dict:
    """Materialise the session as an order via the existing orders module.

    The orders module handles its own state machine (PENDING → PAID etc)
    and runs all the inventory + audit side-effects we'd otherwise have
    to duplicate.
    """
    from app.modules.cart.models import CartStatus

    async with uow.transactional() as session:
        co_svc = CheckoutService(session)
        sess = await co_svc.get(session_id, user_id=principal.user_id)
        if sess.status != "draft":
            raise ValidationError(
                f"Session in status '{sess.status}' cannot be confirmed."
            )

        # ---- CAPTURE IDs UP FRONT (2026-05-13) ----
        # The order placement chain below (savepoints + outbox
        # dispatch + inventory orchestrator) may expire ``sess``'s
        # attribute cache. Any lazy attribute access after that
        # raises "Can't operate on closed transaction." We grab the
        # primitive values now so the cleanup code below can write
        # via raw IDs without re-loading ``sess``.
        _cart_id_str = str(sess.cart_id) if sess.cart_id else None
        _sess_id_str = str(sess.id)
        _sess_currency = sess.currency
        _sess_shipping_addr = dict(sess.shipping_address_json or {})

        # ---- TOTALS INTEGRITY (added 2026-05-13) ----
        # Re-derive every column from snapshot_json + the same pricing
        # rules used at preview. Reject confirm + audit-log if anything
        # was tampered with between preview and confirm. The snapshot
        # itself is frozen at preview (unit prices baked into JSON), so
        # the only ways to fail are:
        #   1. Malicious UPDATE on ``checkout_sessions.*_total`` columns
        #   2. Rounding drift bug in the recompute path
        #   3. ``snapshot_json`` itself modified post-preview
        # All three deserve a hard 400 + a logged audit event.
        ok, reason = co_svc.verify_totals_integrity(sess)
        if not ok:
            from app.core.logging import get_logger
            get_logger("hypershop.checkout.audit").warning(
                "checkout_totals_tampering_detected",
                session_id=str(sess.id),
                user_id=str(principal.user_id) if principal.user_id else None,
                stored_subtotal=str(sess.subtotal),
                stored_grand=str(sess.grand_total),
                reason=reason,
            )
            raise ValidationError(
                "Checkout totals failed integrity check. "
                "Re-open the cart and start a fresh checkout.",
                details={"audit_reason": reason},
            )

        # Build order items from the frozen snapshot.
        items = [
            {"variant_id": UUID(li["variant_id"]), "quantity": li["quantity"]}
            for li in sess.snapshot_json
        ]
        # Reuse the snapshot's address for the order.
        addr = dict(sess.shipping_address_json)
        # The orders module's DeliveryAddress requires specific fields;
        # map ShippingAddressIn → that shape.
        delivery_address = {
            "recipient_name": addr.get("full_name", ""),
            "phone": addr.get("phone", ""),
            "address_line1": addr.get("address_line1", ""),
            "address_line2": addr.get("address_line2"),
            "city": addr.get("city", ""),
            "postal_code": addr.get("postal_code"),
            "country": addr.get("country", "BD"),
        }

        # ---- SANDBOX PAYMENT GATEWAY (no real provider creds) ----
        # The backend Order model only distinguishes ``cod`` vs ``online``
        # (the granular provider — bKash/Nagad/Rocket/Card/Wallet — is a
        # storefront concept rendered by the mock PaymentGateway overlay).
        # The storefront sends ``payment_method_token`` ONLY for non-COD
        # methods (the mock gateway's success token); COD sends null. So a
        # token's presence means "a gateway settled this" → place as
        # ``online`` and immediately simulate the gateway settlement
        # webhook below. No token → COD (auto-confirms at placement).
        _gateway_token = (payload.payment_method_token or "").strip() or None
        _effective_payment_method = "online" if _gateway_token else (sess.payment_method or "cod")

        order_svc = OrderService(session)
        order = await order_svc.place_order(
            principal=principal,
            items=items,
            payment_method=_effective_payment_method,
            delivery_address=delivery_address,
            notes=payload.note or sess.note,
            currency=sess.currency,
        )

        # Sandbox settlement: an online order is born ``pending_payment``.
        # A present (mock) token means the gateway returned success, so we
        # simulate the provider's settlement callback inline → the order
        # transitions pending_payment → payment_confirmed → reserves stock
        # → approved, exactly like a real gateway webhook would drive it.
        # When real bKash/SSLCommerz creds land, this block is replaced by
        # an out-of-band webhook handler verifying the txn before settling.
        _settled = False
        if _effective_payment_method == "online" and _gateway_token:
            order = await order_svc.confirm_payment(
                principal=principal,
                order_id=order.id,
                reason=f"sandbox gateway settled · token={_gateway_token[:48]}",
            )
            _settled = True

        # Persist the shipping address into the customer's address
        # book (best-effort) so subsequent checkouts pre-fill from
        # `GET /customers/addresses` instead of presenting an empty
        # form. Idempotent: matched by (line1, phone) — same address
        # updates timestamps without creating a duplicate row.
        try:
            from app.modules.mobile.models import CustomerAddress
            from sqlalchemy import select as _sa_select
            # Match on the exact address shape first; if the customer
            # already has this address, no-op. Otherwise INSERT — but
            # carefully respect ``uq_customer_addresses_one_default``
            # which permits only ONE default per customer. The old
            # code unconditionally set ``is_default=True`` on every
            # checkout, which violated the constraint and (because the
            # exception was swallowed) poisoned the outer transaction
            # — that's the bug that caused every order to silently
            # roll back. Root cause confirmed 2026-05-13.
            existing = (await session.execute(
                _sa_select(CustomerAddress).where(
                    CustomerAddress.customer_user_id == principal.user_id,
                    CustomerAddress.line1 == delivery_address["address_line1"],
                    CustomerAddress.phone == delivery_address["phone"],
                )
            )).scalar_one_or_none()
            if existing is None:
                # Check whether the customer has ANY default address
                # already. If yes, the new row is just an "additional"
                # address (is_default=False). If not, this is their
                # first address and it becomes the default.
                has_default = (await session.execute(
                    _sa_select(CustomerAddress.id).where(
                        CustomerAddress.customer_user_id == principal.user_id,
                        CustomerAddress.is_default.is_(True),
                    ).limit(1)
                )).first() is not None

                session.add(CustomerAddress(
                    customer_user_id=principal.user_id,
                    label="Home",
                    recipient_name=delivery_address["recipient_name"],
                    phone=delivery_address["phone"],
                    line1=delivery_address["address_line1"],
                    line2=delivery_address.get("address_line2"),
                    city=delivery_address["city"],
                    district=(addr.get("region") if isinstance(addr, dict) else None),
                    postal_code=delivery_address.get("postal_code"),
                    country=(delivery_address.get("country") or "BD"),
                    is_default=not has_default,
                ))
                await session.flush()
        except Exception as _addr_err:  # noqa: BLE001 — address upsert is non-critical
            # Don't 500 the order placement just because the address
            # book write failed. The order itself is already committed.
            # DIAGNOSTIC (2026-05-13): logging the swallowed error so
            # we can identify what's poisoning the outer session
            # between place_order and cart cleanup. If the underlying
            # exception is a SQL/integrity error, the session is now
            # in a "needs rollback" state and any subsequent execute
            # raises "Can't operate on closed transaction".
            from app.core.logging import get_logger as _gl
            _gl("hypershop.checkout").warning(
                "checkout_address_upsert_failed",
                error=str(_addr_err),
                error_type=type(_addr_err).__name__,
            )

        # Mark session converted + flip cart to converted.
        #
        # ⚠️ KNOWN ISSUE (documented 2026-05-13):
        # ``order_svc.place_order`` runs nested SAVEPOINTs + outbox
        # dispatch which can leave the outer transaction in a
        # "closed" state by the time control returns here. Any
        # ORM lazy-load on ``sess`` then raises. The two safe
        # outcomes are:
        #   (a) `sess.status = "confirmed"` is a pure Python set,
        #       no DB. Safe.
        #   (b) `sess.order_id = order.id` — same.
        # The lazy-load risk is in `await session.get(Cart, ...)`.
        # If that raises, we let it bubble — the FastAPI exception
        # handler returns 500, the outer ``uow.transactional()``
        # rolls back, and the customer sees a failed checkout.
        # That's better than returning 200 with un-saved data.
        sess.status = "confirmed"
        sess.order_id = order.id
        cart = await session.get(Cart, sess.cart_id)
        if cart is not None:
            cart.status = CartStatus.CONVERTED.value
        await session.flush()

        # Build the customer-web `OrderWire` shape (compatibility with
        # `fromOrder()` in packages/api-client/src/normalise.ts). Returns
        # both `id`/`order_number` aliases and the legacy `order_id`/
        # `order_code` so older consumers stay happy.
        return {
            # Stub legacy fields (kept for backwards compat)
            "session_id":       str(sess.id),
            "order_id":         str(order.id),
            "order_code":       order.code,
            # Frontend `OrderWire` canonical fields
            "id":               str(order.id),
            "order_number":     order.code,
            "customer_id":      str(order.customer_user_id) if getattr(order, "customer_user_id", None) else str(principal.user_id),
            "status":           "confirmed",
            "country_code":     "BD",
            "currency":         sess.currency or "BDT",
            "subtotal_amount":  str(getattr(order, "subtotal", "0")),
            "discount_amount":  str(getattr(order, "discount_total", "0")),
            "shipping_amount":  str(getattr(order, "shipping_total", "0")),
            "tax_amount":       str(getattr(order, "tax_total", "0")),
            "total_amount":     str(getattr(order, "grand_total", "0")),
            "placed_at":        (getattr(order, "placed_at", None).isoformat() if getattr(order, "placed_at", None) else None),
            "paid_at":          (getattr(order, "payment_confirmed_at", None).isoformat() if getattr(order, "payment_confirmed_at", None) else None),
            "shipped_at":       None,
            "delivered_at":     None,
            "items":            [],
            "lines":            [],
            "shipping_address": dict(sess.shipping_address_json or {}),
            "billing_address":  dict(sess.shipping_address_json or {}),
            "payment_method":   _effective_payment_method,
            "notes":            payload.note or sess.note,
            "history":          [],
        }
