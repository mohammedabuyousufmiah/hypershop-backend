"""Finance Manager operational action catalog.

Sourced from the Finance Manager Rules package (2026-05-26). Each
verb represents a single operator workflow that the Finance Manager
admin dashboard exposes. The service layer (``operations_service``)
maps every verb to:

  * one perm string from ``role_extensions.py`` (RBAC gate)
  * one entry in ``ROLE_BLOCKED_ACTIONS`` from
    ``authority_matrix.py`` (matrix red-line)
  * a set of required payload fields (reason / evidence / reference /
    amount) enforced before the audit log row is written.

Keeping the enum + classifier dicts here (rather than in the service
file) means the admin UI can mirror the same validation rules without
re-importing the SQLAlchemy session.
"""

from __future__ import annotations

from enum import StrEnum


class FinanceAction(StrEnum):
    """All distinct Finance Manager action verbs.

    Naming is UPPER_SNAKE_CASE so it surfaces cleanly in audit log
    rows + permission-denied messages.
    """

    # ----- Read / dashboard -----
    VIEW_FINANCE_DASHBOARD = "VIEW_FINANCE_DASHBOARD"
    VIEW_MONEY_REPORTS = "VIEW_MONEY_REPORTS"
    VIEW_ALL_PAYMENT_STATUS = "VIEW_ALL_PAYMENT_STATUS"
    VIEW_COD_SETTLEMENT_REPORT = "VIEW_COD_SETTLEMENT_REPORT"
    VIEW_OPERATIONS_FINANCE = "VIEW_OPERATIONS_FINANCE"
    VIEW_FULFILLMENT_FINANCE = "VIEW_FULFILLMENT_FINANCE"
    VIEW_SELLER_FINANCE = "VIEW_SELLER_FINANCE"

    # ----- Reconciliation -----
    RECONCILE_BKASH_NAGAD_CARD_TRANSACTION = "RECONCILE_BKASH_NAGAD_CARD_TRANSACTION"
    VERIFY_PAYMENT_GATEWAY_RECONCILIATION = "VERIFY_PAYMENT_GATEWAY_RECONCILIATION"
    RECONCILE_DELIVERY_FEE = "RECONCILE_DELIVERY_FEE"
    RECONCILE_COD_MISMATCH = "RECONCILE_COD_MISMATCH"
    RECONCILE_RIDER_COD_LIABILITY = "RECONCILE_RIDER_COD_LIABILITY"
    RECONCILE_SELLER_STATEMENT = "RECONCILE_SELLER_STATEMENT"

    # ----- Refund / compensation -----
    REVIEW_REFUND_ESCALATION = "REVIEW_REFUND_ESCALATION"
    APPROVE_REFUND = "APPROVE_REFUND"
    REJECT_REFUND = "REJECT_REFUND"
    APPROVE_CUSTOMER_COMPENSATION = "APPROVE_CUSTOMER_COMPENSATION"

    # ----- COD settlement -----
    SETTLE_COD = "SETTLE_COD"
    VERIFY_RIDER_COD_SETTLEMENT = "VERIFY_RIDER_COD_SETTLEMENT"

    # ----- Payouts (seller + rider) -----
    APPROVE_SELLER_PAYOUT_BATCH = "APPROVE_SELLER_PAYOUT_BATCH"
    HOLD_SELLER_PAYOUT = "HOLD_SELLER_PAYOUT"
    RELEASE_SELLER_PAYOUT = "RELEASE_SELLER_PAYOUT"
    RELEASE_SELLER_PAYOUT_HOLD = "RELEASE_SELLER_PAYOUT_HOLD"
    APPROVE_SELLER_CHARGEBACK = "APPROVE_SELLER_CHARGEBACK"
    APPROVE_RIDER_PAYOUT_BATCH = "APPROVE_RIDER_PAYOUT_BATCH"
    HOLD_RIDER_PAYOUT_BATCH = "HOLD_RIDER_PAYOUT_BATCH"
    RELEASE_RIDER_PAYOUT = "RELEASE_RIDER_PAYOUT"
    VERIFY_RIDER_PAYOUT = "VERIFY_RIDER_PAYOUT"
    FREEZE_PAYOUT = "FREEZE_PAYOUT"

    # ----- Wallet adjustments -----
    APPROVE_WALLET_ADJUSTMENT = "APPROVE_WALLET_ADJUSTMENT"
    INVESTIGATE_WALLET_MISMATCH = "INVESTIGATE_WALLET_MISMATCH"
    VERIFY_WALLET_LEDGER = "VERIFY_WALLET_LEDGER"

    # ----- Adjustments / dispute / escalation -----
    CREATE_FINANCE_ADJUSTMENT_REQUEST = "CREATE_FINANCE_ADJUSTMENT_REQUEST"
    INVESTIGATE_FINANCE_DISPUTE = "INVESTIGATE_FINANCE_DISPUTE"
    ESCALATE_SUSPICIOUS_TRANSACTION = "ESCALATE_SUSPICIOUS_TRANSACTION"
    ESCALATE_PAYMENT_DISPUTE = "ESCALATE_PAYMENT_DISPUTE"

    # ----- Daily closing / reports / audit -----
    GENERATE_DAILY_FINANCE_REPORT = "GENERATE_DAILY_FINANCE_REPORT"
    SUBMIT_DAILY_FINANCE_CLOSING_REPORT = "SUBMIT_DAILY_FINANCE_CLOSING_REPORT"
    EXPORT_FINANCE_REPORT = "EXPORT_FINANCE_REPORT"
    EXPORT_FINANCE_AUDIT = "EXPORT_FINANCE_AUDIT"
    ADD_FINANCE_RESOLUTION_NOTE = "ADD_FINANCE_RESOLUTION_NOTE"


