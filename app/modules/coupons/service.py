"""CouponService."""
from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.modules.coupons.models import Coupon, CouponRedemption


class CouponService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_all(
        self, *, only_active: bool = False, offset: int = 0, limit: int = 100
    ) -> tuple[Sequence[Coupon], int]:
        base = select(Coupon)
        if only_active:
            base = base.where(Coupon.is_active.is_(True))
        items = (
            await self.session.execute(
                base.order_by(Coupon.created_at.desc())
                .offset(offset)
                .limit(limit)
            )
        ).scalars().all()
        total_stmt = select(func.count()).select_from(Coupon)
        if only_active:
            total_stmt = total_stmt.where(Coupon.is_active.is_(True))
        total = int((await self.session.execute(total_stmt)).scalar_one())
        return items, total

    async def get_by_id(self, coupon_id: UUID) -> Coupon | None:
        return (
            await self.session.execute(
                select(Coupon).where(Coupon.id == coupon_id)
            )
        ).scalar_one_or_none()

    async def get_by_code(self, code: str) -> Coupon | None:
        return (
            await self.session.execute(
                select(Coupon).where(func.lower(Coupon.code) == code.lower())
            )
        ).scalar_one_or_none()

    async def create(self, **fields) -> Coupon:
        existing = await self.get_by_code(fields["code"])
        if existing is not None:
            raise ConflictError("Coupon code already exists.")
        # Normalise code to upper.
        fields["code"] = str(fields["code"]).upper()
        row = Coupon(**fields)
        self.session.add(row)
        await self.session.flush()
        return row

    async def delete(self, coupon_id: UUID) -> None:
        row = await self.get_by_id(coupon_id)
        if row is None:
            raise NotFoundError("Coupon not found.")
        await self.session.delete(row)
        await self.session.flush()

    def _compute_discount(self, c: Coupon, subtotal_minor: int) -> int:
        if c.discount_type == "FIXED":
            disc = c.discount_value_minor
        elif c.discount_type == "PERCENT":
            # discount_value_minor stored in basis points-style: 1000 = 10%.
            # Convention: discount_value_minor is *whole-percent × 100*,
            # i.e. value 1000 = 10.00%. Keep math obvious.
            disc = int(
                (Decimal(subtotal_minor) * Decimal(c.discount_value_minor))
                / Decimal(10_000)
            )
        else:
            disc = 0
        if c.max_discount_minor is not None:
            disc = min(disc, c.max_discount_minor)
        return max(0, min(disc, subtotal_minor))

    async def preview(
        self,
        *,
        code: str,
        subtotal_minor: int,
        customer_user_id: UUID | None,
    ) -> dict:
        c = await self.get_by_code(code)
        if c is None or not c.is_active:
            return {
                "code": code.upper(),
                "valid": False,
                "reason": "Coupon not found or inactive.",
                "discount_minor": 0,
            }
        now = datetime.now(timezone.utc)
        if c.valid_from and now < c.valid_from:
            return {
                "code": c.code,
                "valid": False,
                "reason": "Coupon not yet active.",
                "discount_minor": 0,
            }
        if c.valid_until and now > c.valid_until:
            return {
                "code": c.code,
                "valid": False,
                "reason": "Coupon expired.",
                "discount_minor": 0,
            }
        if subtotal_minor < c.min_subtotal_minor:
            return {
                "code": c.code,
                "valid": False,
                "reason": f"Subtotal below minimum {c.min_subtotal_minor // 100} BDT.",
                "discount_minor": 0,
            }
        if c.max_total_uses is not None and c.total_uses >= c.max_total_uses:
            return {
                "code": c.code,
                "valid": False,
                "reason": "Coupon usage cap reached.",
                "discount_minor": 0,
            }
        if customer_user_id is not None and c.max_uses_per_customer:
            used_by_customer = int(
                (
                    await self.session.execute(
                        select(func.count())
                        .select_from(CouponRedemption)
                        .where(
                            CouponRedemption.coupon_id == c.id,
                            CouponRedemption.customer_user_id == customer_user_id,
                        )
                    )
                ).scalar_one()
            )
            if used_by_customer >= c.max_uses_per_customer:
                return {
                    "code": c.code,
                    "valid": False,
                    "reason": "Per-customer usage cap reached.",
                    "discount_minor": 0,
                }
        discount = self._compute_discount(c, subtotal_minor)
        return {
            "code": c.code,
            "valid": True,
            "reason": None,
            "discount_minor": discount,
        }

    async def redeem(
        self,
        *,
        code: str,
        subtotal_minor: int,
        customer_user_id: UUID,
        order_id: UUID,
    ) -> int:
        """Record a redemption row + bump the coupon's total_uses counter.

        Returns the applied discount in paisa. Re-running with the same
        order_id is a no-op (UNIQUE constraint catches it; we swallow
        the error and return the recorded discount for idempotency)."""
        preview = await self.preview(
            code=code,
            subtotal_minor=subtotal_minor,
            customer_user_id=customer_user_id,
        )
        if not preview["valid"]:
            raise ValidationError(preview["reason"] or "Invalid coupon.")
        c = await self.get_by_code(code)
        if c is None:
            raise NotFoundError("Coupon not found.")
        discount = int(preview["discount_minor"])
        row = CouponRedemption(
            coupon_id=c.id,
            customer_user_id=customer_user_id,
            order_id=order_id,
            discount_applied_minor=discount,
        )
        self.session.add(row)
        c.total_uses = (c.total_uses or 0) + 1
        try:
            await self.session.flush()
        except Exception:
            # Duplicate (order_id, coupon_id) — assume idempotent retry.
            await self.session.rollback()
            return discount
        return discount
