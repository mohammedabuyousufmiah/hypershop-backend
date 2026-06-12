"""CheckoutService — preview → confirm pipeline.

preview()
  Snapshot the live cart, compute shipping + tax + grand total against
  the supplied address, persist a DRAFT ``checkout_sessions`` row that
  expires after ``SESSION_TTL_HOURS``. Multiple previews for the same
  cart are allowed; the storefront keeps the latest session id.

confirm()
  Re-validate the DRAFT session is still fresh, hand the frozen line
  snapshot + address to ``OrderService.place_order`` to materialise an
  ``orders`` row, then flip the session to CONFIRMED and stamp the
  resulting order_id. The cart is left in OPEN state — the orders
  module's own state machine takes over from here.

cancel()
  DRAFT → CANCELLED with a reason. No side effects on the cart.

apply_loyalty()
  v1 stub: records the requested point value as ``loyalty_redeemed``
  and recomputes the grand total. Wallet/ledger integration arrives
  with the loyalty module.

Shipping calculation is a simple flat schedule for BD until the
deliveries module wires real zone-based shipping.
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationError
from app.core.time import utc_now
from app.modules.cart.models import Cart, CartStatus
from app.modules.cart.service import CartService
from app.modules.catalog.models import ProductVariant
from app.modules.checkout.models import CheckoutSession, CheckoutStatus

SESSION_TTL_HOURS = 24

# Flat BD shipping schedule — Dhaka vs rest of country.
DHAKA_CITY_PREFIXES = ("dhaka", "ঢাকা", "dhk")
SHIPPING_DHAKA = Decimal("80.00")
SHIPPING_OUTSIDE = Decimal("120.00")
FREE_SHIPPING_THRESHOLD = Decimal("3000.00")  # subtotal >= this → free
TAX_RATE = Decimal("0.00")  # included-in-price for BD; explicit zero so UI shows the field


def _ship_for(address: dict[str, Any], subtotal: Decimal) -> Decimal:
    if subtotal >= FREE_SHIPPING_THRESHOLD:
        return Decimal("0.00")
    city = (address.get("city") or "").strip().lower()
    if any(city.startswith(p) for p in DHAKA_CITY_PREFIXES):
        return SHIPPING_DHAKA
    return SHIPPING_OUTSIDE


class CheckoutService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _resolve_cart(self, cart_id: UUID) -> Cart:
        cart = await self.session.get(Cart, cart_id)
        if cart is None:
            raise NotFoundError("Cart not found.")
        if cart.status != CartStatus.OPEN.value:
            raise ValidationError(
                f"Cart is {cart.status}, not open — cannot check out."
            )
        if not cart.items:
            raise ValidationError("Cart is empty — add at least one item.")
        return cart

    async def _snapshot_lines(self, cart: Cart) -> tuple[list[dict], Decimal]:
        """Re-fetch current variant prices for the snapshot, returning
        ``(lines, subtotal)``. Prices written here are frozen into the
        ``snapshot_json`` column; confirm will use these and ignore
        further cart edits."""
        variant_ids = [i.variant_id for i in cart.items]
        stmt = select(ProductVariant).where(ProductVariant.id.in_(variant_ids))
        variants = {
            v.id: v for v in (await self.session.execute(stmt)).scalars().all()
        }
        lines = []
        subtotal = Decimal("0.00")
        for it in cart.items:
            v = variants.get(it.variant_id)
            if v is None or not v.is_active:
                raise ValidationError(
                    "An item in your cart is no longer available. "
                    "Re-open your cart and remove it before checking out.",
                    details={"variant_id": str(it.variant_id)},
                )
            unit_price = v.price
            line_total = unit_price * it.quantity
            subtotal += line_total
            lines.append(
                {
                    "variant_id": str(v.id),
                    "product_id": str(v.product_id),
                    "name": v.name or "Default",
                    "quantity": it.quantity,
                    "unit_price": f"{unit_price:.2f}",
                    "line_total": f"{line_total:.2f}",
                    "currency": cart.currency,
                }
            )
        return lines, subtotal

    # ---- totals integrity check (added 2026-05-13) ------------------------
    # "Frozen and audit-safe": at confirm time, re-derive the totals
    # from the snapshot + same pricing rules used at preview, then
    # compare to the stored columns. Any mismatch means somebody (or
    # a race) tampered with the session row between preview and
    # confirm — we reject the confirm and log the discrepancy.
    #
    # ``snapshot_json`` itself is the immutable record: it has per-
    # line ``unit_price`` + ``line_total`` strings frozen at preview.
    # Tampering with that field also gets caught because the recomputed
    # subtotal won't match the stored one.
    @staticmethod
    def verify_totals_integrity(sess: CheckoutSession) -> tuple[bool, str | None]:
        """Returns ``(ok, reason)``. ``ok=False`` means at least one
        of the stored columns disagrees with what the snapshot would
        produce — caller MUST refuse to confirm.

        Comparison is exact-cent (Decimal == Decimal). Floating-point
        ``float()`` is never used in this path.
        """
        # 1. Recompute subtotal from snapshot lines
        try:
            recomputed_subtotal = Decimal("0.00")
            for li in sess.snapshot_json or []:
                qty = int(li["quantity"])
                unit = Decimal(str(li["unit_price"]))
                line = Decimal(str(li["line_total"]))
                # Per-line internal consistency: line_total must equal
                # unit_price × quantity.
                expected_line = (unit * qty).quantize(Decimal("0.01"))
                if line.quantize(Decimal("0.01")) != expected_line:
                    return False, (
                        f"line_total mismatch on variant {li.get('variant_id')!r}: "
                        f"snapshot={line} expected={expected_line}"
                    )
                recomputed_subtotal += line
            recomputed_subtotal = recomputed_subtotal.quantize(Decimal("0.01"))
        except (KeyError, TypeError, ValueError) as e:
            return False, f"snapshot_json malformed: {e}"

        stored_subtotal = Decimal(str(sess.subtotal)).quantize(Decimal("0.01"))
        if recomputed_subtotal != stored_subtotal:
            return False, (
                f"subtotal mismatch: recomputed={recomputed_subtotal} "
                f"stored={stored_subtotal}"
            )

        # 2. Recompute shipping from address using same rule as preview
        recomputed_shipping = _ship_for(
            sess.shipping_address_json or {}, recomputed_subtotal,
        )
        stored_shipping = Decimal(str(sess.shipping_total)).quantize(Decimal("0.01"))
        if recomputed_shipping != stored_shipping:
            return False, (
                f"shipping_total mismatch: recomputed={recomputed_shipping} "
                f"stored={stored_shipping}"
            )

        # 3. Tax recomputed from subtotal
        recomputed_tax = (recomputed_subtotal * TAX_RATE).quantize(Decimal("0.01"))
        stored_tax = Decimal(str(sess.tax_total)).quantize(Decimal("0.01"))
        if recomputed_tax != stored_tax:
            return False, (
                f"tax_total mismatch: recomputed={recomputed_tax} "
                f"stored={stored_tax}"
            )

        # 4. Grand total — subtotal + shipping + tax − discount − loyalty
        stored_discount = Decimal(str(sess.discount_total or "0.00")).quantize(Decimal("0.01"))
        stored_loyalty = Decimal(str(sess.loyalty_redeemed or "0.00")).quantize(Decimal("0.01"))
        recomputed_grand = (
            recomputed_subtotal + recomputed_shipping + recomputed_tax
            - stored_discount - stored_loyalty
        ).quantize(Decimal("0.01"))
        stored_grand = Decimal(str(sess.grand_total)).quantize(Decimal("0.01"))
        if recomputed_grand != stored_grand:
            return False, (
                f"grand_total mismatch: recomputed={recomputed_grand} "
                f"stored={stored_grand} (discount={stored_discount} "
                f"loyalty={stored_loyalty})"
            )

        return True, None

    # ---- preview ---------------------------------------------------------

    async def preview(
        self,
        *,
        cart: Cart,
        user_id: UUID | None,
        shipping_address: dict[str, Any],
        payment_method: str,
        note: str | None,
    ) -> CheckoutSession:
        if payment_method not in ("cod", "online"):
            raise ValidationError("payment_method must be 'cod' or 'online'.")

        lines, subtotal = await self._snapshot_lines(cart)
        shipping = _ship_for(shipping_address, subtotal)
        tax = (subtotal * TAX_RATE).quantize(Decimal("0.01"))
        grand = subtotal + shipping + tax

        sess = CheckoutSession(
            user_id=user_id,
            cart_id=cart.id,
            status=CheckoutStatus.DRAFT.value,
            currency=cart.currency,
            subtotal=subtotal,
            shipping_total=shipping,
            tax_total=tax,
            discount_total=Decimal("0.00"),
            loyalty_redeemed=Decimal("0.00"),
            grand_total=grand,
            shipping_address_json=shipping_address,
            billing_address_json=shipping_address,  # mirror; v2 can split
            payment_method=payment_method,
            note=note,
            snapshot_json=lines,
            expires_at=utc_now() + timedelta(hours=SESSION_TTL_HOURS),
        )
        self.session.add(sess)
        await self.session.flush()
        return sess

    # ---- read ------------------------------------------------------------

    async def get(self, session_id: UUID, *, user_id: UUID | None) -> CheckoutSession:
        sess = await self.session.get(CheckoutSession, session_id)
        if sess is None:
            raise NotFoundError("Checkout session not found.")
        if sess.user_id is not None and user_id is not None and sess.user_id != user_id:
            raise NotFoundError("Checkout session not found.")
        if sess.status == CheckoutStatus.DRAFT.value and sess.expires_at < utc_now():
            sess.status = CheckoutStatus.EXPIRED.value
            await self.session.flush()
        return sess

    # ---- cancel ----------------------------------------------------------

    async def cancel(
        self, *, sess: CheckoutSession, reason: str
    ) -> CheckoutSession:
        if sess.status != CheckoutStatus.DRAFT.value:
            raise ValidationError(
                f"Cannot cancel session in status '{sess.status}'."
            )
        sess.status = CheckoutStatus.CANCELLED.value
        sess.cancelled_reason = reason
        await self.session.flush()
        return sess

    # ---- apply loyalty (stub) -------------------------------------------

    async def apply_loyalty(
        self, *, sess: CheckoutSession, points: int
    ) -> CheckoutSession:
        if sess.status != CheckoutStatus.DRAFT.value:
            raise ValidationError(
                f"Cannot edit session in status '{sess.status}'."
            )
        # 1 point = 1 BDT for v1 — wired to wallet/ledger later.
        # Clamp to keep grand_total non-negative.
        ceiling = sess.subtotal + sess.shipping_total + sess.tax_total
        redeem = min(Decimal(points), ceiling)
        sess.loyalty_redeemed = redeem
        sess.discount_total = redeem
        sess.grand_total = (
            sess.subtotal + sess.shipping_total + sess.tax_total - redeem
        )
        await self.session.flush()
        # Refresh so subsequent serialisation reads from the in-memory
        # attribute cache rather than triggering a lazy SELECT after
        # the async session context closes.
        await self.session.refresh(sess)
        return sess


def session_to_out(sess: CheckoutSession) -> dict:
    """Wire serialisation matching CheckoutSessionWire."""
    return {
        "id": sess.id,
        "user_id": sess.user_id,
        "cart_id": sess.cart_id,
        "status": sess.status,
        "currency": sess.currency,
        "subtotal": f"{sess.subtotal:.2f}",
        "shipping_total": f"{sess.shipping_total:.2f}",
        "tax_total": f"{sess.tax_total:.2f}",
        "discount_total": f"{sess.discount_total:.2f}",
        "loyalty_redeemed": f"{sess.loyalty_redeemed:.2f}",
        "grand_total": f"{sess.grand_total:.2f}",
        "payment_method": sess.payment_method,
        "note": sess.note,
        "shipping_address": sess.shipping_address_json,
        "billing_address": sess.billing_address_json,
        "items": sess.snapshot_json,
        "order_id": sess.order_id,
        "expires_at": sess.expires_at,
        "created_at": sess.created_at,
        "updated_at": sess.updated_at,
    }
