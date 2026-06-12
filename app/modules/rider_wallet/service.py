"""Service layer for the rider wallet module.

Four classes, one file — they share state heavily so co-locating
keeps the dependency graph trivial:

  RiderWalletService          — single source of truth for ledger writes
                                + wallet-row updates. Every economic
                                event goes through here.
  RiderSettlementService      — submit / verify / reject MFS payments.
  AssignmentEligibilityService — read-only gate consulted by Module 31's
                                start_shift + create_run_sheet.
  ShiftClosureService         — write the per-shift daily summary +
                                trigger lock if unpaid.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit
from app.core.errors import ValidationError
from app.core.logging import get_logger
from app.core.security.principal import Principal, SystemPrincipal
from app.core.time import utc_now
from app.modules.rider_wallet import codes
from app.modules.rider_wallet.errors import (
    CarryForwardLimitExceededError,
    DuplicateTransactionIdError,
    InvalidSettlementAmountError,
    SettlementNotAdjustableError,
    SettlementNotFoundError,
    WalletAssignmentLockedError,
    WalletFrozenError,
    WalletNotFoundError,
)
from app.modules.rider_wallet.repository import (
    RiderCashLimitRepository,
    RiderSettlementRepository,
    RiderWalletDailySummaryRepository,
    RiderWalletLedgerRepository,
    RiderWalletRepository,
)
from app.modules.rider_wallet.state import (
    SETTLEMENT_TERMINAL,
    ClearanceStatus,
    LedgerDirection,
    LedgerEntryType,
    SettlementStatus,
    WalletStatus,
)

_log = get_logger("hypershop.rider_wallet.service")


def _q(value: Decimal | int | float | str) -> Decimal:
    """Quantize to 2dp BDT."""
    return Decimal(str(value)).quantize(Decimal("0.01"))


# ============================================================
#  RiderWalletService
# ============================================================
class RiderWalletService:
    """Owns every mutation of wallet + ledger.

    Invariants enforced here:
      - Every ledger row stores ``balance_after`` = post-write payable.
      - ``cash_in_hand``, ``payable``, ``pending_settlement`` never go
        negative (Decimal max() guard).
      - Status transitions follow this hierarchy (high → low priority):
        FROZEN > OVERDUE_BLOCKED > SETTLEMENT_OVERDUE >
        SETTLEMENT_SUBMITTED > PARTIALLY_SETTLED > HAS_COD_BALANCE >
        CLEAR. The recompute helper picks the topmost applicable.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.wallets = RiderWalletRepository(session)
        self.ledgers = RiderWalletLedgerRepository(session)
        self.summaries = RiderWalletDailySummaryRepository(session)
        self.limits = RiderCashLimitRepository(session)

    # ------------------------------------------------------------------
    # Wallet bootstrap
    # ------------------------------------------------------------------
    async def get_or_create(
        self, *, rider_id: UUID,
        principal: Principal | SystemPrincipal | None = None,
    ):
        wallet = await self.wallets.get_by_rider(rider_id)
        if wallet is not None:
            return wallet
        wallet = await self.wallets.add(rider_id=rider_id)
        await record_audit(
            actor=principal,
            action=codes.ACTION_WALLET_CREATED,
            resource_type="rider_wallet",
            resource_id=wallet.id,
            metadata={"rider_id": str(rider_id)},
        )
        return wallet

    # ------------------------------------------------------------------
    # COD collection (called by deliveries-reconciled handler)
    # ------------------------------------------------------------------
    async def post_cod_collection(
        self, *,
        rider_id: UUID,
        delivery_assignment_id: UUID,
        amount: Decimal,
        shift_id: UUID | None = None,
        principal: Principal | SystemPrincipal | None = None,
        note: str | None = None,
    ):
        """Idempotent: if a cod_collection ledger row already exists for
        this assignment, this is a no-op (returns the existing wallet).

        Wallet effects:
          - cash_in_hand += amount
          - wallet_payable_to_company += amount
          - status → has_cod_balance (unless higher-priority status)
        """
        if amount <= 0:
            raise InvalidSettlementAmountError(
                "COD collection amount must be > 0",
            )
        amount = _q(amount)

        # Idempotency check.
        already = await self.ledgers.has_assignment_entry(
            delivery_assignment_id=delivery_assignment_id,
            entry_type=LedgerEntryType.COD_COLLECTION.value,
        )
        if already:
            return await self.get_or_create(
                rider_id=rider_id, principal=principal,
            )

        wallet = await self.get_or_create(
            rider_id=rider_id, principal=principal,
        )
        new_payable = _q(wallet.wallet_payable_to_company + amount)
        new_cash = _q(wallet.cash_in_hand + amount)
        await self.wallets.update(
            wallet_id=wallet.id,
            cash_in_hand=new_cash,
            wallet_payable_to_company=new_payable,
            wallet_status=self._derive_status(
                wallet, payable=new_payable, pending=wallet.wallet_pending_settlement,
            ),
        )
        await self.ledgers.add(
            rider_id=rider_id,
            shift_id=shift_id,
            delivery_assignment_id=delivery_assignment_id,
            entry_type=LedgerEntryType.COD_COLLECTION.value,
            direction=LedgerDirection.DEBIT.value,
            amount=amount,
            balance_after=new_payable,
            note=note,
            created_by=getattr(principal, "user_id", None) if isinstance(
                principal, Principal,
            ) else None,
        )
        await record_audit(
            actor=principal,
            action=codes.ACTION_COD_COLLECTED,
            resource_type="rider_wallet",
            resource_id=wallet.id,
            metadata={
                "rider_id": str(rider_id),
                "amount": str(amount),
                "delivery_assignment_id": str(delivery_assignment_id),
            },
        )
        return await self.wallets.get_by_rider(rider_id)

    # ------------------------------------------------------------------
    # Lock / unlock / freeze
    # ------------------------------------------------------------------
    async def lock(
        self, *,
        rider_id: UUID,
        reason: str,
        principal: Principal | SystemPrincipal | None = None,
    ):
        wallet = await self.get_or_create(
            rider_id=rider_id, principal=principal,
        )
        await self.wallets.update(
            wallet_id=wallet.id,
            assignment_locked=True,
            assignment_locked_reason=reason,
            wallet_status=WalletStatus.OVERDUE_BLOCKED.value,
            overdue_since=wallet.overdue_since or utc_now(),
        )
        await record_audit(
            actor=principal,
            action=codes.ACTION_WALLET_LOCKED,
            resource_type="rider_wallet",
            resource_id=wallet.id,
            metadata={"rider_id": str(rider_id), "reason": reason},
        )
        return await self.wallets.get_by_rider(rider_id)

    async def unlock(
        self, *,
        rider_id: UUID,
        principal: Principal | SystemPrincipal | None = None,
    ):
        wallet = await self.wallets.get_by_rider(rider_id)
        if wallet is None:
            raise WalletNotFoundError("Wallet not found.")
        if wallet.is_frozen:
            raise WalletFrozenError(
                "Wallet is frozen — unfreeze before unlocking.",
            )
        new_status = self._derive_status(
            wallet,
            payable=wallet.wallet_payable_to_company,
            pending=wallet.wallet_pending_settlement,
            assume_unlocked=True,
        )
        await self.wallets.update(
            wallet_id=wallet.id,
            assignment_locked=False,
            assignment_locked_reason=None,
            overdue_since=None,
            wallet_status=new_status,
        )
        await record_audit(
            actor=principal,
            action=codes.ACTION_WALLET_UNLOCKED,
            resource_type="rider_wallet",
            resource_id=wallet.id,
            metadata={"rider_id": str(rider_id)},
        )
        return await self.wallets.get_by_rider(rider_id)

    async def freeze(
        self, *,
        rider_id: UUID,
        reason: str,
        principal: Principal | SystemPrincipal,
    ):
        wallet = await self.get_or_create(
            rider_id=rider_id, principal=principal,
        )
        await self.wallets.update(
            wallet_id=wallet.id,
            is_frozen=True,
            assignment_locked=True,
            assignment_locked_reason=reason,
            wallet_status=WalletStatus.FROZEN.value,
        )
        await record_audit(
            actor=principal,
            action=codes.ACTION_WALLET_FROZEN,
            resource_type="rider_wallet",
            resource_id=wallet.id,
            metadata={"rider_id": str(rider_id), "reason": reason},
        )
        return await self.wallets.get_by_rider(rider_id)

    async def unfreeze(
        self, *,
        rider_id: UUID,
        principal: Principal | SystemPrincipal,
    ):
        wallet = await self.wallets.get_by_rider(rider_id)
        if wallet is None:
            raise WalletNotFoundError("Wallet not found.")
        new_status = self._derive_status(
            wallet,
            payable=wallet.wallet_payable_to_company,
            pending=wallet.wallet_pending_settlement,
            assume_frozen=False,
        )
        # Unfreeze keeps assignment_locked if there's still unpaid.
        still_locked = wallet.wallet_payable_to_company > 0
        await self.wallets.update(
            wallet_id=wallet.id,
            is_frozen=False,
            assignment_locked=still_locked,
            assignment_locked_reason=(
                "Previous-day settlement due" if still_locked else None
            ),
            wallet_status=new_status,
        )
        await record_audit(
            actor=principal,
            action=codes.ACTION_WALLET_UNFROZEN,
            resource_type="rider_wallet",
            resource_id=wallet.id,
            metadata={"rider_id": str(rider_id)},
        )
        return await self.wallets.get_by_rider(rider_id)

    # ------------------------------------------------------------------
    # Carry-forward approval
    # ------------------------------------------------------------------
    async def approve_carry_forward(
        self, *,
        rider_id: UUID,
        amount: Decimal,
        expires_at: datetime,
        principal: Principal,
    ):
        wallet = await self.get_or_create(
            rider_id=rider_id, principal=principal,
        )
        amount = _q(amount)

        # Cap by per-rider limit if set.
        limit_row = await self.limits.get_by_rider(rider_id)
        if limit_row is not None and limit_row.allow_carry_forward:
            if amount > limit_row.carry_forward_limit:
                raise CarryForwardLimitExceededError(
                    f"Approval ({amount}) exceeds rider's carry-forward "
                    f"limit ({limit_row.carry_forward_limit}).",
                )

        await self.wallets.update(
            wallet_id=wallet.id,
            carry_forward_approved=True,
            carry_forward_amount=amount,
            carry_forward_approved_by=principal.user_id,
            carry_forward_expires_at=expires_at,
            # Approval lifts the lock immediately.
            assignment_locked=False,
            assignment_locked_reason=None,
        )
        await record_audit(
            actor=principal,
            action=codes.ACTION_CARRY_FORWARD_APPROVED,
            resource_type="rider_wallet",
            resource_id=wallet.id,
            metadata={
                "rider_id": str(rider_id),
                "amount": str(amount),
                "expires_at": expires_at.isoformat(),
            },
        )
        return await self.wallets.get_by_rider(rider_id)

    async def reject_carry_forward(
        self, *,
        rider_id: UUID,
        principal: Principal,
        reason: str | None = None,
    ):
        wallet = await self.wallets.get_by_rider(rider_id)
        if wallet is None:
            raise WalletNotFoundError("Wallet not found.")
        await self.wallets.update(
            wallet_id=wallet.id,
            carry_forward_approved=False,
            carry_forward_amount=Decimal("0"),
            carry_forward_approved_by=None,
            carry_forward_expires_at=None,
            # If there's still unpaid, re-apply lock.
            assignment_locked=wallet.wallet_payable_to_company > 0,
            assignment_locked_reason=(
                "Carry-forward rejected; previous-day settlement due"
                if wallet.wallet_payable_to_company > 0 else None
            ),
        )
        await record_audit(
            actor=principal,
            action=codes.ACTION_CARRY_FORWARD_REJECTED,
            resource_type="rider_wallet",
            resource_id=wallet.id,
            metadata={"rider_id": str(rider_id), "reason": reason},
        )
        return await self.wallets.get_by_rider(rider_id)

    # ------------------------------------------------------------------
    # Cash limits
    # ------------------------------------------------------------------
    async def set_cash_limits(
        self, *,
        rider_id: UUID,
        max_cash_in_hand: Decimal,
        max_unsettled_amount: Decimal,
        allow_carry_forward: bool,
        carry_forward_limit: Decimal,
        principal: Principal,
    ):
        row = await self.limits.upsert(
            rider_id=rider_id,
            max_cash_in_hand=_q(max_cash_in_hand),
            max_unsettled_amount=_q(max_unsettled_amount),
            allow_carry_forward=allow_carry_forward,
            carry_forward_limit=_q(carry_forward_limit),
        )
        await record_audit(
            actor=principal,
            action=codes.ACTION_CASH_LIMIT_UPDATED,
            resource_type="rider_cash_limit",
            resource_id=row.id,
            metadata={
                "rider_id": str(rider_id),
                "max_cash_in_hand": str(max_cash_in_hand),
                "allow_carry_forward": allow_carry_forward,
            },
        )
        return row

    # ------------------------------------------------------------------
    # Status derivation helper
    # ------------------------------------------------------------------
    @staticmethod
    def _derive_status(
        wallet,
        *,
        payable: Decimal,
        pending: Decimal,
        assume_frozen: bool | None = None,
        assume_unlocked: bool = False,
    ) -> str:
        """Pick the right wallet_status given current numeric state.

        Priority: FROZEN > OVERDUE_BLOCKED > SETTLEMENT_SUBMITTED >
                  HAS_COD_BALANCE > CLEAR.
        """
        is_frozen = (
            assume_frozen if assume_frozen is not None else wallet.is_frozen
        )
        if is_frozen:
            return WalletStatus.FROZEN.value
        if not assume_unlocked and wallet.assignment_locked:
            return WalletStatus.OVERDUE_BLOCKED.value
        if pending > 0 and payable > 0 and pending < payable:
            return WalletStatus.PARTIALLY_SETTLED.value
        if pending > 0:
            return WalletStatus.SETTLEMENT_SUBMITTED.value
        if payable > 0:
            return WalletStatus.HAS_COD_BALANCE.value
        return WalletStatus.CLEAR.value


