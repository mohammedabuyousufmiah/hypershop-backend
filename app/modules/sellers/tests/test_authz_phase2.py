"""Phase-2 authz tests — products.seller_id + cross-seller write block.

End-to-end through the real DB (testcontainers): seeds two sellers,
registers a user under each, exercises the catalog service's
write paths to verify:

  1. Admin can write any product
  2. Seller user can write own product
  3. Seller user CANNOT write another seller's product (403)
  4. Non-seller non-admin user cannot write any product (403)
  5. Create stamps the right seller_id automatically
  6. Update cannot change seller_id (silently dropped)
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select

from app.core.db.session import get_sessionmaker
from app.core.errors import ForbiddenError
from app.core.security.principal import Principal
from app.modules.catalog.models import Product
from app.modules.catalog.service import CatalogService
from app.modules.sellers.authz import (
    assert_can_write_product,
    hypershop_direct_seller_id,
    resolve_owner_seller_id,
    seller_id_for_user,
)
from app.modules.sellers.codes import SELLER_ROLE_OWNER
from app.modules.sellers.service import SellerService

pytestmark = pytest.mark.integration


def _admin_principal(user_id) -> Principal:
    return Principal(
        user_id=user_id,
        session_id=uuid4(),
        roles=frozenset({"admin"}),
        permissions=frozenset({"*"}),
    )


def _seller_user_principal(user_id) -> Principal:
    return Principal(
        user_id=user_id,
        session_id=uuid4(),
        roles=frozenset({"customer"}),
        permissions=frozenset({"catalog.product.write"}),
    )


def _customer_principal(user_id) -> Principal:
    return Principal(
        user_id=user_id,
        session_id=uuid4(),
        roles=frozenset({"customer"}),
        permissions=frozenset({"catalog.product.write"}),
    )


async def _create_seller(admin_user, slug: str, name: str):
    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        svc = SellerService(s)
        seller = await svc.create(
            business_name=name, slug=slug,
            contact_email=None, contact_phone=None,
            principal=_admin_principal(admin_user["user_id"]),
        )
        await svc.submit_kyc(
            seller_id=seller.id,
            tin="1", nid="2",
            bank_account_name="X", bank_account_number="3",
            bank_name="Y", bank_branch=None, trade_license_no=None,
            principal=_admin_principal(admin_user["user_id"]),
        )
        await svc.approve(
            seller_id=seller.id,
            principal=_admin_principal(admin_user["user_id"]),
        )
    return seller.id


async def _link_user(admin_user, seller_id, user_id):
    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        svc = SellerService(s)
        await svc.link_user(
            seller_id=seller_id,
            user_id=user_id,
            role=SELLER_ROLE_OWNER,
            principal=_admin_principal(admin_user["user_id"]),
        )


# ───────── 1. Hypershop Direct seller exists ─────────


async def test_hypershop_direct_resolvable():
    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        sid = await hypershop_direct_seller_id(s)
    assert sid is not None


# ───────── 2. seller_id_for_user resolves correctly ─────────


async def test_seller_id_for_unlinked_user(registered_user):
    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        sid = await seller_id_for_user(s, registered_user["user_id"])
    assert sid is None


async def test_seller_id_for_linked_user(admin_user, registered_user):
    seller_id = await _create_seller(admin_user, "alpha-co", "Alpha Co")
    await _link_user(admin_user, seller_id, registered_user["user_id"])
    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        sid = await seller_id_for_user(s, registered_user["user_id"])
    assert sid == seller_id


# ───────── 3. resolve_owner_seller_id ─────────


async def test_resolve_admin_falls_back_to_hypershop_direct(admin_user):
    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        owner = await resolve_owner_seller_id(
            s, _admin_principal(admin_user["user_id"]),
        )
        direct = await hypershop_direct_seller_id(s)
    assert owner == direct


async def test_resolve_seller_user_returns_their_seller(
    admin_user, registered_user,
):
    seller_id = await _create_seller(admin_user, "beta-co", "Beta Co")
    await _link_user(admin_user, seller_id, registered_user["user_id"])
    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        owner = await resolve_owner_seller_id(
            s, _seller_user_principal(registered_user["user_id"]),
        )
    assert owner == seller_id


# ───────── 4. assert_can_write_product ─────────


async def test_admin_can_write_any_product(admin_user):
    """Use Hypershop Direct as the owning seller for this fixture."""
    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        direct_id = await hypershop_direct_seller_id(s)
    # Create a product owned by Hypershop Direct
    pid = await _seed_product(seller_id=direct_id)
    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        product = await assert_can_write_product(
            s, _admin_principal(admin_user["user_id"]), pid,
        )
    assert product.id == pid


async def test_cross_seller_write_blocked(admin_user, registered_user):
    seller_a = await _create_seller(admin_user, "gamma-co", "Gamma Co")
    seller_b = await _create_seller(admin_user, "delta-co", "Delta Co")
    # registered_user is linked to seller A only
    await _link_user(admin_user, seller_a, registered_user["user_id"])
    # Product owned by seller B
    pid_b = await _seed_product(seller_id=seller_b)
    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        with pytest.raises(ForbiddenError) as exc:
            await assert_can_write_product(
                s, _seller_user_principal(registered_user["user_id"]), pid_b,
            )
    assert "cross_seller_write" in str(exc.value.details)


async def test_non_seller_user_blocked(admin_user, registered_user):
    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        direct_id = await hypershop_direct_seller_id(s)
    pid = await _seed_product(seller_id=direct_id)
    # registered_user is NOT linked to any seller
    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        with pytest.raises(ForbiddenError) as exc:
            await assert_can_write_product(
                s, _customer_principal(registered_user["user_id"]), pid,
            )
    assert "no_seller_link" in str(exc.value.details)


# ───────── 5. CatalogService.create_product stamps seller_id ─────────


async def test_create_product_stamps_admin_to_hypershop_direct(admin_user):
    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        svc = CatalogService(s)
        product = await svc.create_product(
            principal=_admin_principal(admin_user["user_id"]),
            slug="test-stamping-admin",
            name="Test Stamp Admin",
            short_description=None,
            description=None,
            brand_id=None,
            category_id=None,
            base_currency="BDT",
            tax_class="standard",
            attributes={},
            status="draft",
            variants=[{
                "name": "default",
                "price": "10.00",
                "currency": "BDT",
                "is_active": True,
            }],
            media=[],
            is_medicine=False,
        )
        direct_id = await hypershop_direct_seller_id(s)
    assert product.seller_id == direct_id


async def test_create_product_stamps_seller_user_to_their_seller(
    admin_user, registered_user,
):
    seller_id = await _create_seller(admin_user, "epsilon-co", "Epsilon Co")
    await _link_user(admin_user, seller_id, registered_user["user_id"])
    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        svc = CatalogService(s)
        product = await svc.create_product(
            principal=_seller_user_principal(registered_user["user_id"]),
            slug="test-stamping-seller",
            name="Test Stamp Seller",
            short_description=None,
            description=None,
            brand_id=None,
            category_id=None,
            base_currency="BDT",
            tax_class="standard",
            attributes={},
            status="draft",
            variants=[{
                "name": "default",
                "price": "20.00",
                "currency": "BDT",
                "is_active": True,
            }],
            media=[],
            is_medicine=False,
        )
    assert product.seller_id == seller_id


# ───────── 6. update_product can't change seller_id ─────────


async def test_update_product_cannot_change_seller_id(admin_user):
    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        direct_id = await hypershop_direct_seller_id(s)
    pid = await _seed_product(seller_id=direct_id)
    fake_other_seller = uuid4()
    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        svc = CatalogService(s)
        await svc.update_product(
            principal=_admin_principal(admin_user["user_id"]),
            product_id=pid,
            fields={
                "name": "Renamed",
                # seller_id should be silently dropped by service.
                "seller_id": fake_other_seller,
            },
        )
    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        product = (
            await s.execute(select(Product).where(Product.id == pid))
        ).scalar_one()
    assert product.seller_id == direct_id  # NOT changed
    assert product.name == "Renamed"  # name DID change


# ───────── helpers ─────────


async def _seed_product(*, seller_id):
    """Insert a minimal product directly via SQLAlchemy."""
    from decimal import Decimal

    from app.modules.catalog.models import (
        Product as ProductModel,
        ProductStatus,
        ProductVariant,
    )

    sm = get_sessionmaker()
    pid = uuid4()
    suffix = pid.hex[:8]
    async with sm() as s, s.begin():
        p = ProductModel(
            id=pid,
            slug=f"authz-{suffix}",
            name=f"Authz Test {suffix}",
            mother_sku=f"AUTHZ-{suffix.upper()}",
            status=ProductStatus.ACTIVE,
            base_currency="BDT",
            tax_class="standard",
            seller_id=seller_id,
            is_medicine=False,
            requires_prescription=False,
        )
        s.add(p)
        await s.flush()
        v = ProductVariant(
            product_id=pid,
            sku=f"AUTHZ-{suffix.upper()}-V1",
            name="default",
            price=Decimal("10.00"),
            currency="BDT",
            is_active=True,
        )
        s.add(v)
    return pid
