"""LoyaltyService — earn / redeem / read.

Invariant: every mutation writes a ledger row AND updates the account
row in the same DB transaction. The caller's UoW is responsible for
the outer commit. Service methods are idempotent against a given
``source_order_id`` — calling ``earn(..., source_order_id=X)`` twice
yields one EARN row.
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import DomainError
from app.modules.loyalty import codes
from app.modules.loyalty.models import LoyaltyAccount
from app.modules.loyalty.repository import LoyaltyRepository


class LoyaltyError(DomainError):
    pass


class InsufficientBalanceError(LoyaltyError):
    code = "loyalty.insufficient_balance"
    status_code = 422
    public_message = "Insufficient loyalty balance."


class InvalidRedemptionError(LoyaltyError):
    code = "loyalty.invalid_redemption"
    status_code = 422
    public_message = "Invalid loyalty redemption."


class LoyaltyService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = LoyaltyRepository(session)

    # ---- reads ----

    async def get_or_create_account(self, user_id: UUID) -> LoyaltyAccount:
        acc = await self.repo.get_account(user_id)
        if acc is None:
            acc = await self.repo.create_account(user_id)
        return acc

    def _next_tier_progress(
        self, rolling: int
    ) -> tuple[str | None, int | None]:
        """Returns (next_tier_label, points_remaining_to_reach_it) using
        the rolling 12-month window."""
        if rolling < codes.TIER_BRONZE_MIN:
            return "BRONZE", codes.TIER_BRONZE_MIN - rolling
        if rolling < codes.TIER_SILVER_MIN:
            return "SILVER", codes.TIER_SILVER_MIN - rolling
        if rolling < codes.TIER_GOLD_MIN:
            return "GOLD", codes.TIER_GOLD_MIN - rolling
        return None, None  # GOLD is the cap

    async def _rolling_earned(self, user_id: UUID) -> int:
        """Sum EARN points in the trailing 12 months (locked-spec window)."""
        since = self.repo.rolling_window_start(
            self.repo.utcnow(), days=codes.EARN_EXPIRY_DAYS
        )
        return await self.repo.sum_earned_in_window(user_id, since=since)

    async def snapshot(self, user_id: UUID) -> dict:
        acc = await self.get_or_create_account(user_id)
        rolling = await self._rolling_earned(user_id)
        # Recompute tier on read — drift-proof against the cached column
        # falling behind when points age out without any new transaction.
        current_tier = codes.compute_tier(rolling)
        next_tier, points_to_next = self._next_tier_progress(rolling)
        return {
            "balance_points": acc.balance_points,
            "rolling_earned_points": rolling,
            "lifetime_earned_points": acc.lifetime_earned_points,
            "tier": current_tier,
            "next_tier": next_tier,
            "points_to_next_tier": points_to_next,
        }

    async def availability(
        self, user_id: UUID, *, subtotal_minor: int
    ) -> dict:
        acc = await self.get_or_create_account(user_id)
        cap = codes.compute_max_redeem_points(subtotal_minor)
        max_points = min(acc.balance_points, cap)
        return {
            "balance_points": acc.balance_points,
            "subtotal_minor": subtotal_minor,
            "max_points": max(max_points, 0),
            "max_discount_minor": codes.points_to_minor(max(max_points, 0)),
        }

    # ---- mutations ----

    async def earn(
        self,
        *,
        user_id: UUID,
        subtotal_minor: int,
        source_order_id: UUID,
    ) -> LoyaltyAccount:
        """Append an EARN row sized by ``subtotal_minor``. No-op when
        nothing earnable (subtotal < 100 BDT) or when we've already
        recorded an EARN for this order (idempotent on retries)."""
        # Idempotency guard.
        existing = await self.repo.txn_for_order(
            user_id=user_id,
            source_order_id=source_order_id,
            txn_type="EARN",
        )
        if existing is not None:
            return await self.get_or_create_account(user_id)

        points = codes.compute_earn_points(subtotal_minor)
        acc = await self.get_or_create_account(user_id)
        if points <= 0:
            return acc

        now = self.repo.utcnow()
        expires = self.repo.earn_expiry(now, days=codes.EARN_EXPIRY_DAYS)
        await self.repo.append_txn(
            user_id=user_id,
            txn_type="EARN",
            points=points,
            source_order_id=source_order_id,
            expires_at=expires,
            reason=f"earn for order {source_order_id}",
        )
        acc.balance_points += points
        acc.lifetime_earned_points += points
        # Tier is rolling-12-month — recompute from the EARN ledger in the
        # window. The just-appended row is committed in the same flush,
        # so it's already counted.
        await self.session.flush()
        rolling = await self._rolling_earned(user_id)
        new_tier = codes.compute_tier(rolling)
        if new_tier != acc.tier:
            acc.tier = new_tier
            acc.tier_recomputed_at = now
            await self.session.flush()
        return acc

    async def redeem(
        self,
        *,
        user_id: UUID,
        points: int,
        subtotal_minor: int,
        source_order_id: UUID,
    ) -> LoyaltyAccount:
        """Append a REDEEM row. Validates against:
          * ``points > 0``
          * ``points <= balance``
          * ``points <= 3% subtotal`` cap
          * not-already-redeemed for this order
        """
        if points <= 0:
            raise InvalidRedemptionError("points must be positive")

        existing = await self.repo.txn_for_order(
            user_id=user_id,
            source_order_id=source_order_id,
            txn_type="REDEEM",
        )
        if existing is not None:
            # Already redeemed for this order — return account unchanged.
            return await self.get_or_create_account(user_id)

        cap = codes.compute_max_redeem_points(subtotal_minor)
        if points > cap:
            raise InvalidRedemptionError(
                f"redemption exceeds 3% subtotal cap "
                f"(max {cap} pts for this order)",
            )

        acc = await self.get_or_create_account(user_id)
        if points > acc.balance_points:
            raise InsufficientBalanceError(
                f"balance is {acc.balance_points} pts; cannot redeem {points}",
            )

        await self.repo.append_txn(
            user_id=user_id,
            txn_type="REDEEM",
            points=-points,
            source_order_id=source_order_id,
            reason=f"redeem on order {source_order_id}",
        )
        acc.balance_points -= points
        await self.session.flush()
        return acc

    async def adjust(
        self,
        *,
        user_id: UUID,
        delta_points: int,
        reason: str,
        actor_user_id: UUID | None = None,
    ) -> LoyaltyAccount:
        """Admin-driven manual adjustment. ``delta_points`` may be +/-."""
        if delta_points == 0:
            raise InvalidRedemptionError("adjustment cannot be zero")
        acc = await self.get_or_create_account(user_id)
        if delta_points < 0 and abs(delta_points) > acc.balance_points:
            raise InsufficientBalanceError(
                f"cannot debit {abs(delta_points)} from balance {acc.balance_points}",
            )
        await self.repo.append_txn(
            user_id=user_id,
            txn_type="ADJUST",
            points=delta_points,
            reason=reason,
            created_by=actor_user_id,
        )
        acc.balance_points += delta_points
        # ADJUST does NOT affect lifetime (matches spec — tier ratchets
        # only on real earns).
        await self.session.flush()
        return acc