# ============================================================
#  RiderSettlementService
# ============================================================
class RiderSettlementService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.settlements = RiderSettlementRepository(session)
        self.wallet_svc = RiderWalletService(session)

    async def submit(
        self, *,
        rider_id: UUID,
        shift_id: UUID | None,
        mfs_provider: str,
        sender_mfs_number: str,
        company_receiver_account: str,
        submitted_amount: Decimal,
        transaction_id: str,
        transaction_time: datetime,
        proof_image_url: str | None,
        principal: Principal,
    ):
        """Rider claims they paid the company. Creates a SUBMITTED row +
        moves submitted_amount onto wallet.pending_settlement.

        Idempotency: txn_id is UNIQUE in the DB; duplicate raises a
        409 via DuplicateTransactionIdError.
        """
        if submitted_amount <= 0:
            raise InvalidSettlementAmountError(
                "Settlement amount must be > 0",
            )
        submitted_amount = _q(submitted_amount)

        existing = await self.settlements.get_by_transaction_id(
            transaction_id,
        )
        if existing is not None:
            raise DuplicateTransactionIdError(
                f"Transaction ID {transaction_id} already submitted "
                f"(settlement {existing.id}).",
            )

        wallet = await self.wallet_svc.get_or_create(
            rider_id=rider_id, principal=principal,
        )
        if wallet.is_frozen:
            raise WalletFrozenError(
                "Cannot submit settlement on a frozen wallet.",
            )

        row = await self.settlements.add(
            rider_id=rider_id,
            shift_id=shift_id,
            settlement_date=date.today(),
            mfs_provider=mfs_provider,
            sender_mfs_number=sender_mfs_number,
            company_receiver_account=company_receiver_account,
            submitted_amount=submitted_amount,
            transaction_id=transaction_id,
            transaction_time=transaction_time,
            proof_image_url=proof_image_url,
            status=SettlementStatus.SUBMITTED.value,
        )
        new_pending = _q(wallet.wallet_pending_settlement + submitted_amount)
        await self.wallet_svc.wallets.update(
            wallet_id=wallet.id,
            wallet_pending_settlement=new_pending,
            wallet_status=RiderWalletService._derive_status(
                wallet,
                payable=wallet.wallet_payable_to_company,
                pending=new_pending,
            ),
        )
        await self.wallet_svc.ledgers.add(
            rider_id=rider_id,
            shift_id=shift_id,
            settlement_id=row.id,
            entry_type=LedgerEntryType.SETTLEMENT_SUBMITTED.value,
            direction=LedgerDirection.CREDIT.value,
            amount=submitted_amount,
            balance_after=wallet.wallet_payable_to_company,
            note=f"Settlement submitted: {transaction_id}",
            created_by=principal.user_id,
        )
        await record_audit(
            actor=principal,
            action=codes.ACTION_SETTLEMENT_SUBMITTED,
            resource_type="rider_settlement",
            resource_id=row.id,
            metadata={
                "rider_id": str(rider_id),
                "amount": str(submitted_amount),
                "transaction_id": transaction_id,
                "mfs_provider": mfs_provider,
            },
        )
        return row

    async def verify(
        self, *,
        settlement_id: UUID,
        verified_amount: Decimal,
        principal: Principal,
        review_note: str | None = None,
    ):
        """Finance verifies. ``verified_amount`` may equal, be less
        than, or be greater than submitted_amount.

          - equal     → status=verified, payable -= verified
          - less      → status=adjusted (partial), payable -= verified
                        (rider will need a top-up settlement for the
                        difference; ops follows up with the rider)
          - greater   → status=adjusted (excess), payable -= verified;
                        if it goes negative we record an
                        excess_adjustment ledger row to credit the rider
        """
        s = await self.settlements.get(settlement_id)
        if s is None:
            raise SettlementNotFoundError("Settlement not found.")
        if s.status in SETTLEMENT_TERMINAL:
            raise SettlementNotAdjustableError(
                f"Settlement is already in terminal status '{s.status}'.",
            )
        verified_amount = _q(verified_amount)
        if verified_amount < 0:
            raise InvalidSettlementAmountError(
                "Verified amount must be >= 0.",
            )

        wallet = await self.wallet_svc.wallets.get_by_rider(s.rider_id)
        if wallet is None:
            raise WalletNotFoundError("Wallet not found.")

        is_partial = verified_amount != s.submitted_amount
        new_status = (
            SettlementStatus.ADJUSTED.value if is_partial
            else SettlementStatus.VERIFIED.value
        )

        # Move submitted_amount OUT of pending; payable shrinks by
        # verified amount (which may be different).
        new_pending = _q(
            max(
                Decimal("0"),
                wallet.wallet_pending_settlement - s.submitted_amount,
            ),
        )
        new_payable = _q(
            max(
                Decimal("0"),
                wallet.wallet_payable_to_company - verified_amount,
            ),
        )
        new_cash = _q(
            max(Decimal("0"), wallet.cash_in_hand - verified_amount),
        )
        # If verified > payable (excess), the difference becomes credit.
        excess = (
            verified_amount - wallet.wallet_payable_to_company
            if verified_amount > wallet.wallet_payable_to_company
            else Decimal("0")
        )
        new_credit = _q(wallet.wallet_credit_adjustment + excess)

        await self.settlements.update(
            settlement_id=settlement_id,
            status=new_status,
            verified_amount=verified_amount,
            reviewed_by=principal.user_id,
            reviewed_at=utc_now(),
            review_note=review_note,
        )
        await self.wallet_svc.wallets.update(
            wallet_id=wallet.id,
            wallet_pending_settlement=new_pending,
            wallet_payable_to_company=new_payable,
            cash_in_hand=new_cash,
            wallet_credit_adjustment=new_credit,
            last_settlement_at=utc_now(),
            wallet_status=RiderWalletService._derive_status(
                wallet, payable=new_payable, pending=new_pending,
            ),
        )
        await self.wallet_svc.ledgers.add(
            rider_id=s.rider_id,
            shift_id=s.shift_id,
            settlement_id=s.id,
            entry_type=LedgerEntryType.SETTLEMENT_VERIFIED.value,
            direction=LedgerDirection.CREDIT.value,
            amount=verified_amount,
            balance_after=new_payable,
            note=f"Settlement verified: {s.transaction_id}",
            created_by=principal.user_id,
        )
        if excess > 0:
            await self.wallet_svc.ledgers.add(
                rider_id=s.rider_id,
                shift_id=s.shift_id,
                settlement_id=s.id,
                entry_type=LedgerEntryType.EXCESS_ADJUSTMENT.value,
                direction=LedgerDirection.CREDIT.value,
                amount=excess,
                balance_after=new_payable,
                note=f"Excess credit from settlement {s.transaction_id}",
                created_by=principal.user_id,
            )

        # Auto-unlock if fully cleared.
        if new_payable == 0 and new_pending == 0 and not wallet.is_frozen:
            await self.wallet_svc.wallets.update(
                wallet_id=wallet.id,
                assignment_locked=False,
                assignment_locked_reason=None,
                overdue_since=None,
            )

        await record_audit(
            actor=principal,
            action=codes.ACTION_SETTLEMENT_VERIFIED,
            resource_type="rider_settlement",
            resource_id=s.id,
            metadata={
                "rider_id": str(s.rider_id),
                "submitted": str(s.submitted_amount),
                "verified": str(verified_amount),
                "is_partial": is_partial,
            },
        )
        # Outbox event → SMS notifier handler (registered in handlers.py).
        # We use the outbox so a transient SMS provider failure doesn't
        # roll back finance's verify decision; the dispatcher retries
        # with backoff.
        from app.core.events.outbox import enqueue_outbox
        from app.modules.rider_wallet.events import (
            EVT_SETTLEMENT_ADJUSTED,
            EVT_SETTLEMENT_VERIFIED,
        )
        await enqueue_outbox(
            type=EVT_SETTLEMENT_ADJUSTED if is_partial
                 else EVT_SETTLEMENT_VERIFIED,
            payload={
                "settlement_id": str(s.id),
                "rider_id": str(s.rider_id),
                "submitted_amount": str(s.submitted_amount),
                "verified_amount": str(verified_amount),
                "transaction_id": s.transaction_id,
            },
        )
        return await self.settlements.get(settlement_id)

    async def reject(
        self, *,
        settlement_id: UUID,
        principal: Principal,
        review_note: str,
    ):
        s = await self.settlements.get(settlement_id)
        if s is None:
            raise SettlementNotFoundError("Settlement not found.")
        if s.status in SETTLEMENT_TERMINAL:
            raise SettlementNotAdjustableError(
                f"Settlement is already in terminal status '{s.status}'.",
            )

        wallet = await self.wallet_svc.wallets.get_by_rider(s.rider_id)
        if wallet is None:
            raise WalletNotFoundError("Wallet not found.")

        # Rollback the pending — the rider's claim was bogus.
        new_pending = _q(
            max(
                Decimal("0"),
                wallet.wallet_pending_settlement - s.submitted_amount,
            ),
        )
        await self.settlements.update(
            settlement_id=settlement_id,
            status=SettlementStatus.REJECTED.value,
            reviewed_by=principal.user_id,
            reviewed_at=utc_now(),
            review_note=review_note,
        )
        await self.wallet_svc.wallets.update(
            wallet_id=wallet.id,
            wallet_pending_settlement=new_pending,
            wallet_status=RiderWalletService._derive_status(
                wallet,
                payable=wallet.wallet_payable_to_company,
                pending=new_pending,
            ),
        )
        await self.wallet_svc.ledgers.add(
            rider_id=s.rider_id,
            shift_id=s.shift_id,
            settlement_id=s.id,
            entry_type=LedgerEntryType.SETTLEMENT_REJECTED.value,
            direction=LedgerDirection.DEBIT.value,
            amount=s.submitted_amount,
            balance_after=wallet.wallet_payable_to_company,
            note=f"Settlement rejected: {s.transaction_id} — {review_note}",
            created_by=principal.user_id,
        )
        # Re-apply lock if there's still payable.
        if wallet.wallet_payable_to_company > 0 and not wallet.assignment_locked:
            await self.wallet_svc.lock(
                rider_id=s.rider_id,
                reason="Settlement rejected; previous-day settlement due",
                principal=principal,
            )
        await record_audit(
            actor=principal,
            action=codes.ACTION_SETTLEMENT_REJECTED,
            resource_type="rider_settlement",
            resource_id=s.id,
            metadata={
                "rider_id": str(s.rider_id),
                "submitted": str(s.submitted_amount),
                "reason": review_note,
            },
        )
        # Outbox notify (see verify() for rationale).
        from app.core.events.outbox import enqueue_outbox
        from app.modules.rider_wallet.events import EVT_SETTLEMENT_REJECTED
        await enqueue_outbox(
            type=EVT_SETTLEMENT_REJECTED,
            payload={
                "settlement_id": str(s.id),
                "rider_id": str(s.rider_id),
                "submitted_amount": str(s.submitted_amount),
                "transaction_id": s.transaction_id,
                "reason": review_note,
            },
        )
        return await self.settlements.get(settlement_id)


