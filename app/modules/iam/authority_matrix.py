"""Role authority matrix — runtime enforcement of red lines.

Sourced from the 4 role-rule packages (2026-05-26). See
``docs/AUTHORITY_MATRIX.md`` for the human-readable spec.

This file is the single point a service calls before executing any
sensitive action; any role-action combination that is *blocked* below
will raise ``AuthorityViolation`` at the call site. The matrix is
INTENTIONALLY redundant with permission gates — even if a router
permission check is mis-wired, this matrix is the last fence keeping
a Finance Manager from changing stock or an Admin from faking a
payment success.

Usage::

    from app.modules.iam.authority_matrix import (
        ensure_action_allowed, AuthorityAction,
    )

    ensure_action_allowed(
        role_name=principal.primary_role,
        action=AuthorityAction.MARK_PAYMENT_SUCCESS,
    )
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class AuthorityAction(str, Enum):
    """Canonical action verbs that are HARD-BLOCKED for specific roles.

    Wire each verb to the highest-risk action it represents. The names
    are role-agnostic — the matrix below decides who is blocked.
    """

    # System-only — only the payment gateway webhook may mark payment.
    MARK_PAYMENT_SUCCESS = "MARK_PAYMENT_SUCCESS"
    CONFIRM_ORDER_MANUALLY = "CONFIRM_ORDER_MANUALLY"
    MANUALLY_CONFIRM_PAID_ORDER = "MANUALLY_CONFIRM_PAID_ORDER"
    EDIT_PAYMENT_STATUS = "EDIT_PAYMENT_STATUS"
    BYPASS_PAYMENT_WEBHOOK = "BYPASS_PAYMENT_WEBHOOK"

    # Money actions — Finance Manager owns these; everyone else blocked.
    APPROVE_REFUND = "APPROVE_REFUND"
    RELEASE_PAYOUT = "RELEASE_PAYOUT"
    RELEASE_SELLER_PAYOUT = "RELEASE_SELLER_PAYOUT"
    RELEASE_RIDER_PAYOUT = "RELEASE_RIDER_PAYOUT"
    SETTLE_COD = "SETTLE_COD"
    ADJUST_COD = "ADJUST_COD"
    MARK_AMOUNT_RECEIVED = "MARK_AMOUNT_RECEIVED"

    # Wallet — Finance-only with audit + double-control.
    EDIT_WALLET = "EDIT_WALLET"
    EDIT_CUSTOMER_WALLET = "EDIT_CUSTOMER_WALLET"
    EDIT_RIDER_WALLET = "EDIT_RIDER_WALLET"
    EDIT_SELLER_BALANCE = "EDIT_SELLER_BALANCE"

    # System settings — Admin / Super Admin only.
    CHANGE_COMMISSION = "CHANGE_COMMISSION"
    CHANGE_SYSTEM_SETTINGS = "CHANGE_SYSTEM_SETTINGS"
    DISABLE_SECURITY_RULES = "DISABLE_SECURITY_RULES"
    CHANGE_PRODUCT_PRICE = "CHANGE_PRODUCT_PRICE"

    # History-destructive — nobody, including Super Admin, may run.
    DELETE_AUDIT_LOG = "DELETE_AUDIT_LOG"
    DELETE_ORDER = "DELETE_ORDER"
    DELETE_PAYMENT_RECORD = "DELETE_PAYMENT_RECORD"
    DELETE_COD_SETTLEMENT_RECORD = "DELETE_COD_SETTLEMENT_RECORD"
    DELETE_WALLET_LEDGER = "DELETE_WALLET_LEDGER"
    DELETE_COMPLAINT = "DELETE_COMPLAINT"
    HIDE_COMPLAINT = "HIDE_COMPLAINT"

    # Stock truth — Inventory Manager owns, Finance is blocked.
    CHANGE_STOCK_STATUS = "CHANGE_STOCK_STATUS"
    RETURN_TO_STOCK_DECISION = "RETURN_TO_STOCK_DECISION"

    # Delivery exception — Operations Manager owns; Finance is blocked.
    APPROVE_DELIVERY_EXCEPTION = "APPROVE_DELIVERY_EXCEPTION"
    RIDER_REASSIGNMENT = "RIDER_REASSIGNMENT"

    # Last-mile state actions — Rider-only finality.
    START_OUT_FOR_DELIVERY = "START_OUT_FOR_DELIVERY"
    CONFIRM_DELIVERED = "CONFIRM_DELIVERED"

    # Manager-only approvals.
    APPROVE_OPERATIONAL_EXCEPTION = "APPROVE_OPERATIONAL_EXCEPTION"
    REJECT_OPERATIONAL_EXCEPTION = "REJECT_OPERATIONAL_EXCEPTION"
    APPROVE_MANAGER_APPROVAL = "APPROVE_MANAGER_APPROVAL"
    APPROVE_SUPERVISOR_ESCALATION = "APPROVE_SUPERVISOR_ESCALATION"
    APPROVE_ORDER_HOLD = "APPROVE_ORDER_HOLD"
    RELEASE_MANUAL_HOLD = "RELEASE_MANUAL_HOLD"
    APPROVE_FAILED_DELIVERY_DISPUTE_DECISION = "APPROVE_FAILED_DELIVERY_DISPUTE_DECISION"
    REVIEW_SHIFT_REPORT = "REVIEW_SHIFT_REPORT"


# Action groups — kept as plain tuples so they can be reused inside the
# per-role blocked sets without re-typing each verb.
_SYSTEM_ONLY = (
    AuthorityAction.MARK_PAYMENT_SUCCESS,
    AuthorityAction.CONFIRM_ORDER_MANUALLY,
    AuthorityAction.MANUALLY_CONFIRM_PAID_ORDER,
    AuthorityAction.EDIT_PAYMENT_STATUS,
    AuthorityAction.BYPASS_PAYMENT_WEBHOOK,
)
_MONEY = (
    AuthorityAction.APPROVE_REFUND,
    AuthorityAction.RELEASE_PAYOUT,
    AuthorityAction.RELEASE_SELLER_PAYOUT,
    AuthorityAction.RELEASE_RIDER_PAYOUT,
    AuthorityAction.SETTLE_COD,
    AuthorityAction.ADJUST_COD,
    AuthorityAction.MARK_AMOUNT_RECEIVED,
)
_WALLET = (
    AuthorityAction.EDIT_WALLET,
    AuthorityAction.EDIT_CUSTOMER_WALLET,
    AuthorityAction.EDIT_RIDER_WALLET,
    AuthorityAction.EDIT_SELLER_BALANCE,
)
_SYSTEM_SETTING = (
    AuthorityAction.CHANGE_COMMISSION,
    AuthorityAction.CHANGE_SYSTEM_SETTINGS,
    AuthorityAction.DISABLE_SECURITY_RULES,
    AuthorityAction.CHANGE_PRODUCT_PRICE,
)
_HISTORY_DESTRUCTIVE = (
    AuthorityAction.DELETE_AUDIT_LOG,
    AuthorityAction.DELETE_ORDER,
    AuthorityAction.DELETE_PAYMENT_RECORD,
    AuthorityAction.DELETE_COD_SETTLEMENT_RECORD,
    AuthorityAction.DELETE_WALLET_LEDGER,
    AuthorityAction.DELETE_COMPLAINT,
    AuthorityAction.HIDE_COMPLAINT,
)
_STOCK = (
    AuthorityAction.CHANGE_STOCK_STATUS,
    AuthorityAction.RETURN_TO_STOCK_DECISION,
)
_DELIVERY_EXCEPTION = (
    AuthorityAction.APPROVE_DELIVERY_EXCEPTION,
    AuthorityAction.RIDER_REASSIGNMENT,
)
_RIDER_ONLY = (
    AuthorityAction.START_OUT_FOR_DELIVERY,
    AuthorityAction.CONFIRM_DELIVERED,
)
_MANAGER_ONLY = (
    AuthorityAction.APPROVE_OPERATIONAL_EXCEPTION,
    AuthorityAction.REJECT_OPERATIONAL_EXCEPTION,
    AuthorityAction.APPROVE_MANAGER_APPROVAL,
    AuthorityAction.APPROVE_SUPERVISOR_ESCALATION,
    AuthorityAction.APPROVE_ORDER_HOLD,
    AuthorityAction.RELEASE_MANUAL_HOLD,
    AuthorityAction.APPROVE_FAILED_DELIVERY_DISPUTE_DECISION,
    AuthorityAction.REVIEW_SHIFT_REPORT,
)


# Per-role HARD-BLOCKED action set. A role NOT listed here has no
# matrix-level restrictions — only permission-level gates apply.
# Note: a role being absent from the matrix does NOT grant it the
# action; permission checks still run independently.
ROLE_BLOCKED_ACTIONS: dict[str, frozenset[AuthorityAction]] = {
    "finance_manager": frozenset(
        (
            *_SYSTEM_ONLY,                  # cannot fake-confirm payment
            *_STOCK,                         # not stock truth
            *_DELIVERY_EXCEPTION,            # not delivery truth
            *_SYSTEM_SETTING,                # cannot change commission / price / settings
            *_HISTORY_DESTRUCTIVE,           # never delete audit / order / wallet rows
        )
    ),
    "inventory_manager": frozenset(
        (
            *_SYSTEM_ONLY,
            *_MONEY,                         # cannot touch money
            *_WALLET,
            *_DELIVERY_EXCEPTION,
            *_SYSTEM_SETTING,
            *_HISTORY_DESTRUCTIVE,
        )
    ),
    "operations_manager_lm": frozenset(
        (
            *_SYSTEM_ONLY,
            *_MONEY,
            *_WALLET,
            *_SYSTEM_SETTING,
            *_HISTORY_DESTRUCTIVE,
            *_RIDER_ONLY,                    # cannot start OFD / confirm delivered
            *_STOCK,                         # cannot change stock truth
        )
    ),
    "supervisor": frozenset(
        (
            *_SYSTEM_ONLY,
            *_MONEY,
            *_WALLET,
            *_SYSTEM_SETTING,
            *_HISTORY_DESTRUCTIVE,
            *_RIDER_ONLY,
            *_STOCK,
            *_DELIVERY_EXCEPTION,            # may flag/escalate only
            *_MANAGER_ONLY,                  # manager-tier decisions blocked
        )
    ),
    "fulfillment_supervisor": frozenset(
        (
            *_SYSTEM_ONLY,
            *_MONEY,
            *_WALLET,
            *_SYSTEM_SETTING,
            *_HISTORY_DESTRUCTIVE,
            *_RIDER_ONLY,
            *_STOCK,
            *_DELIVERY_EXCEPTION,
            *_MANAGER_ONLY,
        )
    ),
    "warehouse_receiver": frozenset(
        (
            *_SYSTEM_ONLY,
            *_MONEY,
            *_WALLET,
            *_SYSTEM_SETTING,
            *_HISTORY_DESTRUCTIVE,
            *_DELIVERY_EXCEPTION,
            *_RIDER_ONLY,
            *_MANAGER_ONLY,
        )
    ),
    "warehouse_packer": frozenset(
        (
            *_SYSTEM_ONLY,
            *_MONEY,
            *_WALLET,
            *_SYSTEM_SETTING,
            *_HISTORY_DESTRUCTIVE,
            *_DELIVERY_EXCEPTION,
            *_RIDER_ONLY,
            *_MANAGER_ONLY,
            *_STOCK,
        )
    ),
    # Admin: blocked from system-only + wallet + audit-delete.
    "admin": frozenset(
        (
            *_SYSTEM_ONLY,
            *_WALLET,
            AuthorityAction.DELETE_AUDIT_LOG,
        )
    ),
    # Super Admin: blocked from system-only + audit-delete.
    "super_admin": frozenset(
        (
            *_SYSTEM_ONLY,
            AuthorityAction.DELETE_AUDIT_LOG,
        )
    ),
}


@dataclass(frozen=True, slots=True)
class AuthorityViolation(Exception):
    """Raised when a role attempts an action the matrix forbids.

    Carrying the role + action makes the exception message stable
    enough to grep in logs and feed an audit dashboard.
    """

    role: str
    action: AuthorityAction

    def __str__(self) -> str:
        return (
            f"AuthorityViolation: role={self.role!r} is hard-blocked "
            f"from action={self.action.value!r} (see "
            f"docs/AUTHORITY_MATRIX.md)"
        )


def is_action_blocked(*, role_name: str, action: AuthorityAction) -> bool:
    """Pure-function check — True if the role cannot perform this action."""
    return action in ROLE_BLOCKED_ACTIONS.get(role_name, frozenset())


def ensure_action_allowed(*, role_name: str, action: AuthorityAction) -> None:
    """Raise ``AuthorityViolation`` when the role is blocked.

    Call this at the start of every router handler that mutates money,
    stock, or audit state. Pair with the standard permission gate —
    the matrix is the *redundant* fence, not the primary one.
    """
    if is_action_blocked(role_name=role_name, action=action):
        raise AuthorityViolation(role=role_name, action=action)


# Convenience predicate for tests / admin dashboards.
def blocked_actions_for(role_name: str) -> tuple[AuthorityAction, ...]:
    return tuple(sorted(ROLE_BLOCKED_ACTIONS.get(role_name, frozenset()),
                        key=lambda a: a.value))
