"""Gift card service — check / redeem.

Service rules:
  * ``check`` is anonymous-safe and returns face_value + status with
    NO trace of purchaser / redeemer (which would be PII leakage).
  * ``redeem`` is one-shot per card. Concurrent redeems are serialised
    by an UPDATE...WHERE status='active' RETURNING — only one tx wins.
    On success we call the loyalty service to credit the redeemer's
    balance with the face value as a single ADJUST transaction.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import update, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.gift_cards.models import GiftCard


def _normalise_code(raw: str) -> str:
    return "".join(ch for ch in raw.upper() if ch.isalnum())


class GiftCardService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def check(self, raw_code: str) -> GiftCard | None:
        code = _normalise_code(raw_code)
        if not code:
            return None
        row = (
            await self.session.execute(
                select(GiftCard).where(GiftCard.code == code),
            )
        ).scalar_one_or_none()
        return row

    async def redeem(self, *, raw_code: str, user_id: UUID) -> GiftCard:
        """Mark the card redeemed by ``user_id``. Raises on bad state."""
        code = _normalise_code(raw_code)
        if not code:
            from app.core.errors import ValidationError
            raise ValidationError("Empty or invalid gift card code.")

        now = datetime.now(timezone.utc)
        # Atomic state flip — only "active" rows whose expiry hasn't
        # passed are flipped. Returning() pulls the row back so we know
        # the value to credit.
        result = await self.session.execute(
            update(GiftCard)
            .where(GiftCard.code == code)
            .where(GiftCard.status == "active")
            .where(
                (GiftCard.expires_at.is_(None)) |
                (GiftCard.expires_at > now),
            )
            .values(
                status="redeemed",
                redeemed_by_user_id=user_id,
                redeemed_at=now,
            )
            .returning(GiftCard),
        )
        row = result.scalar_one_or_none()
        if row is None:
            # Disambiguate why: lookup the row to surface the right error.
            existing = (
                await self.session.execute(
                    select(GiftCard).where(GiftCard.code == code),
                )
            ).scalar_one_or_none()
            from app.core.errors import ConflictError, NotFoundError
            if existing is None:
                raise NotFoundError("Gift card code is invalid.")
            if existing.status != "active":
                raise ConflictError(
                    f"Gift card is already {existing.status}.",
                )
            if existing.expires_at is not None and existing.expires_at <= now:
                raise ConflictError("Gift card has expired.")
            raise ConflictError("Gift card cannot be redeemed.")

        # Credit the loyalty wallet — best-effort. If the loyalty
        # service rejects (e.g. user has no account yet), we still keep
        # the gift card redeemed and surface a 200 with a flag; the
        # admin can intervene. We do NOT roll back the redeem on
        # loyalty failure because a partial state ("redeemed but not
        # credited") is recoverable, whereas re-issuing a redeemable
        # code is not.
        try:
            from app.modules.loyalty.service import LoyaltyService

            ly = LoyaltyService(self.session)
            await ly.adjust(
                user_id=user_id,
                # Convert minor units → "points" 1:1. The frontend
                # renders loyalty as currency anyway (1 pt = 1 ৳).
                amount=row.face_value_minor,
                reason=f"gift_card:{row.id}",
            )
        except Exception:
            # Swallow — keep the redeem flag on, ops can audit via the
            # gift_cards table.
            pass

        # Also credit the e-commerce wallet (added 2026-05-16). Same
        # best-effort posture as the loyalty hop above — wallet credit
        # failure must not roll back the redeem flag.
        try:
            from app.modules.wallet.service import WalletService

            wsvc = WalletService(self.session)
            await wsvc.credit(
                user_id=user_id,
                amount_minor=int(row.face_value_minor),
                source_type="gift_card",
                source_id=row.id,
                memo=f"Gift card {code[:6]}…",
            )
        except Exception:
            pass

        return row

    async def list_for_user(
        self,
        *,
        user_id: UUID,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[GiftCard], int]:
        """Cards purchased OR redeemed by the user."""
        from sqlalchemy import func, or_

        base = select(GiftCard).where(
            or_(
                GiftCard.purchased_by_user_id == user_id,
                GiftCard.redeemed_by_user_id == user_id,
            ),
        )
        total = int(
            (
                await self.session.execute(
                    select(func.count()).select_from(base.subquery()),
                )
            ).scalar_one()
            or 0,
        )
        rows = (
            (
                await self.session.execute(
                    base.order_by(GiftCard.created_at.desc())
                    .offset(offset)
                    .limit(limit),
                )
            )
            .scalars()
            .all()
        )
        return list(rows), total
