"""Canonical chart of accounts.

The seeder reads this list and idempotently inserts each row. Codes are
fixed (used as cross-module foreign references) — never renumber. New
accounts get appended; deprecated ones flip ``is_active=False`` instead
of being deleted (existing journal lines reference them).

Code grouping convention:
- 1xxx assets
- 2xxx liabilities
- 3xxx equity
- 4xxx revenue
- 5xxx expenses (cost of sales)
- 6xxx expenses (operating)
"""

from __future__ import annotations

from dataclasses import dataclass

from app.modules.finance.state import AccountType


@dataclass(frozen=True, slots=True)
class AccountSpec:
    code: str
    name: str
    type: AccountType
    description: str
    is_system: bool = True


# Account codes referenced from posting code — keep in sync with the rows below.
A_CASH_IN_BANK = "1010"
A_CASH_ON_HAND_RIDER = "1020"
A_AR_COD = "1100"
A_AR_GATEWAY_PENDING = "1110"
A_INVENTORY = "1300"

L_AP_SUPPLIERS = "2010"
L_VAT_PAYABLE = "2100"
L_REFUND_PAYABLE = "2200"

E_OPENING_RETAINED = "3010"

R_PRODUCT_SALES = "4010"
R_SHIPPING_REVENUE = "4020"
R_SALES_RETURNS = "4910"  # contra-revenue (debit-balanced)
R_SALES_DISCOUNTS = "4920"  # contra-revenue

C_COGS = "5010"
C_INVENTORY_LOSS = "5020"  # writedowns / damages

X_COD_SHORT = "6010"  # COD discrepancy expense (when rider deposits less)
X_GATEWAY_DRIFT = "6020"  # Gateway settlement drift adjustment (debit on shortfall, credit on overpayment)


CHART_OF_ACCOUNTS: tuple[AccountSpec, ...] = (
    # Assets
    AccountSpec(A_CASH_IN_BANK, "Cash in Bank", AccountType.ASSET,
                "Settled bank funds; rider deposits and gateway settlements land here."),
    AccountSpec(A_CASH_ON_HAND_RIDER, "Cash on Hand — Riders", AccountType.ASSET,
                "COD cash collected by riders before deposit. Cleared by cod_deposit."),
    AccountSpec(A_AR_COD, "Accounts Receivable — COD Customers", AccountType.ASSET,
                "Owed by COD customers from order placement until rider collection."),
    AccountSpec(A_AR_GATEWAY_PENDING, "Accounts Receivable — Gateway Pending",
                AccountType.ASSET,
                "Authorized online payments awaiting gateway settlement to bank."),
    AccountSpec(A_INVENTORY, "Inventory", AccountType.ASSET,
                "Asset value of stock on hand. Increased on goods receipt; reduced on COGS."),
    # Liabilities
    AccountSpec(L_AP_SUPPLIERS, "Accounts Payable — Suppliers", AccountType.LIABILITY,
                "Owed to suppliers from goods receipt until settlement."),
    AccountSpec(L_VAT_PAYABLE, "VAT Payable", AccountType.LIABILITY,
                "Output VAT collected from customers, owed to tax authority."),
    AccountSpec(L_REFUND_PAYABLE, "Refunds Payable", AccountType.LIABILITY,
                "Refund obligations accrued from completed returns until paid."),
    # Equity
    AccountSpec(E_OPENING_RETAINED, "Opening Balance Equity", AccountType.EQUITY,
                "Plug account for one-time opening balances; should net to zero."),
    # Revenue
    AccountSpec(R_PRODUCT_SALES, "Product Sales Revenue", AccountType.REVENUE,
                "Net revenue from sale of medicines and other products (ex-VAT)."),
    AccountSpec(R_SHIPPING_REVENUE, "Shipping Revenue", AccountType.REVENUE,
                "Delivery fees billed to the customer."),
    AccountSpec(R_SALES_RETURNS, "Sales Returns", AccountType.REVENUE,
                "Contra-revenue. Debit-balanced; reduces gross sales on the P&L."),
    AccountSpec(R_SALES_DISCOUNTS, "Sales Discounts", AccountType.REVENUE,
                "Contra-revenue for promotional discounts at checkout."),
    # Expenses
    AccountSpec(C_COGS, "Cost of Goods Sold", AccountType.EXPENSE,
                "Inventory value of products delivered to customers."),
    AccountSpec(C_INVENTORY_LOSS, "Inventory Loss / Writedown", AccountType.EXPENSE,
                "Damages, expirations, and adjustments that reduce inventory value."),
    AccountSpec(X_COD_SHORT, "COD Cash Short / Over", AccountType.EXPENSE,
                "Difference between COD expected and actually deposited by rider."),
    AccountSpec(X_GATEWAY_DRIFT, "Gateway Settlement Drift", AccountType.EXPENSE,
                "Adjustment for net delta between gateway-reported settlement and "
                "matched intent amounts. Debit on shortfall, credit on overpayment."),
)


def by_code(code: str) -> AccountSpec:
    for spec in CHART_OF_ACCOUNTS:
        if spec.code == code:
            return spec
    raise KeyError(f"Unknown chart-of-accounts code: {code}")
