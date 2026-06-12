"""Enums for the rider wallet + settlement module.

Module 32 sits OPERATIONALLY ABOVE the per-order COD reconciliation
in ``deliveries.DeliveryAssignment.cod_*`` and the rider's bank-deposit
table in ``finance.CodDeposit``:

  - DeliveryAssignment.cod_collected  → posted into wallet ledger as
    ``cod_collection`` once the delivery is reconciled.
  - RiderWallet.cash_in_hand           tracks running custody balance.
  - RiderWallet.wallet_payable_to_company tracks what the rider OWES
    the company right now (collected − settled).
  - RiderSettlement                    = a rider's MFS payment to the
                                         company's bKash/Nagad/Rocket;
                                         awaits finance verification
                                         before the wallet clears.
  - The previous-day-unpaid LOCK is the heart of the policy: if a
    rider closed yesterday with positive payable AND no carry-forward
    approval, today's shift start + run-sheet creation are blocked.
"""

from __future__ import annotations

from enum import StrEnum


class WalletStatus(StrEnum):
    """High-level status of the wallet for a rider.

    Reflects the *current* state of obligations, not history.
    """

    CLEAR = "clear"                          # nothing owed
    HAS_COD_BALANCE = "has_cod_balance"      # rider owes, settling normal
    SETTLEMENT_SUBMITTED = "settlement_submitted"
    PARTIALLY_SETTLED = "partially_settled"
    SETTLEMENT_OVERDUE = "settlement_overdue"  # past close, still unpaid
    OVERDUE_BLOCKED = "overdue_blocked"        # day rolled over → next-day lock
    FROZEN = "frozen"                          # admin-disabled


ALL_WALLET_STATUSES: frozenset[str] = frozenset(s.value for s in WalletStatus)


class LedgerEntryType(StrEnum):
    """What kind of event produced this ledger row.

    The ledger is append-only — one row per economic event. Money moves
    through the wallet; the ledger explains *why*.
    """

    COD_COLLECTION = "cod_collection"            # rider took cash at door
    SETTLEMENT_SUBMITTED = "settlement_submitted"   # rider claims they paid
    SETTLEMENT_VERIFIED = "settlement_verified"     # finance approved
    SETTLEMENT_REJECTED = "settlement_rejected"     # finance refused
    SETTLEMENT_ADJUSTED = "settlement_adjusted"     # partial verify
    SHORTAGE_PENALTY = "shortage_penalty"           # rider short on cash
    EXCESS_ADJUSTMENT = "excess_adjustment"         # rider over (refund/credit)
    CASH_DEPOSIT_CORRECTION = "cash_deposit_correction"  # ops-only manual fix


ALL_LEDGER_ENTRY_TYPES: frozenset[str] = frozenset(
    e.value for e in LedgerEntryType
)


class LedgerDirection(StrEnum):
    """Movement of the rider's payable balance from this entry's POV.

    DEBIT  — payable goes UP (rider owes more); e.g. cod_collection
    CREDIT — payable goes DOWN (rider owes less); e.g. settlement_verified

    Mirror to traditional accounting: from the COMPANY's perspective the
    rider is a "debtor", so DEBIT entries here grow the receivable, CREDITs
    shrink it.
    """

    DEBIT = "debit"
    CREDIT = "credit"


class MfsProvider(StrEnum):
    """Mobile Financial Service the rider used to settle.

    Limited to the BD market for now; ``OTHER`` covers ad-hoc cases
    (bank transfer, cash deposit at branch, etc.) that finance still
    has to verify manually.
    """

    BKASH = "bkash"
    NAGAD = "nagad"
    ROCKET = "rocket"
    OTHER = "other"


ALL_MFS_PROVIDERS: frozenset[str] = frozenset(p.value for p in MfsProvider)


class SettlementStatus(StrEnum):
    DRAFT = "draft"               # being composed by rider; not submitted
    SUBMITTED = "submitted"       # rider posted; awaiting finance
    UNDER_REVIEW = "under_review"  # finance picked it up
    VERIFIED = "verified"          # txn confirmed; wallet credited
    REJECTED = "rejected"          # txn invalid/missing; wallet re-blocked
    ADJUSTED = "adjusted"          # partial verify (verified < submitted)


ALL_SETTLEMENT_STATUSES: frozenset[str] = frozenset(
    s.value for s in SettlementStatus
)

SETTLEMENT_TERMINAL: frozenset[str] = frozenset({
    SettlementStatus.VERIFIED.value,
    SettlementStatus.REJECTED.value,
    SettlementStatus.ADJUSTED.value,
})


class ClearanceStatus(StrEnum):
    """Per-day rider clearance for opening the next shift."""

    CLEARED = "cleared"
    PENDING_SETTLEMENT = "pending_settlement"
    PENDING_VERIFICATION = "pending_verification"
    PARTIAL_SETTLEMENT = "partial_settlement"
    OVERDUE_BLOCKED = "overdue_blocked"
    CARRY_FORWARD_APPROVED = "carry_forward_approved"


ALL_CLEARANCE_STATUSES: frozenset[str] = frozenset(
    s.value for s in ClearanceStatus
)
