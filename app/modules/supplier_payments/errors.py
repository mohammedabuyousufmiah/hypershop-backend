"""Supplier-payment exceptions, mapped via the global handler."""

from __future__ import annotations

from app.core.errors import (
    BusinessRuleError,
    ConflictError,
    DomainError,
    NotFoundError,
    ValidationError,
)


class BillNotFoundError(NotFoundError):
    code = "supplier_payments.bill_not_found"


class BillApprovalStateNotFoundError(NotFoundError):
    code = "supplier_payments.bill_approval_state_not_found"


class ApprovalNotFoundError(NotFoundError):
    code = "supplier_payments.approval_not_found"


class BankAccountNotFoundError(NotFoundError):
    code = "supplier_payments.bank_account_not_found"


class WorkflowNotFoundError(NotFoundError):
    code = "supplier_payments.workflow_not_found"


class IllegalApprovalTransitionError(DomainError):
    code = "supplier_payments.illegal_approval_transition"
    status_code = 409


class SeparationOfDutiesError(BusinessRuleError):
    """Raised when the same user tries to approve two different levels
    on the same bill. Hard policy — one user, one level only.
    """
    code = "supplier_payments.separation_of_duties"


class WrongApprovalLevelError(BusinessRuleError):
    """Raised when a level-N approval is attempted but the bill is
    waiting on a different level.
    """
    code = "supplier_payments.wrong_approval_level"


class DuplicateInvoiceError(ConflictError):
    """Same supplier + invoice_number already exists."""
    code = "supplier_payments.duplicate_invoice"


class SupplierBankAccountUnverifiedError(BusinessRuleError):
    """Cannot release payment to an unverified supplier bank account."""
    code = "supplier_payments.bank_account_unverified"


class BillNotApprovedForPaymentError(BusinessRuleError):
    """Cannot execute payment on a bill that hasn't reached
    ``ready_for_payment``.
    """
    code = "supplier_payments.bill_not_approved_for_payment"


class BillOnHoldError(BusinessRuleError):
    code = "supplier_payments.bill_on_hold"


class WorkflowConfigInvalidError(ValidationError):
    code = "supplier_payments.workflow_config_invalid"
