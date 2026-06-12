"""Audit action codes for the supplier-payment approval module."""

from __future__ import annotations

# ----- Bill workflow -----
ACTION_BILL_SUBMITTED = "supplier_payments.bill_submitted"
ACTION_BILL_REJECTED = "supplier_payments.bill_rejected"
ACTION_BILL_RETURNED = "supplier_payments.bill_returned"
ACTION_BILL_HELD = "supplier_payments.bill_held"
ACTION_BILL_RESUMED = "supplier_payments.bill_resumed"

# ----- Approvals (per level) -----
ACTION_LEVEL_1_APPROVED = "supplier_payments.level_1_approved"
ACTION_LEVEL_2_APPROVED = "supplier_payments.level_2_approved"
ACTION_LEVEL_3_APPROVED = "supplier_payments.level_3_approved"
ACTION_SUPER_ADMIN_APPROVED = "supplier_payments.super_admin_approved"
ACTION_APPROVAL_REJECTED = "supplier_payments.approval_rejected"

# ----- Recommendation -----
ACTION_RECOMMENDATION_GENERATED = "supplier_payments.recommendation_generated"

# ----- Payment execution -----
ACTION_BILL_MARKED_READY = "supplier_payments.bill_marked_ready"
ACTION_PAYMENT_EXECUTED = "supplier_payments.payment_executed"
ACTION_PAYMENT_PROOF_UPLOADED = "supplier_payments.payment_proof_uploaded"
ACTION_PAYMENT_VERIFIED = "supplier_payments.payment_verified"
ACTION_PAYMENT_RECONCILED = "supplier_payments.payment_reconciled"
ACTION_PAYMENT_DISPUTED = "supplier_payments.payment_disputed"

# ----- Bank account -----
ACTION_BANK_ACCOUNT_CREATED = "supplier_payments.bank_account_created"
ACTION_BANK_ACCOUNT_VERIFIED = "supplier_payments.bank_account_verified"
ACTION_BANK_ACCOUNT_DEACTIVATED = "supplier_payments.bank_account_deactivated"

# ----- Workflow config -----
ACTION_WORKFLOW_UPDATED = "supplier_payments.workflow_updated"

# ----- Duplicate / dispute flags -----
ACTION_DUPLICATE_FLAGGED = "supplier_payments.duplicate_flagged"
ACTION_DISPUTE_RAISED = "supplier_payments.dispute_raised"
ACTION_DISPUTE_RESOLVED = "supplier_payments.dispute_resolved"
