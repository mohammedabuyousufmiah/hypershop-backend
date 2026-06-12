"""Cross-module authz helpers for the sellers concern — phase 2.

Used by the catalog module's write paths (and Module 35's video
upload) to enforce: admins can write any product; seller users can
only write products they own; non-seller non-admin users can't
write at all.

Kept in its own file so the catalog module imports a tiny surface
rather than the full sellers service.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ForbiddenError, NotFoundError
from app.core.security.principal import Principal
from app.modules.catalog.models import Product
from app.modules.sellers.codes import (
    HYPERSHOP_DIRECT_BUSINESS_NAME,
    SELLER_ROLE_OWNER,
    SELLER_ROLE_MANAGER,
    SELLER_ROLE_STAFF,
    STATUS_APPROVED,
)
from app.modules.sellers.models import Seller, SellerUser

# Roles inside a seller account that are allowed to mutate the
# seller's catalog. Phase-1 keeps this generous — staff can write
# products. Phase 5 may tighten staff to read-only when the payout
# engine adds risk-controlled actions.
_WRITE_ROLES = frozenset({
    SELLER_ROLE_OWNER, SELLER_ROLE_MANAGER, SELLER_ROLE_STAFF,
})


async def seller_id_for_user(
    session: AsyncSession, user_id: UUID,
) -> UUID | None:
    """Return the seller_id this user is linked to, or None.

    Returns None for admins / staff / customers who have no entry
    in ``seller_users``. Callers should treat None + admin role as
    "operates on Hypershop Direct's behalf"; None + non-admin role
    means "not a seller, can't write product rows".
    """
    stmt = select(SellerUser.seller_id).where(SellerUser.user_id == user_id)
    return (await session.execute(stmt)).scalar_one_or_none()


async def seller_user_role(
    session: AsyncSession, user_id: UUID,
) -> str | None:
    stmt = select(SellerUser.role).where(SellerUser.user_id == user_id)
    return (await session.execute(stmt)).scalar_one_or_none()


async def hypershop_direct_seller_id(session: AsyncSession) -> UUID:
    """The slug 'hypershop-direct' is seeded by migration 0033."""
    stmt = select(Seller.id).where(Seller.slug == "hypershop-direct")
    sid = (await session.execute(stmt)).scalar_one_or_none()
    if sid is None:
        # Should not happen in any deployed environment — migration
        # 0033 always seeds this row. Surface a 500 rather than
        # silently fabricating a UUID.
        raise NotFoundError(
            f"'{HYPERSHOP_DIRECT_BUSINESS_NAME}' seller row missing — "
            f"migration 0033 not applied?",
        )
    return sid


async def resolve_owner_seller_id(
    session: AsyncSession, principal: Principal,
) -> UUID:
    """Compute the seller_id to stamp on a freshly-created product.

    - Admin (``*`` permission) without a seller link → Hypershop Direct
    - User linked to an approved seller → that seller's id
    - Anyone else → Hypershop Direct (defensive default; admin RBAC
      check upstream should already have rejected the call)

    The function never raises Forbidden — RBAC is the catalog
    endpoint's job. This is a pure resolver.
    """
    sid = await seller_id_for_user(session, principal.user_id)
    if sid is not None:
        seller = await session.get(Seller, sid)
        if seller is not None and seller.status == STATUS_APPROVED:
            return sid
        # Linked seller exists but isn't approved — fall through to
        # Hypershop Direct so the product still lands somewhere.
        # The catalog write should have been blocked by the RBAC
        # check; this is defensive.
    return await hypershop_direct_seller_id(session)


async def assert_can_write_product(
    session: AsyncSession,
    principal: Principal,
    product_id: UUID,
) -> Product:
    """Raise ForbiddenError unless the principal owns the product.

    Returns the loaded ``Product`` row so callers don't have to
    re-fetch it. Raises NotFoundError if the product doesn't exist.

    Authorisation rules:
      - Admin (`*` permission) → always allowed
      - Seller user with role in ``_WRITE_ROLES`` AND
        ``principal.seller_id == product.seller_id`` → allowed
      - Anyone else → ForbiddenError
    """
    product = await session.get(Product, product_id)
    if product is None:
        raise NotFoundError("Product not found.")

    # Wildcard / admin bypass.
    if "*" in principal.permissions:
        return product

    seller_id = await seller_id_for_user(session, principal.user_id)
    if seller_id is None:
        raise ForbiddenError(
            "Only sellers + admins can edit catalog products.",
            details={"reason": "no_seller_link"},
        )
    if product.seller_id != seller_id:
        raise ForbiddenError(
            "You can only edit products belonging to your seller account.",
            details={"reason": "cross_seller_write"},
        )
    role = await seller_user_role(session, principal.user_id)
    if role not in _WRITE_ROLES:
        raise ForbiddenError(
            "Your seller role does not permit catalog writes.",
            details={"reason": "role_lacks_write", "role": role},
        )
    return product