# ============================================================
#  AssignmentEligibilityService
# ============================================================
class AssignmentEligibilityService:
    """Read-only gate consulted by Module 31 before allowing a rider
    to start a shift OR before an admin creates a run sheet.

    Decision tree:
      - is_frozen           → BLOCK (reason=Wallet frozen)
      - assignment_locked   → BLOCK (reason=db.assignment_locked_reason)
      - payable > 0 && carry_forward active → ALLOW
      - payable > 0 && no carry_forward     → BLOCK (reason=Previous-day
                                                     settlement due)
      - else                → ALLOW
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.wallets = RiderWalletRepository(session)

    async def check(self, *, rider_id: UUID) -> tuple[bool, str | None]:
        wallet = await self.wallets.get_by_rider(rider_id)
        if wallet is None:
            # No wallet yet = no obligations = allowed.
            return True, None
        if wallet.is_frozen:
            return False, "Wallet frozen by admin."
        if wallet.assignment_locked:
            return False, (
                wallet.assignment_locked_reason
                or "Previous-day settlement due"
            )
        if wallet.wallet_payable_to_company > 0:
            # Rider-already-submitted carve-out: if they've submitted
            # settlement covering the full payable, allow shift start
            # while finance verifies. Reject path re-applies lock.
            if wallet.wallet_pending_settlement >= wallet.wallet_payable_to_company:
                return True, None
            now = utc_now()
            cf_active = (
                wallet.carry_forward_approved
                and wallet.carry_forward_expires_at is not None
                and wallet.carry_forward_expires_at > now
                and wallet.carry_forward_amount >= wallet.wallet_payable_to_company
            )
            if cf_active:
                return True, None
            return False, "Previous-day settlement due"
        return True, None

    async def assert_or_raise(self, *, rider_id: UUID) -> None:
        """Convenience: raise WalletAssignmentLockedError on block."""
        ok, reason = await self.check(rider_id=rider_id)
        if not ok:
            raise WalletAssignmentLockedError(
                reason or "Rider assignment is locked.",
            )

    async def apply_lock_if_unpaid(
        self, *,
        rider_id: UUID,
        principal: Principal | SystemPrincipal | None = None,
    ) -> bool:
        """Used by the nightly sweep + on-shift-close: if rider has
        unpaid balance + no carry-forward, lock them.
        Returns True if a lock was applied (or already in place).

        Rider-friendly carve-out: a rider who SUBMITTED settlement(s)
        covering their full payable doesn't get locked. They did their
        part — just waiting for finance to verify. Locking them
        because finance hasn't verified yet would be unfair friction
        (they'd be unable to start tomorrow's shift through no fault
        of their own).
        """
        wallet = await self.wallets.get_by_rider(rider_id)
        if wallet is None or wallet.wallet_payable_to_company <= 0:
            return False
        if wallet.assignment_locked:
            return True

        # Rider-already-submitted carve-out — pending_settlement covers
        # the payable. Finance still needs to verify; if they reject,
        # the reject path re-applies the lock immediately.
        if wallet.wallet_pending_settlement >= wallet.wallet_payable_to_company:
            return False

        now = utc_now()
        cf_active = (
            wallet.carry_forward_approved
            and wallet.carry_forward_expires_at is not None
            and wallet.carry_forward_expires_at > now
            and wallet.carry_forward_amount >= wallet.wallet_payable_to_company
        )
        if cf_active:
            return False
        svc = RiderWalletService(self.session)
        await svc.lock(
            rider_id=rider_id,
            reason="Previous-day settlement due",
            principal=principal,
        )
        return True


# ============================================================
#  ShiftClosureService
# ============================================================
class ShiftClosureService:
    """Generates the per-shift closing snapshot in
    rider_wallet_daily_summaries + applies the lock if needed.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.wallet_svc = RiderWalletService(session)
        self.eligibility = AssignmentEligibilityService(session)
        self.summaries = RiderWalletDailySummaryRepository(session)
        self.ledgers = RiderWalletLedgerRepository(session)

    async def request_close(
        self, *,
        rider_id: UUID,
        shift_id: UUID | None,
        summary_date: date | None = None,
        principal: Principal,
    ):
        """Idempotent: re-calling for the same (rider, date) returns
        the existing summary unchanged. To refresh the numbers, ops
        must use a separate "recompute" admin endpoint (not built
        here — out of scope for v1).
        """
        summary_date = summary_date or date.today()

        existing = await self.summaries.get_for_date(
            rider_id=rider_id, summary_date=summary_date,
        )
        if existing is not None:
            return existing

        wallet = await self.wallet_svc.get_or_create(
            rider_id=rider_id, principal=principal,
        )

        # Aggregate the day's ledger.
        cod_total = await self.ledgers.sum_for_rider_in_range(
            rider_id, starts_on=summary_date, ends_on=summary_date,
            entry_type=LedgerEntryType.COD_COLLECTION.value,
        )
        submitted_total = await self.ledgers.sum_for_rider_in_range(
            rider_id, starts_on=summary_date, ends_on=summary_date,
            entry_type=LedgerEntryType.SETTLEMENT_SUBMITTED.value,
        )
        verified_total = await self.ledgers.sum_for_rider_in_range(
            rider_id, starts_on=summary_date, ends_on=summary_date,
            entry_type=LedgerEntryType.SETTLEMENT_VERIFIED.value,
        )

        closing = _q(wallet.wallet_payable_to_company)
        is_cleared = closing == 0
        if is_cleared:
            clearance = ClearanceStatus.CLEARED.value
        elif wallet.wallet_pending_settlement > 0:
            clearance = ClearanceStatus.PENDING_VERIFICATION.value
        else:
            clearance = ClearanceStatus.PENDING_SETTLEMENT.value

        summary = await self.summaries.add(
            rider_id=rider_id,
            shift_id=shift_id,
            summary_date=summary_date,
            total_cod_collected=_q(cod_total),
            total_submitted=_q(submitted_total),
            total_verified=_q(verified_total),
            total_pending=_q(wallet.wallet_pending_settlement),
            closing_payable=closing,
            clearance_status=clearance,
            is_cleared_for_next_shift=is_cleared,
            blocked_amount=closing if not is_cleared else Decimal("0"),
        )

        # Apply the lock if unpaid + no carry-forward.
        if not is_cleared:
            await self.eligibility.apply_lock_if_unpaid(
                rider_id=rider_id, principal=principal,
            )

        await record_audit(
            actor=principal,
            action=codes.ACTION_DAILY_SUMMARY_CLOSED,
            resource_type="rider_wallet_daily_summary",
            resource_id=summary.id,
            metadata={
                "rider_id": str(rider_id),
                "summary_date": summary_date.isoformat(),
                "closing_payable": str(closing),
                "clearance_status": clearance,
            },
        )
        return summary
