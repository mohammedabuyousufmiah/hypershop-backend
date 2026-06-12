"""Rider wallet exceptions, mapped to HTTP via the global handler."""

from __future__ import annotations

from app.core.errors import (
    BusinessRuleError,
    ConflictError,
    NotFoundError,
    ValidationError,
)


class WalletNotFoundError(NotFoundError):
    code = "rider_wallet.wallet_not_found"


class SettlementNotFoundError(NotFoundError):
    code = "rider_wallet.settlement_not_found"


class DuplicateTransactionIdError(ConflictError):
    """Raised when the same MFS txn_id is submitted twice."""
    code = "rider_wallet.duplicate_transaction_id"


class WalletFrozenError(BusinessRuleError):
    """Wallet is admin-frozen — no settlements/assignments allowed."""
    code = "rider_wallet.wallet_frozen"


class WalletAssignmentLockedError(BusinessRuleError):
    """Previous-day unpaid balance blocks today's assignment.

    Surfaced from ``AssignmentEligibilityService.check_assignment_eligibility``
    when called from Module 31's start_shift / create_run_sheet hooks.
    """
    code = "rider_wallet.assignment_locked"


class SettlementNotAdjustableError(BusinessRuleError):
    """Settlement is in a terminal state (verified/rejected/adjusted)."""
    code = "rider_wallet.settlement_not_adjustable"


class InvalidSettlementAmountError(ValidationError):
    code = "rider_wallet.invalid_settlement_amount"


class CarryForwardExpiredError(BusinessRuleError):
    code = "rider_wallet.carry_forward_expired"


class CarryForwardLimitExceededError(BusinessRuleError):
    code = "rider_wallet.carry_forward_limit_exceeded"
