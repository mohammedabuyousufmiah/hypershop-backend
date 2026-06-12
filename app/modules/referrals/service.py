"""ReferralService — code generation, attribution, reward grant."""
from __future__ import annotations

import secrets
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import DomainError
from app.modules.referrals import codes
from app.modules.referrals.repository import ReferralRepository


class ReferralError(DomainError):
    pass


class SelfReferralError(ReferralError):
    code = "referral.self_not_allowed"
    status_code = 422
    public_message = "Cannot refer yourself."


class AlreadyAttributedError(ReferralError):
    code = "referral.already_attributed"
    status_code = 409
    public_message = "This account was already attributed to a referrer."


class CodeNotFoundError(ReferralError):
    code = "referral.code_not_found"
    status_code = 404
    public_message = "Referral code not found."


def _generate_code() -> str:
    return "".join(
        secrets.choice(codes.CODE_ALPHABET) for _ in range(codes.CODE_LENGTH)
    )


class ReferralService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = ReferralRepository(session)

    async def get_or_create_code(self, user_id: UUID) -> str:
        existing = await self.repo.get_code_by_user(user_id)
        if existing is not None:
            return existing.code
        # Collision guard: try a few times before bailing. With a 32^8
        # space (~1.1T) and N << 1M users, collisions are negligible.
        for _ in range(5):
            candidate = _generate_code()
            taken = await self.repo.get_code_by_text(candidate)
            if taken is None:
                row = await self.repo.insert_code(user_id, candidate)
                return row.code
        raise ReferralError("Could not allocate a unique referral code.")

    async def snapshot(self, user_id: UUID) -> dict:
        existing = await self.repo.get_code_by_user(user_id)
        code = existing.code if existing else None
        total = existing.total_referrals if existing else 0
        rewarded = existing.rewarded_referrals if existing else 0
        pending = await self.repo.count_pending(user_id) if existing else 0
        # ``active_referral_id`` — kept for FE compatibility; we don't use
        # the concept of "active campaign" yet (the code itself is the
        # identifier).
        return {
            "code": code,
            "active_referral_id": str(existing.id) if existing else None,
            "total_referrals": total,
            "rewarded_referrals": rewarded,
            "pending_referrals": pending,
        }

    async def attribute(self, *, referee_user_id: UUID, raw_code: str) -> dict:
        existing_attr = await self.repo.get_attribution_for_referee(
            referee_user_id
        )
        if existing_attr is not None:
            raise AlreadyAttributedError()

        code_row = await self.repo.get_code_by_text(raw_code.strip())
        if code_row is None:
            raise CodeNotFoundError()
        if code_row.user_id == referee_user_id:
            raise SelfReferralError()

        await self.repo.insert_attribution(
            referrer_user_id=code_row.user_id,
            referee_user_id=referee_user_id,
        )
        code_row.total_referrals = (code_row.total_referrals or 0) + 1
        await self.session.flush()
        return {
            "accepted": True,
            "referrer_user_id": code_row.user_id,
            "reason": None,
        }

    async def grant_reward_on_first_paid_order(
        self,
        *,
        referee_user_id: UUID,
        order_id: UUID,
    ) -> None:
        """Called by the order-PAID hook. Idempotent — if the attribution
        already moved to REWARDED, we no-op so a double-paid signal
        doesn't double-grant.

        Side effect: writes a loyalty ADJUST txn for the referrer (+100 pts)
        and one for the referee (+50 pts), via the existing LoyaltyService.
        """
        attr = await self.repo.get_attribution_for_referee(referee_user_id)
        if attr is None or attr.status != codes.STATUS_PENDING:
            return

        # Late import — referrals module bootstraps without loyalty so
        # the dependency is one-way.
        from app.modules.loyalty.service import LoyaltyService

        loyalty = LoyaltyService(self.session)
        await loyalty.adjust(
            user_id=attr.referrer_user_id,
            delta_points=codes.REFERRER_REWARD_POINTS,
            reason=f"referral reward · referee={attr.referee_user_id} · order={order_id}",
        )
        await loyalty.adjust(
            user_id=attr.referee_user_id,
            delta_points=codes.REFEREE_REWARD_POINTS,
            reason=f"referral welcome · referrer={attr.referrer_user_id} · order={order_id}",
        )

        attr.status = codes.STATUS_REWARDED
        attr.rewarded_at = datetime.now(timezone.utc)
        attr.rewarded_by_order_id = order_id

        # Bump the referrer's rewarded counter on the code row.
        code_row = await self.repo.get_code_by_user(attr.referrer_user_id)
        if code_row is not None:
            code_row.rewarded_referrals = (code_row.rewarded_referrals or 0) + 1
        await self.session.flush()
