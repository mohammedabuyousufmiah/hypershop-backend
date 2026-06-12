"""ORM models for rider wallet + settlement (Module 32).

Five new tables:

  rider_wallets                — one row per rider; running totals
  rider_wallet_ledger          — append-only, one row per economic event
  rider_settlements            — rider's MFS payment claims to verify
  rider_cash_limits            — per-rider caps on cash-in-hand + carry-forward
  rider_wallet_daily_summaries — closed-out per-shift snapshot

Bridges:
  - ``rider_wallets.rider_id``     → ``riders.id``  (Module 12, deliveries)
  - ``rider_wallet_ledger.shift_id`` → ``rider_shifts.id`` (Module 31)
  - ``rider_wallet_ledger.assignment_id`` → ``delivery_assignments.id``
    (so a COD-collection ledger row links back to the originating order)
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db.base import Base
from app.core.db.mixins import TimestampMixin


# ============================================================
#  rider_wallets
# ============================================================
class RiderWallet(Base, TimestampMixin):
    """One row per rider — single source of truth for running totals.

    Mutated atomically alongside an append in ``rider_wallet_ledger`` —
    every state change MUST go through ``RiderWalletService`` so the
    invariant ``balance_after = previous + signed(amount)`` holds.
    """

    __tablename__ = "rider_wallets"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    rider_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("riders.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,  # one wallet per rider
    )

    # Money totals (BDT to 2dp). All derived from the ledger; the wallet
    # row is a *cache* so the rider/admin UI doesn't sum the ledger on
    # every view.
    cash_in_hand: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, server_default=text("0"),
    )
    wallet_payable_to_company: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, server_default=text("0"),
    )
    wallet_pending_settlement: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, server_default=text("0"),
    )
    # Running credit (refund/excess from past) — offsets future payable.
    wallet_credit_adjustment: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, server_default=text("0"),
    )

    # WalletStatus enum.
    wallet_status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="clear",
    )
    last_settlement_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    # Operational flags.
    is_frozen: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"),
    )
    assignment_locked: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"),
    )
    assignment_locked_reason: Mapped[str | None] = mapped_column(
        String(255), nullable=True,
    )
    overdue_since: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    # Carry-forward state (single active approval at a time; new
    # approval overwrites the previous one).
    carry_forward_approved: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"),
    )
    carry_forward_approved_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    carry_forward_amount: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, server_default=text("0"),
    )
    carry_forward_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    __table_args__ = (
        CheckConstraint(
            "wallet_status IN ('clear','has_cod_balance',"
            "'settlement_submitted','partially_settled',"
            "'settlement_overdue','overdue_blocked','frozen')",
            name="ck_rider_wallets_status_enum",
        ),
        CheckConstraint(
            "cash_in_hand >= 0 AND wallet_payable_to_company >= 0 "
            "AND wallet_pending_settlement >= 0 "
            "AND wallet_credit_adjustment >= 0",
            name="ck_rider_wallets_nonneg",
        ),
        Index(
            "ix_rider_wallets_locked_status",
            "assignment_locked", "wallet_status",
        ),
    )


# ============================================================
#  rider_wallet_ledger (append-only)
# ============================================================
class RiderWalletLedger(Base):
    """Append-only ledger. One row per economic event.

    No TimestampMixin — only ``created_at`` matters; ledger rows are
    NEVER updated. Migration also REVOKEs UPDATE/DELETE on app role
    so any rewrite attempt blows up loudly.
    """

    __tablename__ = "rider_wallet_ledger"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    rider_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("riders.id", ondelete="RESTRICT"),
        nullable=False,
    )
    shift_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("rider_shifts.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Optional FK to a delivery_assignment — set ONLY for cod_collection
    # rows so we can grep "which orders made up the rider's payable".
    delivery_assignment_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("delivery_assignments.id", ondelete="SET NULL"),
        nullable=True,
    )
    settlement_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("rider_settlements.id", ondelete="SET NULL"),
        nullable=True,
    )

    # LedgerEntryType + LedgerDirection enums.
    entry_type: Mapped[str] = mapped_column(String(40), nullable=False)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    # Wallet's payable AFTER this entry — pre-computed by the service so
    # the ledger reads back without joining/summing.
    balance_after: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False,
    )

    # Free-form note (rider-visible).
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("(now() AT TIME ZONE 'UTC')"),
    )

    __table_args__ = (
        CheckConstraint(
            "entry_type IN ('cod_collection','settlement_submitted',"
            "'settlement_verified','settlement_rejected',"
            "'settlement_adjusted','shortage_penalty',"
            "'excess_adjustment','cash_deposit_correction')",
            name="ck_rider_wallet_ledger_entry_type_enum",
        ),
        CheckConstraint(
            "direction IN ('debit','credit')",
            name="ck_rider_wallet_ledger_direction_enum",
        ),
        CheckConstraint(
            "amount >= 0",
            name="ck_rider_wallet_ledger_amount_nonneg",
        ),
        Index(
            "ix_rider_wallet_ledger_rider_created",
            "rider_id", "created_at",
        ),
        Index(
            "ix_rider_wallet_ledger_assignment",
            "delivery_assignment_id",
        ),
    )


# ============================================================
#  rider_settlements
# ============================================================
class RiderSettlement(Base, TimestampMixin):
    """A rider's claim of "I paid the company X via bKash, here's the txn ID."

    Finance verifies → wallet credited; finance rejects → wallet
    re-locked. The transaction_id is UNIQUE so the same MFS txn can't
    be used twice.
    """

    __tablename__ = "rider_settlements"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    rider_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("riders.id", ondelete="RESTRICT"),
        nullable=False,
    )
    shift_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("rider_shifts.id", ondelete="SET NULL"),
        nullable=True,
    )
    settlement_date: Mapped[date] = mapped_column(Date, nullable=False)

    # MfsProvider enum.
    mfs_provider: Mapped[str] = mapped_column(String(16), nullable=False)
    sender_mfs_number: Mapped[str] = mapped_column(String(32), nullable=False)
    company_receiver_account: Mapped[str] = mapped_column(
        String(64), nullable=False,
    )

    submitted_amount: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False,
    )
    # Set on verify; may equal or differ from submitted (partial/excess).
    verified_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(14, 2), nullable=True,
    )

    # MFS txn id — the rider's bKash/Nagad/Rocket transaction ID. UNIQUE
    # to prevent replay.
    transaction_id: Mapped[str] = mapped_column(
        String(120), nullable=False, unique=True,
    )
    transaction_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
    proof_image_url: Mapped[str | None] = mapped_column(
        String(512), nullable=True,
    )

    # SettlementStatus enum.
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="submitted",
    )
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    __table_args__ = (
        CheckConstraint(
            "mfs_provider IN ('bkash','nagad','rocket','other')",
            name="ck_rider_settlements_mfs_enum",
        ),
        CheckConstraint(
            "status IN ('draft','submitted','under_review','verified',"
            "'rejected','adjusted')",
            name="ck_rider_settlements_status_enum",
        ),
        CheckConstraint(
            "submitted_amount > 0",
            name="ck_rider_settlements_submitted_pos",
        ),
        CheckConstraint(
            "verified_amount IS NULL OR verified_amount >= 0",
            name="ck_rider_settlements_verified_nonneg",
        ),
        Index(
            "ix_rider_settlements_rider_status_date",
            "rider_id", "status", "settlement_date",
        ),
    )


# ============================================================
#  rider_cash_limits
# ============================================================
class RiderCashLimit(Base, TimestampMixin):
    """Per-rider operational caps. Optional row — if missing, falls back
    to settings defaults (REPORT_*_DEFAULT in config).
    """

    __tablename__ = "rider_cash_limits"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    rider_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("riders.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    max_cash_in_hand: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, server_default=text("0"),
    )
    max_unsettled_amount: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, server_default=text("0"),
    )
    allow_carry_forward: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"),
    )
    carry_forward_limit: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, server_default=text("0"),
    )

    __table_args__ = (
        CheckConstraint(
            "max_cash_in_hand >= 0 AND max_unsettled_amount >= 0 "
            "AND carry_forward_limit >= 0",
            name="ck_rider_cash_limits_nonneg",
        ),
    )


# ============================================================
#  rider_wallet_daily_summaries
# ============================================================
class RiderWalletDailySummary(Base, TimestampMixin):
    """Closed-out per-shift snapshot.

    Written by ``ShiftClosureService.request_close``. UNIQUE on
    (rider_id, summary_date) — one closing snapshot per rider per day.
    The shift_id is set if the close is tied to a Module-31 shift.
    """

    __tablename__ = "rider_wallet_daily_summaries"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    rider_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("riders.id", ondelete="RESTRICT"),
        nullable=False,
    )
    shift_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("rider_shifts.id", ondelete="SET NULL"),
        nullable=True,
    )
    summary_date: Mapped[date] = mapped_column(Date, nullable=False)

    total_cod_collected: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, server_default=text("0"),
    )
    total_submitted: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, server_default=text("0"),
    )
    total_verified: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, server_default=text("0"),
    )
    total_pending: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, server_default=text("0"),
    )
    total_short: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, server_default=text("0"),
    )
    total_excess: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, server_default=text("0"),
    )
    closing_payable: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, server_default=text("0"),
    )

    # ClearanceStatus enum.
    clearance_status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="cleared",
    )
    is_cleared_for_next_shift: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true"),
    )
    blocked_amount: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, server_default=text("0"),
    )

    __table_args__ = (
        UniqueConstraint(
            "rider_id", "summary_date",
            name="uq_rider_wallet_daily_rider_date",
        ),
        CheckConstraint(
            "clearance_status IN ('cleared','pending_settlement',"
            "'pending_verification','partial_settlement',"
            "'overdue_blocked','carry_forward_approved')",
            name="ck_rider_wallet_daily_clearance_enum",
        ),
        Index(
            "ix_rider_wallet_daily_rider_date",
            "rider_id", "summary_date",
        ),
    )


# ============================================================
#  rider_cod_recharge_sessions  (Track A — Bkash Tokenized Checkout)
# ============================================================
class RiderCodRechargeSession(Base, TimestampMixin):
    """Ephemeral session linking a rider COD recharge to a Bkash payment.

    Lifecycle:
      1. Rider taps "Pay COD via Bkash" — backend opens Bkash create_payment
         and stores this row with status=initiated, provider_payment_id=<paymentID>.
      2. Rider completes flow on Bkash hosted page; Bkash redirects back.
      3. Backend executes the payment (or webhook arrives), and on capture:
         - status = "completed"
         - rider_wallet_ledger gets a "settlement_via_bkash" debit on payable_to_company
         - rider_wallets.wallet_payable_to_company is decremented atomically.
      4. On failure / cancel / expiry: status = "failed" / "cancelled" / "expired".

    Reuses ``rider_wallet_ledger`` and ``rider_wallets`` via service layer —
    NEVER mutate balances outside of ``RiderWalletService``.
    """

    __tablename__ = "rider_cod_recharge_sessions"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    rider_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("riders.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    wallet_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("rider_wallets.id", ondelete="RESTRICT"),
        nullable=False,
    )
    shift_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("rider_shifts.id", ondelete="SET NULL"),
        nullable=True,
    )

    requested_amount: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False,
    )
    captured_amount: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, server_default=text("0"),
    )

    mfs_provider: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="bkash",
    )
    payer_mfs_number: Mapped[str] = mapped_column(
        String(32), nullable=False,
    )

    # Bkash-side identifiers
    provider_payment_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, unique=True,
    )
    provider_trx_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True,
    )
    bkash_url: Mapped[str | None] = mapped_column(
        Text, nullable=True,
    )

    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="initiated",
    )
    failure_reason: Mapped[str | None] = mapped_column(
        Text, nullable=True,
    )

    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    idempotency_key: Mapped[str | None] = mapped_column(
        String(128), nullable=True, unique=True, index=True,
    )

    __table_args__ = (
        CheckConstraint(
            "mfs_provider IN ('bkash')",
            name="ck_rider_cod_recharge_mfs_provider",
        ),
        CheckConstraint(
            "status IN ('initiated','in_progress','completed','failed',"
            "'cancelled','expired')",
            name="ck_rider_cod_recharge_status",
        ),
        CheckConstraint(
            "requested_amount > 0",
            name="ck_rider_cod_recharge_amount_positive",
        ),
        Index(
            "ix_rider_cod_recharge_rider_status",
            "rider_id", "status",
        ),
        Index(
            "ix_rider_cod_recharge_provider_payment_id",
            "provider_payment_id",
            unique=True,
            postgresql_where=text("provider_payment_id IS NOT NULL"),
        ),
    )
