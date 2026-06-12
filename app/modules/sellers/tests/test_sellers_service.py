"""Service-level lifecycle tests for the sellers module — phase 1.

Covers:
  - create → kyc_submit → approve happy path
  - kyc_submit blocked from non-(registered/rejected) state
  - reject → resubmit → approve
  - approve → suspend → reinstate round trip
  - one user can't be linked to two sellers
  - commission update + payout config update
  - Hypershop Direct seed row exists post-migration
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from app.core.db.session import get_sessionmaker
from app.core.security.principal import Principal
from app.modules.sellers.codes import (
    DEFAULT_PAYOUT_CADENCE,
    PAYOUT_WEEKLY,
    SELLER_ROLE_OWNER,
    STATUS_APPROVED,
    STATUS_KYC_SUBMITTED,
    STATUS_REJECTED,
    STATUS_REGISTERED,
    STATUS_SUSPENDED,
)
from app.modules.sellers.errors import (
    SellerBadStateError,
    SellerUserAlreadyLinkedError,
)
from app.modules.sellers.repository import SellerRepository
from app.modules.sellers.service import SellerService

pytestmark = pytest.mark.integration


def _admin(user_id) -> Principal:
    return Principal(
        user_id=user_id,
        session_id=uuid4(),
        roles=frozenset({"manager"}),
        permissions=frozenset({"sellers.admin"}),
    )


# ───────── 1. Happy path lifecycle ─────────


async def test_create_then_kyc_submit_then_approve(admin_user):
    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        svc = SellerService(s)
        seller = await svc.create(
            business_name="Acme Pharma",
            slug="acme-pharma",
            contact_email="ops@acme.example",
            contact_phone="+8801711000000",
            principal=_admin(admin_user["user_id"]),
        )
    assert seller.status == STATUS_REGISTERED

    async with sm() as s, s.begin():
        svc = SellerService(s)
        seller = await svc.submit_kyc(
            seller_id=seller.id,
            tin="1234567890",
            nid="9876543210",
            bank_account_name="Acme Pharma Ltd",
            bank_account_number="1112223334445",
            bank_name="DBBL",
            bank_branch="Gulshan",
            trade_license_no=None,
            principal=_admin(admin_user["user_id"]),
        )
    assert seller.status == STATUS_KYC_SUBMITTED

    async with sm() as s, s.begin():
        svc = SellerService(s)
        seller = await svc.approve(
            seller_id=seller.id, principal=_admin(admin_user["user_id"]),
        )
    assert seller.status == STATUS_APPROVED


# ───────── 2. KYC blocked from wrong state ─────────


async def test_kyc_blocked_when_already_approved(admin_user):
    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        svc = SellerService(s)
        seller = await svc.create(
            business_name="Beta Pharma",
            slug="beta-pharma",
            contact_email=None,
            contact_phone=None,
            principal=_admin(admin_user["user_id"]),
        )
        await svc.submit_kyc(
            seller_id=seller.id,
            tin="1", nid="2",
            bank_account_name="X", bank_account_number="3",
            bank_name="Y", bank_branch=None, trade_license_no=None,
            principal=_admin(admin_user["user_id"]),
        )
        await svc.approve(seller_id=seller.id, principal=_admin(admin_user["user_id"]))

    async with sm() as s, s.begin():
        svc = SellerService(s)
        with pytest.raises(SellerBadStateError):
            await svc.submit_kyc(
                seller_id=seller.id,
                tin="1", nid="2",
                bank_account_name="X", bank_account_number="3",
                bank_name="Y", bank_branch=None, trade_license_no=None,
                principal=_admin(admin_user["user_id"]),
            )


# ───────── 3. Reject → resubmit → approve ─────────


async def test_reject_allows_resubmit(admin_user):
    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        svc = SellerService(s)
        seller = await svc.create(
            business_name="Gamma Pharma",
            slug="gamma-pharma",
            contact_email=None,
            contact_phone=None,
            principal=_admin(admin_user["user_id"]),
        )
        await svc.submit_kyc(
            seller_id=seller.id,
            tin="1", nid="2",
            bank_account_name="X", bank_account_number="3",
            bank_name="Y", bank_branch=None, trade_license_no=None,
            principal=_admin(admin_user["user_id"]),
        )
        rejected = await svc.reject(
            seller_id=seller.id,
            reason="bank account name mismatch",
            principal=_admin(admin_user["user_id"]),
        )
    assert rejected.status == STATUS_REJECTED

    async with sm() as s, s.begin():
        svc = SellerService(s)
        # rejected → resubmit allowed
        resubmitted = await svc.submit_kyc(
            seller_id=seller.id,
            tin="1", nid="2",
            bank_account_name="X corrected", bank_account_number="3",
            bank_name="Y", bank_branch=None, trade_license_no=None,
            principal=_admin(admin_user["user_id"]),
        )
    assert resubmitted.status == STATUS_KYC_SUBMITTED


# ───────── 4. Approve → suspend → reinstate ─────────


async def test_suspend_then_reinstate_round_trip(admin_user):
    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        svc = SellerService(s)
        seller = await svc.create(
            business_name="Delta Pharma",
            slug="delta-pharma",
            contact_email=None,
            contact_phone=None,
            principal=_admin(admin_user["user_id"]),
        )
        await svc.submit_kyc(
            seller_id=seller.id,
            tin="1", nid="2",
            bank_account_name="X", bank_account_number="3",
            bank_name="Y", bank_branch=None, trade_license_no=None,
            principal=_admin(admin_user["user_id"]),
        )
        await svc.approve(seller_id=seller.id, principal=_admin(admin_user["user_id"]))

    async with sm() as s, s.begin():
        svc = SellerService(s)
        suspended = await svc.suspend(
            seller_id=seller.id,
            reason="multiple customer complaints",
            principal=_admin(admin_user["user_id"]),
        )
    assert suspended.status == STATUS_SUSPENDED

    async with sm() as s, s.begin():
        svc = SellerService(s)
        reinstated = await svc.reinstate(
            seller_id=seller.id, principal=_admin(admin_user["user_id"]),
        )
    assert reinstated.status == STATUS_APPROVED


# ───────── 5. seller_user link exclusivity ─────────


async def test_user_cannot_link_to_two_sellers(admin_user, registered_user):
    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        svc = SellerService(s)
        a = await svc.create(
            business_name="A", slug="a-co",
            contact_email=None, contact_phone=None,
            principal=_admin(admin_user["user_id"]),
        )
        b = await svc.create(
            business_name="B", slug="b-co",
            contact_email=None, contact_phone=None,
            principal=_admin(admin_user["user_id"]),
        )

    async with sm() as s, s.begin():
        svc = SellerService(s)
        await svc.link_user(
            seller_id=a.id,
            user_id=registered_user["user_id"],
            role=SELLER_ROLE_OWNER,
            principal=_admin(admin_user["user_id"]),
        )

    async with sm() as s, s.begin():
        svc = SellerService(s)
        with pytest.raises(SellerUserAlreadyLinkedError):
            await svc.link_user(
                seller_id=b.id,
                user_id=registered_user["user_id"],
                role=SELLER_ROLE_OWNER,
                principal=_admin(admin_user["user_id"]),
            )


# ───────── 6. Commission + payout-config updates ─────────


async def test_commission_update_persists(admin_user):
    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        svc = SellerService(s)
        seller = await svc.create(
            business_name="Echo Pharma", slug="echo-pharma",
            contact_email=None, contact_phone=None,
            principal=_admin(admin_user["user_id"]),
        )
    async with sm() as s, s.begin():
        svc = SellerService(s)
        updated = await svc.update_commission(
            seller_id=seller.id,
            commission_percent=Decimal("7.50"),
            principal=_admin(admin_user["user_id"]),
        )
    assert updated.commission_percent == Decimal("7.50")


async def test_payout_config_update(admin_user):
    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        svc = SellerService(s)
        seller = await svc.create(
            business_name="Foxtrot Pharma", slug="foxtrot-pharma",
            contact_email=None, contact_phone=None,
            principal=_admin(admin_user["user_id"]),
        )
    async with sm() as s, s.begin():
        svc = SellerService(s)
        updated = await svc.update_payout_config(
            seller_id=seller.id,
            payout_cadence=PAYOUT_WEEKLY,
            payout_method="nagad",
            payout_account_id="01711000000",
            principal=_admin(admin_user["user_id"]),
        )
    assert updated.payout_cadence == PAYOUT_WEEKLY
    assert updated.payout_method == "nagad"
    assert updated.payout_account_id == "01711000000"


# ───────── 7. Hypershop Direct seed row ─────────


async def test_hypershop_direct_seed_exists():
    """Migration 0033 seeds a 'hypershop-direct' approved seller for
    phase 2 product backfill. It must exist post-migration."""
    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        repo = SellerRepository(s)
        direct = await repo.get_by_slug("hypershop-direct")
    assert direct is not None
    assert direct.status == STATUS_APPROVED
    assert direct.commission_percent == Decimal("0.00")
