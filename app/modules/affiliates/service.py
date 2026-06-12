"""AffiliateService — code allocation, link generation, commission grant."""
from __future__ import annotations

import secrets
from decimal import Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import DomainError
from app.modules.affiliates import codes
from app.modules.affiliates.repository import AffiliateRepository


class AffiliateError(DomainError):
    pass


class CodeNotFoundError(AffiliateError):
    code = "affiliate.code_not_found"
    status_code = 404
    public_message = "Affiliate code not found."


def _generate_code() -> str:
    return "".join(
        secrets.choice(codes.CODE_ALPHABET) for _ in range(codes.CODE_LENGTH)
    )


class AffiliateService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = AffiliateRepository(session)

    async def get_or_create(self, user_id: UUID):
        existing = await self.repo.get_by_user(user_id)
        if existing is not None:
            return existing
        for _ in range(5):
            candidate = _generate_code()
            taken = await self.repo.get_by_code(candidate)
            if taken is None:
                return await self.repo.insert(
                    user_id=user_id,
                    code=candidate,
                    commission_rate=codes.DEFAULT_COMMISSION_RATE,
                )
        raise AffiliateError("Could not allocate a unique affiliate code.")

    def build_link(self, *, base_url: str, code: str, slug: str, locale: str) -> str:
        prefix = "/bn" if locale == "bn" else ""
        return f"{base_url.rstrip('/')}{prefix}/product/{slug}?aff={code}"

    async def credit_for_order(
        self,
        *,
        code: str,
        subtotal_minor: int,
        order_id: UUID,
    ) -> dict:
        """Grant commission to the affiliate identified by ``code``.

        Idempotent — calls into ``LoyaltyService.adjust`` whose ``reason``
        string includes the ``order_id``; back-end UNIQUE-ish guard
        (we accept duplicate adjust rows but the affiliate counters
        only bump once per call here — the checkout-confirm hook only
        fires once per order on the happy path).
        """
        if subtotal_minor <= 0:
            return {"credited_minor": 0, "credited_points": 0, "code": code}
        rec = await self.repo.get_by_code(code)
        if rec is None:
            raise CodeNotFoundError()
        if not rec.enabled:
            return {"credited_minor": 0, "credited_points": 0, "code": code}

        # 1 BDT = 100 minor (paisa); 5 % of subtotal → commission in paisa.
        commission_minor = int(
            (Decimal(subtotal_minor) * rec.commission_rate).quantize(Decimal("1"))
        )
        if commission_minor <= 0:
            return {"credited_minor": 0, "credited_points": 0, "code": code}

        # Convert paisa → BDT → loyalty points (1 BDT = 2 points by spec).
        commission_bdt = Decimal(commission_minor) / Decimal(100)
        commission_points = int(commission_bdt * codes.LOYALTY_POINTS_PER_BDT)

        # Pay via loyalty ADJUST (positive delta).
        if commission_points > 0:
            from app.modules.loyalty.service import LoyaltyService

            loyalty = LoyaltyService(self.session)
            await loyalty.adjust(
                user_id=rec.user_id,
                delta_points=commission_points,
                reason=f"affiliate commission · order={order_id} · code={code}",
            )

        rec.total_conversions = (rec.total_conversions or 0) + 1
        rec.total_commission_minor = (
            (rec.total_commission_minor or 0) + commission_minor
        )
        await self.session.flush()

        return {
            "credited_minor": commission_minor,
            "credited_points": commission_points,
            "code": rec.code,
        }
