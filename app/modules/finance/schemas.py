from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import Field

from app.core.validation import StrictModel


# ---------------- Accounts ----------------


class AccountResponse(StrictModel):
    id: UUID
    code: str
    name: str
    type: str
    description: str | None
    is_system: bool
    is_active: bool


class AccountUpdate(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=2048)
    is_active: bool | None = None


# ---------------- Journal entries ----------------


class JournalLineInput(StrictModel):
    account_code: str = Field(..., min_length=1, max_length=16)
    debit: Decimal = Field(default=Decimal("0"), ge=0, decimal_places=2)
    credit: Decimal = Field(default=Decimal("0"), ge=0, decimal_places=2)
    memo: str | None = Field(default=None, max_length=512)


class JournalEntryCreate(StrictModel):
    entry_date: date
    narration: str = Field(..., min_length=1, max_length=512)
    currency: str = Field(default="BDT", min_length=3, max_length=3)
    reference_type: str | None = Field(default=None, max_length=48)
    reference_id: UUID | None = None
    lines: list[JournalLineInput] = Field(..., min_length=2, max_length=200)


class JournalLineResponse(StrictModel):
    id: UUID
    line_number: int
    account_id: UUID
    account_code: str
    debit: Decimal
    credit: Decimal
    memo: str | None


class JournalEntryResponse(StrictModel):
    id: UUID
    code: str
    entry_date: date
    narration: str
    source: str
    status: str
    currency: str
    reference_type: str | None
    reference_id: UUID | None
    reverses_entry_id: UUID | None
    posted_at: datetime | None
    posted_by: UUID | None
    extra: dict[str, Any]
    lines: list[JournalLineResponse]
    created_at: datetime
    updated_at: datetime


class JournalEntryReverseRequest(StrictModel):
    reason: str = Field(..., min_length=1, max_length=512)
    entry_date: date | None = None  # default = today


# ---------------- Periods + close ----------------


class PeriodResponse(StrictModel):
    id: UUID
    year: int
    month: int
    starts_on: date
    ends_on: date
    status: str
    closed_at: datetime | None
    closed_by: UUID | None
    closing_notes: str | None


class PeriodCloseRequest(StrictModel):
    year: int = Field(..., ge=2000, le=2100)
    month: int = Field(..., ge=1, le=12)
    notes: str | None = Field(default=None, max_length=2048)


class DailyCloseRequest(StrictModel):
    closing_date: date
    notes: str | None = Field(default=None, max_length=2048)


class DailyCloseResponse(StrictModel):
    id: UUID
    closing_date: date
    closed_at: datetime
    closed_by: UUID | None
    entry_count: int
    total_debits: Decimal
    total_credits: Decimal
    revenue_total: Decimal
    cogs_total: Decimal
    cash_in_total: Decimal
    cash_out_total: Decimal
    notes: str | None
    snapshot: dict[str, Any]


# ---------------- Supplier bills + payments ----------------


class SupplierBillCreate(StrictModel):
    supplier_id: UUID
    bill_number: str = Field(..., min_length=1, max_length=96)
    bill_date: date
    due_date: date | None = None
    currency: str = Field(default="BDT", min_length=3, max_length=3)
    subtotal: Decimal = Field(..., ge=0, decimal_places=2)
    tax_total: Decimal = Field(default=Decimal("0"), ge=0, decimal_places=2)
    goods_receipt_id: UUID | None = None
    notes: str | None = Field(default=None, max_length=2048)


class SupplierBillResponse(StrictModel):
    id: UUID
    code: str
    supplier_id: UUID
    goods_receipt_id: UUID | None
    bill_number: str
    bill_date: date
    due_date: date | None
    currency: str
    subtotal: Decimal
    tax_total: Decimal
    grand_total: Decimal
    amount_paid: Decimal
    status: str
    booking_entry_id: UUID | None
    notes: str | None
    created_at: datetime
    updated_at: datetime


class SupplierPaymentCreate(StrictModel):
    bill_id: UUID
    paid_on: date
    amount: Decimal = Field(..., gt=0, decimal_places=2)
    method: str = Field(..., min_length=1, max_length=32)
    bank_reference: str | None = Field(default=None, max_length=160)
    notes: str | None = Field(default=None, max_length=1024)


