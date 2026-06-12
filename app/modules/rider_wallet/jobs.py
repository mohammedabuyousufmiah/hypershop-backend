"""ARQ cron jobs for the rider wallet module.

  - ``apply_previous_day_settlement_lock_job`` (nightly at 23:30 UTC =
    05:30 BDT next morning) — sweep every wallet with payable > 0 and
    no active carry-forward; lock them for the next day.

  - ``expire_carry_forwards_job`` (every 30 min) — for any wallet with
    ``carry_forward_approved=true`` AND ``carry_forward_expires_at <=
    now``, clear the approval. If payable still > 0, also lock.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import and_, select, update

from app.core.db.uow import UnitOfWork
from app.core.logging import get_logger
from app.core.security.principal import SystemPrincipal
from app.core.time import utc_now
from app.modules.rider_wallet import codes
from app.modules.rider_wallet.models import RiderWallet
from app.modules.rider_wallet.repository import RiderWalletRepository
from app.modules.rider_wallet.service import (
    AssignmentEligibilityService,
    RiderWalletService,
)

_log = get_logger("hypershop.rider_wallet.jobs")


async def apply_previous_day_settlement_lock_job(
    _ctx: dict[str, Any],
) -> int:
    """Lock every rider with unpaid balance + no active carry-forward.

    Returns the number of riders locked this tick.
    """
    locked_count = 0
    principal = SystemPrincipal()
    async with UnitOfWork().transactional() as session:
        wallets = RiderWalletRepository(session)
        eligibility = AssignmentEligibilityService(session)
        rider_ids = await wallets.find_riders_with_unpaid()
        for rid in rider_ids:
            applied = await eligibility.apply_lock_if_unpaid(
                rider_id=rid, principal=principal,
            )
            if applied:
                locked_count += 1
    if locked_count:
        _log.info(
            "previous_day_settlement_lock_applied",
            count=locked_count,
        )
    return locked_count


async def expire_carry_forwards_job(_ctx: dict[str, Any]) -> int:
    """Clear expired carry-forward approvals.

    For any wallet whose carry-forward_expires_at has passed: clear the
    approval flags. If wallet_payable_to_company > 0, also re-lock the
    rider (they were only allowed to work because of the approval).
    """
    now = utc_now()
    cleared = 0
    async with UnitOfWork().transactional() as session:
        # Pre-collect candidates (need their ids + payables to know
        # whether to re-lock).
        rows = (
            (
                await session.execute(
                    select(
                        RiderWallet.id,
                        RiderWallet.rider_id,
                        RiderWallet.wallet_payable_to_company,
                    )
                    .where(
                        and_(
                            RiderWallet.carry_forward_approved.is_(True),
                            RiderWallet.carry_forward_expires_at.isnot(None),
                            RiderWallet.carry_forward_expires_at <= now,
                        ),
                    ),
                )
            )
            .all()
        )
        if not rows:
            return 0

        from app.core.audit import record_audit
        from decimal import Decimal

        principal = SystemPrincipal()
        wallet_svc = RiderWalletService(session)

        for wid, rider_id, payable in rows:
            # Clear the approval.
            await session.execute(
                update(RiderWallet)
                .where(RiderWallet.id == wid)
                .values(
                    carry_forward_approved=False,
                    carry_forward_amount=Decimal("0"),
                    carry_forward_approved_by=None,
                    carry_forward_expires_at=None,
                ),
            )
            await record_audit(
                actor=principal,
                action=codes.ACTION_CARRY_FORWARD_EXPIRED,
                resource_type="rider_wallet",
                resource_id=wid,
                metadata={"rider_id": str(rider_id)},
            )
            cleared += 1
            # Re-lock if still owe money.
            if payable and payable > 0:
                await wallet_svc.lock(
                    rider_id=rider_id,
                    reason="Carry-forward expired; previous-day "
                           "settlement due",
                    principal=principal,
                )
    if cleared:
        _log.info("carry_forwards_expired", count=cleared)
    return cleared