# Read-only actions skip the reason/evidence/reference gates entirely.
VIEW_ACTIONS: frozenset[FinanceAction] = frozenset(
    {
        FinanceAction.VIEW_FINANCE_DASHBOARD,
        FinanceAction.VIEW_MONEY_REPORTS,
        FinanceAction.VIEW_ALL_PAYMENT_STATUS,
        FinanceAction.VIEW_COD_SETTLEMENT_REPORT,
        FinanceAction.VIEW_OPERATIONS_FINANCE,
        FinanceAction.VIEW_FULFILLMENT_FINANCE,
        FinanceAction.VIEW_SELLER_FINANCE,
    }
)

# Money-movement actions — must carry a positive BDT amount and a
# reference ID (settlement ref / payment ref / order ref).
MONEY_MOVEMENT_ACTIONS: frozenset[FinanceAction] = frozenset(
    {
        FinanceAction.APPROVE_REFUND,
        FinanceAction.RECONCILE_DELIVERY_FEE,
        FinanceAction.APPROVE_CUSTOMER_COMPENSATION,
        FinanceAction.RECONCILE_BKASH_NAGAD_CARD_TRANSACTION,
        FinanceAction.VERIFY_PAYMENT_GATEWAY_RECONCILIATION,
        FinanceAction.SETTLE_COD,
        FinanceAction.VERIFY_RIDER_COD_SETTLEMENT,
        FinanceAction.RECONCILE_COD_MISMATCH,
        FinanceAction.RECONCILE_RIDER_COD_LIABILITY,
        FinanceAction.RECONCILE_SELLER_STATEMENT,
        FinanceAction.APPROVE_SELLER_PAYOUT_BATCH,
        FinanceAction.RELEASE_SELLER_PAYOUT,
        FinanceAction.RELEASE_SELLER_PAYOUT_HOLD,
        FinanceAction.APPROVE_SELLER_CHARGEBACK,
        FinanceAction.APPROVE_RIDER_PAYOUT_BATCH,
        FinanceAction.RELEASE_RIDER_PAYOUT,
        FinanceAction.VERIFY_RIDER_PAYOUT,
        FinanceAction.APPROVE_WALLET_ADJUSTMENT,
        FinanceAction.CREATE_FINANCE_ADJUSTMENT_REQUEST,
        FinanceAction.VERIFY_WALLET_LEDGER,
    }
)

# Actions that always require an evidence URL (decision artefact).
EVIDENCE_REQUIRED_ACTIONS: frozenset[FinanceAction] = MONEY_MOVEMENT_ACTIONS | {
    FinanceAction.FREEZE_PAYOUT,
    FinanceAction.HOLD_SELLER_PAYOUT,
    FinanceAction.HOLD_RIDER_PAYOUT_BATCH,
    FinanceAction.EXPORT_FINANCE_REPORT,
    FinanceAction.EXPORT_FINANCE_AUDIT,
}

# Actions that always require a reference_id (settlement / order /
# audit ref).
REFERENCE_REQUIRED_ACTIONS: frozenset[FinanceAction] = MONEY_MOVEMENT_ACTIONS | {
    FinanceAction.REVIEW_REFUND_ESCALATION,
    FinanceAction.REJECT_REFUND,
    FinanceAction.HOLD_SELLER_PAYOUT,
    FinanceAction.HOLD_RIDER_PAYOUT_BATCH,
    FinanceAction.FREEZE_PAYOUT,
    FinanceAction.INVESTIGATE_WALLET_MISMATCH,
    FinanceAction.INVESTIGATE_FINANCE_DISPUTE,
    FinanceAction.ESCALATE_SUSPICIOUS_TRANSACTION,
    FinanceAction.ESCALATE_PAYMENT_DISPUTE,
    FinanceAction.GENERATE_DAILY_FINANCE_REPORT,
    FinanceAction.SUBMIT_DAILY_FINANCE_CLOSING_REPORT,
}

# Actions where requester ≠ approver must be enforced — Finance
# Manager cannot self-approve their own request.
NO_SELF_APPROVE_ACTIONS: frozenset[FinanceAction] = frozenset(
    {
        FinanceAction.APPROVE_REFUND,
        FinanceAction.APPROVE_CUSTOMER_COMPENSATION,
        FinanceAction.SETTLE_COD,
        FinanceAction.APPROVE_SELLER_PAYOUT_BATCH,
        FinanceAction.RELEASE_SELLER_PAYOUT,
        FinanceAction.RELEASE_SELLER_PAYOUT_HOLD,
        FinanceAction.APPROVE_SELLER_CHARGEBACK,
        FinanceAction.APPROVE_RIDER_PAYOUT_BATCH,
        FinanceAction.HOLD_RIDER_PAYOUT_BATCH,
        FinanceAction.RELEASE_RIDER_PAYOUT,
        FinanceAction.APPROVE_WALLET_ADJUSTMENT,
    }
)