class SupplierPaymentResponse(StrictModel):
    id: UUID
    code: str
    bill_id: UUID
    paid_on: date
    amount: Decimal
    method: str
    bank_reference: str | None
    notes: str | None
    journal_entry_id: UUID | None
    created_at: datetime


# ---------------- COD deposits ----------------


class CodDepositCreate(StrictModel):
    rider_id: UUID
    deposit_date: date
    expected_amount: Decimal = Field(..., ge=0, decimal_places=2)
    deposited_amount: Decimal = Field(..., ge=0, decimal_places=2)
    bank_reference: str | None = Field(default=None, max_length=160)
    notes: str | None = Field(default=None, max_length=1024)


class CodDepositResponse(StrictModel):
    id: UUID
    code: str
    rider_id: UUID
    deposit_date: date
    expected_amount: Decimal
    deposited_amount: Decimal
    discrepancy: Decimal
    status: str
    bank_reference: str | None
    notes: str | None
    journal_entry_id: UUID | None
    created_at: datetime


class RiderCashOnHandResponse(StrictModel):
    rider_id: UUID
    expected_total: Decimal  # sum of unreconciled COD collections
    deposited_total: Decimal
    outstanding: Decimal


# ---------------- Refunds ----------------


class RefundPaymentRequest(StrictModel):
    refund_id: UUID
    paid_on: date
    amount: Decimal = Field(..., gt=0, decimal_places=2)
    method: str = Field(..., min_length=1, max_length=32)
    bank_reference: str | None = Field(default=None, max_length=160)
    notes: str | None = Field(default=None, max_length=1024)


class RefundRecordResponse(StrictModel):
    id: UUID
    code: str
    return_request_id: UUID
    order_id: UUID
    customer_user_id: UUID
    accrued_amount: Decimal
    paid_amount: Decimal
    status: str
    accrual_entry_id: UUID | None
    payment_entry_id: UUID | None
    paid_on: date | None
    method: str | None
    bank_reference: str | None
    notes: str | None
    created_at: datetime
    updated_at: datetime


# ---------------- Reports ----------------


class TrialBalanceRow(StrictModel):
    account_code: str
    account_name: str
    account_type: str
    debit_total: Decimal
    credit_total: Decimal
    balance: Decimal  # signed; positive = on the account's normal side


class TrialBalanceResponse(StrictModel):
    as_of: date
    rows: list[TrialBalanceRow]
    total_debits: Decimal
    total_credits: Decimal


class PnLLine(StrictModel):
    account_code: str
    account_name: str
    amount: Decimal


class PnLReport(StrictModel):
    starts_on: date
    ends_on: date
    revenue_lines: list[PnLLine]
    expense_lines: list[PnLLine]
    revenue_total: Decimal  # net (sales - returns - discounts)
    cogs_total: Decimal
    operating_expense_total: Decimal
    gross_profit: Decimal
    net_income: Decimal


class BalanceSheetLine(StrictModel):
    account_code: str
    account_name: str
    amount: Decimal


class BalanceSheetReport(StrictModel):
    as_of: date
    asset_lines: list[BalanceSheetLine]
    liability_lines: list[BalanceSheetLine]
    equity_lines: list[BalanceSheetLine]
    assets_total: Decimal
    liabilities_total: Decimal
    equity_total: Decimal
    retained_earnings: Decimal  # net income earned to date (revenue - expenses)
    balance_check: Decimal  # = assets - (liabilities + equity + retained_earnings); should be 0


class VatLedgerLine(StrictModel):
    entry_code: str
    entry_date: date
    debit: Decimal
    credit: Decimal
    memo: str | None
    reference_type: str | None
    reference_id: UUID | None


class VatLedgerResponse(StrictModel):
    starts_on: date
    ends_on: date
    output_vat_collected: Decimal  # credits to VAT-Payable
    output_vat_reversed: Decimal  # debits to VAT-Payable (refunds, reversals)
    net_payable: Decimal
    lines: list[VatLedgerLine]
