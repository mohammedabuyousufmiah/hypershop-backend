"""Finance enums.

Five canonical account types — every account in the chart of accounts
must be exactly one of these. Normal balance side derives from the type:
assets/expenses are debit-normal, liabilities/equity/revenue are
credit-normal. Sign of the running balance is computed from this.
"""

from __future__ import annotations

from enum import StrEnum


class AccountType(StrEnum):
    ASSET = "asset"
    LIABILITY = "liability"
    EQUITY = "equity"
    REVENUE = "revenue"
    EXPENSE = "expense"


class NormalSide(StrEnum):
    DEBIT = "debit"
    CREDIT = "credit"


def normal_side_for(account_type: AccountType) -> NormalSide:
    if account_type in (AccountType.ASSET, AccountType.EXPENSE):
        return NormalSide.DEBIT
    return NormalSide.CREDIT


class JournalEntryStatus(StrEnum):
    DRAFT = "draft"
    POSTED = "posted"
    REVERSED = "reversed"  # superseded by a reversing entry; kept for audit


class JournalEntrySource(StrEnum):
    MANUAL = "manual"
    ORDER_REVENUE = "order_revenue"
    ORDER_COGS = "order_cogs"
    ORDER_REVERSAL = "order_reversal"
    SUPPLIER_BILL = "supplier_bill"
    SUPPLIER_PAYMENT = "supplier_payment"
    COD_COLLECTION = "cod_collection"
    COD_DEPOSIT = "cod_deposit"
    REFUND_ACCRUAL = "refund_accrual"
    REFUND_PAYMENT = "refund_payment"
    OPENING_BALANCE = "opening_balance"
    ADJUSTMENT = "adjustment"
    GATEWAY_RECONCILIATION = "gateway_reconciliation"


class PeriodStatus(StrEnum):
    OPEN = "open"
    LOCKED = "locked"  # daily/period close has been performed; no further posts


class SupplierBillStatus(StrEnum):
    OPEN = "open"
    PARTIALLY_PAID = "partially_paid"
    PAID = "paid"
    VOID = "void"


class CodDepositStatus(StrEnum):
    PENDING = "pending"
    DEPOSITED = "deposited"
    DISCREPANCY = "discrepancy"


class RefundStatus(StrEnum):
    PENDING = "pending"
    PAID = "paid"
    CANCELLED = "cancelled"


class FinanceError(Exception):
    """Module-level base. Service layer wraps in BusinessRuleError for the API."""
