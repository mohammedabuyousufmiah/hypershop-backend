"""Service layer for the sellers module — phase 1.

Lifecycle:
  registered  →  kyc_submitted     (customer-side action — submit KYC)
                 ↓
              approved             (admin action)
              rejected             (admin action — terminal)
                 ↓
              suspended            (admin action — reversible)
                 ↑
              approved             (admin reinstate)

Phase 1 has no public registration — admins create the seller row,
the operator nominates a user to the seller, and that user submits
KYC. Self-serve registration lands in phase 4.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit.service import record_audit
from app.core.errors import ValidationError
from app.core.security.principal import Principal
from app.modules.sellers.codes import (
    ACTION_SELLER_APPROVED,
    ACTION_SELLER_CREATED,
    ACTION_SELLER_KYC_SUBMITTED,
    ACTION_SELLER_REINSTATED,
    ACTION_SELLER_REJECTED,
    ACTION_SELLER_SUSPENDED,
    ACTION_SELLER_USER_LINKED,
    ACTION_SELLER_USER_UNLINKED,
    ALL_PAYOUT_CADENCES,
    ALL_PAYOUT_METHODS,
    ALL_SELLER_ROLES,
    STATUS_APPROVED,
    STATUS_KYC_SUBMITTED,
    STATUS_REGISTERED,
    STATUS_REJECTED,
    STATUS_SUSPENDED,
)
from app.modules.sellers.errors import (
    SellerBadStateError,
    SellerKycIncompleteError,
    SellerNotFoundError,
    SellerUserAlreadyLinkedError,
    SellerUserNotLinkedError,
)
from app.modules.sellers.models import Seller, SellerUser
from app.modules.sellers.repository import SellerRepository


class SellerService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = SellerRepository(session)

    # ---- create ----

    async def create(
        self,
        *,
        business_name: str,
        slug: str,
        contact_email: str | None,
        contact_phone: str | None,
        principal: Principal,
    ) -> Seller:
        # Slug uniqueness — surface 409 here rather than relying on
        # the DB constraint blowing up the transaction.
        if await self.repo.get_by_slug(slug) is not None:
            raise ValidationError(
                f"Seller slug '{slug}' is already in use.",
                details={"field": "slug"},
            )
        seller = await self.repo.create(
            business_name=business_name,
            slug=slug,
            contact_email=contact_email,
            contact_phone=contact_phone,
            status=STATUS_REGISTERED,
        )
        await record_audit(
            actor=principal,
            action=ACTION_SELLER_CREATED,
            resource_type="seller",
            resource_id=seller.id,
            metadata={"slug": slug, "business_name": business_name},
        )
        return seller

    # ---- KYC ----

    async def submit_kyc(
        self,
        *,
        seller_id: UUID,
        tin: str,
        nid: str,
        bank_account_name: str,
        bank_account_number: str,
        bank_name: str,
        bank_branch: str | None,
        trade_license_no: str | None,
        principal: Principal,
    ) -> Seller:
        s = await self._require(seller_id)
        # KYC can be re-submitted while in registered or rejected
        # (rejection feedback often points at a single missing field
        # — the seller fixes + re-submits).
        if s.status not in (STATUS_REGISTERED, STATUS_REJECTED):
            raise SellerBadStateError(
                f"KYC can only be submitted from 'registered' or 'rejected' "
                f"(current: {s.status}).",
                details={"current_status": s.status},
            )
        # All four bank fields together — half-filled bank info is
        # useless and a phase-5 payout will fail.
        if not (tin and nid and bank_account_name and bank_account_number and bank_name):
            raise SellerKycIncompleteError()
        await self.repo.update_fields(
            seller_id,
            tin=tin,
            nid=nid,
            bank_account_name=bank_account_name,
            bank_account_number=bank_account_number,
            bank_name=bank_name,
            bank_branch=bank_branch,
            trade_license_no=trade_license_no,
            status=STATUS_KYC_SUBMITTED,
        )
        await record_audit(
            actor=principal,
            action=ACTION_SELLER_KYC_SUBMITTED,
            resource_type="seller",
            resource_id=seller_id,
        )
        refreshed = await self.repo.get(seller_id)
        assert refreshed is not None
        return refreshed

    # ---- moderation ----

    async def approve(
        self, *, seller_id: UUID, principal: Principal,
    ) -> Seller:
        s = await self._require(seller_id)
        if s.status != STATUS_KYC_SUBMITTED:
            raise SellerBadStateError(
                f"Seller must be 'kyc_submitted' to approve "
                f"(current: {s.status}).",
                details={"current_status": s.status},
            )
        await self.repo.update_fields(
            seller_id,
            status=STATUS_APPROVED,
            moderated_by=principal.user_id,
            moderated_at=datetime.now(timezone.utc),
            rejection_reason=None,
        )
        await record_audit(
            actor=principal,
            action=ACTION_SELLER_APPROVED,
            resource_type="seller",
            resource_id=seller_id,
        )
        refreshed = await self.repo.get(seller_id)
        assert refreshed is not None
        return refreshed

    async def reject(
        self, *, seller_id: UUID, reason: str, principal: Principal,
    ) -> Seller:
        s = await self._require(seller_id)
        if s.status != STATUS_KYC_SUBMITTED:
            raise SellerBadStateError(
                f"Seller must be 'kyc_submitted' to reject "
                f"(current: {s.status}).",
                details={"current_status": s.status},
            )
        await self.repo.update_fields(
            seller_id,
            status=STATUS_REJECTED,
            moderated_by=principal.user_id,
            moderated_at=datetime.now(timezone.utc),
            rejection_reason=reason,
        )
        await record_audit(
            actor=principal,
            action=ACTION_SELLER_REJECTED,
            resource_type="seller",
            resource_id=seller_id,
            metadata={"reason": reason},
        )
        refreshed = await self.repo.get(seller_id)
        assert refreshed is not None
        return refreshed

    async def suspend(
        self, *, seller_id: UUID, reason: str, principal: Principal,
    ) -> Seller:
        s = await self._require(seller_id)
        if s.status != STATUS_APPROVED:
            raise SellerBadStateError(
                f"Only approved sellers can be suspended "
                f"(current: {s.status}).",
                details={"current_status": s.status},
            )
        await self.repo.update_fields(
            seller_id,
            status=STATUS_SUSPENDED,
            moderated_by=principal.user_id,
            moderated_at=datetime.now(timezone.utc),
            rejection_reason=reason,
        )
        await record_audit(
            actor=principal,
            action=ACTION_SELLER_SUSPENDED,
            resource_type="seller",
            resource_id=seller_id,
            metadata={"reason": reason},
        )
        refreshed = await self.repo.get(seller_id)
        assert refreshed is not None
        return refreshed

    async def reinstate(
        self, *, seller_id: UUID, principal: Principal,
    ) -> Seller:
        s = await self._require(seller_id)
        if s.status != STATUS_SUSPENDED:
            raise SellerBadStateError(
                f"Only suspended sellers can be reinstated "
                f"(current: {s.status}).",
                details={"current_status": s.status},
            )
        await self.repo.update_fields(
            seller_id,
            status=STATUS_APPROVED,
            moderated_by=principal.user_id,
            moderated_at=datetime.now(timezone.utc),
            rejection_reason=None,
        )
        await record_audit(
            actor=principal,
            action=ACTION_SELLER_REINSTATED,
            resource_type="seller",
            resource_id=seller_id,
        )
        refreshed = await self.repo.get(seller_id)
        assert refreshed is not None
        return refreshed

    # ---- commission + payout config ----

    async def update_commission(
        self,
        *,
        seller_id: UUID,
        commission_percent: Decimal,
        principal: Principal,
    ) -> Seller:
        s = await self._require(seller_id)
        await self.repo.update_commission(seller_id, commission_percent)
        await record_audit(
            actor=principal,
            action="sellers.seller.commission_updated",
            resource_type="seller",
            resource_id=seller_id,
            metadata={"new_percent": str(commission_percent)},
        )
        refreshed = await self.repo.get(seller_id)
        assert refreshed is not None
        return refreshed

    async def update_payout_config(
        self,
        *,
        seller_id: UUID,
        payout_cadence: str | None,
        payout_method: str | None,
        payout_account_id: str | None,
        principal: Principal,
    ) -> Seller:
        await self._require(seller_id)
        values: dict[str, object] = {}
        if payout_cadence is not None:
            if payout_cadence not in ALL_PAYOUT_CADENCES:
                raise ValidationError(
                    f"Unknown payout_cadence '{payout_cadence}'.",
                    details={"allowed": list(ALL_PAYOUT_CADENCES)},
                )
            values["payout_cadence"] = payout_cadence
        if payout_method is not None:
            if payout_method not in ALL_PAYOUT_METHODS:
                raise ValidationError(
                    f"Unknown payout_method '{payout_method}'.",
                    details={"allowed": list(ALL_PAYOUT_METHODS)},
                )
            values["payout_method"] = payout_method
        if payout_account_id is not None:
            values["payout_account_id"] = payout_account_id
        if values:
            await self.repo.update_fields(seller_id, **values)
            await record_audit(
                actor=principal,
                action="sellers.seller.payout_config_updated",
                resource_type="seller",
                resource_id=seller_id,
                metadata={k: str(v) for k, v in values.items()},
            )
        refreshed = await self.repo.get(seller_id)
        assert refreshed is not None
        return refreshed

    # ---- seller_users ----

    async def link_user(
        self,
        *,
        seller_id: UUID,
        user_id: UUID,
        role: str,
        principal: Principal,
    ) -> SellerUser:
        if role not in ALL_SELLER_ROLES:
            raise ValidationError(
                f"Unknown seller role '{role}'.",
                details={"allowed": list(ALL_SELLER_ROLES)},
            )
        await self._require(seller_id)
        # Phase-1 rule: one user can only belong to one seller.
        existing = await self.repo.get_user_link(user_id=user_id)
        if existing is not None:
            raise SellerUserAlreadyLinkedError(
                details={"existing_seller_id": str(existing.seller_id)},
            )
        link = await self.repo.link_user(
            seller_id=seller_id, user_id=user_id, role=role,
        )
        await record_audit(
            actor=principal,
            action=ACTION_SELLER_USER_LINKED,
            resource_type="seller",
            resource_id=seller_id,
            metadata={"user_id": str(user_id), "role": role},
        )
        return link

    async def unlink_user(
        self, *, seller_id: UUID, user_id: UUID, principal: Principal,
    ) -> None:
        existing = await self.repo.get_user_link(user_id=user_id)
        if existing is None or existing.seller_id != seller_id:
            raise SellerUserNotLinkedError()
        await self.repo.unlink_user(seller_id=seller_id, user_id=user_id)
        await record_audit(
            actor=principal,
            action=ACTION_SELLER_USER_UNLINKED,
            resource_type="seller",
            resource_id=seller_id,
            metadata={"user_id": str(user_id)},
        )

    # ---- read paths ----

    async def list(
        self,
        *,
        status: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[Sequence[Seller], int]:
        return await self.repo.list(status=status, offset=offset, limit=limit)

    # ---- internals ----

    async def _require(self, seller_id: UUID) -> Seller:
        s = await self.repo.get(seller_id)
        if s is None:
            raise SellerNotFoundError()
        return s
