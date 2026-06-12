"""Pydantic v2 wire schemas for rider wallet + settlement."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import Field

from app.core.validation import StrictModel


# ============================================================
#  Wallet
# ============================================================
class WalletResponse(StrictModel):
    id: UUID
    rider_id: UUID
    cash_in_hand: Decimal
    wallet_payable_to_company: Decimal
    wallet_pending_settlement: Decimal
    wallet_credit_adjustment: Decimal
    wallet_status: str
    last_settlement_at: datetime | None
    is_frozen: bool
    assignment_locked: bool
    assignment_locked_reason: str | None
    overdue_since: datetime | None
    carry_forward_approved: bool
    carry_forward_amount: Decimal
    carry_forward_expires_at: datetime | None


class ClearanceStatusResponse(StrictModel):
    rider_id: UUID
    assignment_locked: bool
    wallet_status: str
    payable_amount: Decimal
    pending_settlement: Decimal
    carry_forward_approved: bool
    carry_forward_active: bool
    reason: str | None


class WalletListResponse(StrictModel):
    items: list[WalletResponse]


# ============================================================
#  Ledger
# ============================================================
class LedgerEntryResponse(StrictModel):
    id: UUID
    rider_id: UUID
    shift_id: UUID | None
    delivery_assignment_id: UUID | None
    settlement_id: UUID | None
    entry_type: str
    direction: str
    amount: Decimal
    balance_after: Decimal
    note: str | None
    created_at: datetime


class LedgerListResponse(StrictModel):
    items: list[LedgerEntryResponse]


# ============================================================
#  Settlement
# ============================================================
class SettlementSubmitRequest(StrictModel):
    shift_id: UUID | None = None
    mfs_provider: Literal["bkash", "nagad", "rocket", "other"]
    sender_mfs_number: str = Field(min_length=6, max_length=32)
    submitted_amount: Decimal = Field(gt=0)
    transaction_id: str = Field(min_length=4, max_length=120)
    transaction_time: datetime
    proof_image_url: str | None = Field(default=None, max_length=512)


class SettlementVerifyRequest(StrictModel):
    verified_amount: Decimal = Field(ge=0)
    review_note: str | None = Field(default=None, max_length=1024)


class SettlementRejectRequest(StrictModel):
    review_note: str = Field(min_length=1, max_length=1024)


class SettlementResponse(StrictModel):
    id: UUID
    rider_id: UUID
    shift_id: UUID | None
    settlement_date: date
    mfs_provider: str
    sender_mfs_number: str
    company_receiver_account: str
    submitted_amount: Decimal
    verified_amount: Decimal | None
    transaction_id: str
    transaction_time: datetime
    proof_image_url: str | None
    status: str
    review_note: str | None
    reviewed_at: datetime | None
    created_at: datetime


class SettlementListResponse(StrictModel):
    items: list[SettlementResponse]


# ============================================================
#  Daily summary
# ============================================================
class DailySummaryResponse(StrictModel):
    id: UUID
    rider_id: UUID
    shift_id: UUID | None
    summary_date: date
    total_cod_collected: Decimal
    total_submitted: Decimal
    total_verified: Decimal
    total_pending: Decimal
    closing_payable: Decimal
    clearance_status: str
    is_cleared_for_next_shift: bool
    blocked_amount: Decimal


class DailySummaryListResponse(StrictModel):
    items: list[DailySummaryResponse]


# ============================================================
#  Admin actions
# ============================================================
class FreezeRequest(StrictModel):
    reason: str = Field(min_length=1, max_length=512)


class UnlockRequest(StrictModel):
    note: str | None = Field(default=None, max_length=512)


class CarryForwardApproveRequest(StrictModel):
    amount: Decimal = Field(gt=0)
    expires_at: datetime


class CarryForwardRejectRequest(StrictModel):
    reason: str | None = Field(default=None, max_length=512)


class CashLimitUpdateRequest(StrictModel):
    max_cash_in_hand: Decimal = Field(ge=0)
    max_unsettled_amount: Decimal = Field(ge=0)
    allow_carry_forward: bool = False
    carry_forward_limit: Decimal = Field(ge=0, default=Decimal("0"))


class CashLimitResponse(StrictModel):
    id: UUID
    rider_id: UUID
    max_cash_in_hand: Decimal
    max_unsettled_amount: Decimal
    allow_carry_forward: bool
    carry_forward_limit: Decimal


# ============================================================
#  Closure
# ============================================================
class ShiftCloseRequest(StrictModel):
    shift_id: UUID | None = None
    summary_date: date | None = None


# ============================================================
#  COD recharge — Bkash Tokenized Checkout (Track A)
# ============================================================
class CodRechargeInitiateRequest(StrictModel):
    amount: Decimal = Field(gt=0, description="BDT amount to recharge to company")
    mfs_provider: Literal["bkash"] = "bkash"
    mfs_number: str = Field(min_length=11, max_length=11, pattern=r"^01\d{9}$")
    idempotency_key: str | None = Field(default=None, max_length=128)


class CodRechargeInitiateResponse(StrictModel):
    recharge_session_id: UUID
    bkash_url: str = Field(description="Open in WebView; rider completes flow on Bkash hosted page")
    provider_payment_id: str
    expires_at: datetime
    masked_payer_number: str = Field(description="e.g. ***1234 — for display only")


class CodRechargeVerifyRequest(StrictModel):
    recharge_session_id: UUID
    provider_payment_id: str = Field(min_length=4, max_length=64)


class CodRechargeVerifyResponse(StrictModel):
    recharge_session_id: UUID
    status: Literal["completed", "failed", "in_progress", "cancelled", "expired"]
    captured_amount_bdt: Decimal
    provider_trx_id: str | None
    failure_reason: str | None
    wallet_payable_to_company_after: Decimal
