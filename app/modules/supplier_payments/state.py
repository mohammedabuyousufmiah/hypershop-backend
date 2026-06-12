"""Enums + state-machine helpers for the supplier-payment approval engine.

Module 33 sits ON TOP of the existing finance.SupplierBill /
finance.SupplierPayment tables (Module 15). Those continue to be the
source of truth for *financial* state (open / partially_paid / paid).

What this module adds is the **governance layer**:
  - approval_status     — independent of fin status; tracks which of
                          the 3 (or 4) approval levels has been cleared
  - recommendation_*    — score + suggested date/amount/method
  - dispute / dup flags — bills that warrant ops attention
  - bank_account verify — supplier must have a verified bank/MFS row
                          before a payment can be released

Bills can sit at fin status='open' AND approval_status='draft' for a
long time (the financial booking happens immediately on bill receipt;
approval gating happens before payment).
"""

from __future__ import annotations

from enum import StrEnum


# ----------------------------------------------------------------------
# Bill approval lifecycle
# ----------------------------------------------------------------------
class BillApprovalStatus(StrEnum):
    """Where the bill sits in the approval workflow.

    Not the same as the *financial* status (open/partially_paid/paid).
    A bill can be financially 'open' for weeks while it cycles through
    approval gates.
    """

    DRAFT = "draft"                      # being prepared by procurement
    SUBMITTED = "submitted"              # ready for level-1 review
    LEVEL_1_VERIFIED = "level_1_verified"
    LEVEL_2_APPROVED = "level_2_approved"
    LEVEL_3_APPROVED = "level_3_approved"
    SUPER_ADMIN_REQUIRED = "super_admin_required"  # threshold met
    APPROVED_FINAL = "approved_final"    # all gates cleared
    READY_FOR_PAYMENT = "ready_for_payment"
    PAID = "paid"                        # payment executed (proof on row)
    RECONCILED = "reconciled"            # finance-verified payment
    RETURNED_FOR_CORRECTION = "returned_for_correction"
    REJECTED = "rejected"                # terminal — bill won't be paid
    ON_HOLD = "on_hold"


ALL_APPROVAL_STATUSES: frozenset[str] = frozenset(
    s.value for s in BillApprovalStatus
)


APPROVAL_TERMINAL_STATUSES: frozenset[str] = frozenset({
    BillApprovalStatus.RECONCILED.value,
    BillApprovalStatus.REJECTED.value,
})

# Statuses where mutating fields (amount / supplier / dates) is blocked.
APPROVAL_LOCKED_STATUSES: frozenset[str] = frozenset({
    BillApprovalStatus.LEVEL_1_VERIFIED.value,
    BillApprovalStatus.LEVEL_2_APPROVED.value,
    BillApprovalStatus.LEVEL_3_APPROVED.value,
    BillApprovalStatus.SUPER_ADMIN_REQUIRED.value,
    BillApprovalStatus.APPROVED_FINAL.value,
    BillApprovalStatus.READY_FOR_PAYMENT.value,
    BillApprovalStatus.PAID.value,
    BillApprovalStatus.RECONCILED.value,
    BillApprovalStatus.REJECTED.value,
})


# Which approval-level number we're WAITING on, given the current status.
# Used by the API to filter the approval queue per role.
NEXT_APPROVAL_LEVEL: dict[str, int | None] = {
    BillApprovalStatus.SUBMITTED.value: 1,
    BillApprovalStatus.LEVEL_1_VERIFIED.value: 2,
    BillApprovalStatus.LEVEL_2_APPROVED.value: 3,
    BillApprovalStatus.LEVEL_3_APPROVED.value: None,  # may need lvl 4
    BillApprovalStatus.SUPER_ADMIN_REQUIRED.value: 4,
}


# ----------------------------------------------------------------------
# Per-row approval decisions
# ----------------------------------------------------------------------
class ApprovalDecision(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    RETURNED_FOR_CORRECTION = "returned_for_correction"


ALL_APPROVAL_DECISIONS: frozenset[str] = frozenset(
    d.value for d in ApprovalDecision
)


class ApprovalLevel:
    """Numeric levels — kept as plain ints to avoid enum overhead in
    the (level × bill) unique constraint.
    """

    LEVEL_1 = 1   # accounts / AP verification
    LEVEL_2 = 2   # procurement / ops / warehouse approval
    LEVEL_3 = 3   # finance final approval
    LEVEL_4 = 4   # super-admin / executive (only if threshold met)

    ALL: tuple[int, ...] = (LEVEL_1, LEVEL_2, LEVEL_3, LEVEL_4)


# ----------------------------------------------------------------------
# Payment priority (drives the recommendation engine ordering)
# ----------------------------------------------------------------------
class PaymentPriority(StrEnum):
    CRITICAL = "critical"       # overdue + critical supplier; pay today
    HIGH = "high"               # near-due or important supplier
    NORMAL = "normal"
    LOW = "low"                 # not urgent
    ON_HOLD = "on_hold"         # disputed / under review — do not pay


PRIORITY_RANK: dict[str, int] = {
    PaymentPriority.CRITICAL.value: 0,
    PaymentPriority.HIGH.value: 1,
    PaymentPriority.NORMAL.value: 2,
    PaymentPriority.LOW.value: 3,
    PaymentPriority.ON_HOLD.value: 4,
}


# ----------------------------------------------------------------------
# Payment-side enums (verification on the existing fin_supplier_payments)
# ----------------------------------------------------------------------
class PaymentVerificationStatus(StrEnum):
    """Layer on top of fin_supplier_payments — what's the verification
    state of this payment?
    """

    PENDING = "pending"           # paid out; awaiting upload of proof
    PROOF_UPLOADED = "proof_uploaded"
    VERIFIED = "verified"         # finance saw proof; matches bank
    RECONCILED = "reconciled"     # bank statement matched
    DISPUTED = "disputed"


ALL_PAYMENT_VERIFICATION_STATUSES: frozenset[str] = frozenset(
    s.value for s in PaymentVerificationStatus
)


# ----------------------------------------------------------------------
# Bank-account types for supplier payouts
# ----------------------------------------------------------------------
class SupplierBankAccountType(StrEnum):
    BANK = "bank"     # traditional bank transfer (account_number + bank_name)
    MFS = "mfs"       # bKash / Nagad / Rocket
    CASH = "cash"     # in-person cash hand-off (no account, but tracked)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
class TransitionError(Exception):
    def __init__(self, *, frm: str, to: str) -> None:
        self.frm = frm
        self.to = to
        super().__init__(
            f"Invalid bill-approval transition: {frm} → {to}",
        )
