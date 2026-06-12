"""Outbox event types emitted by the supplier-payment approval engine.

Used to decouple notification sends (email to next-level approver,
email to procurement on rejection) from the transactional approval
write path. Send failures retry via the outbox dispatcher's
exponential backoff rather than rolling back the approval decision.
"""

from __future__ import annotations

# Fired when a bill enters a state that needs a NEW approver to act.
# Payload: { "bill_id", "bill_code", "next_level": int (1..4),
#            "supplier_name", "grand_total", "currency",
#            "submitted_by_user_id", "workflow_code" }
EVT_APPROVAL_NEEDED = "supplier_payments.approval.needed"

# Fired when a bill is rejected at any level — procurement needs to fix.
EVT_BILL_REJECTED = "supplier_payments.bill.rejected"

# Fired when a bill is returned for correction.
EVT_BILL_RETURNED = "supplier_payments.bill.returned"

# Fired when a bill is fully approved (level-3 or super-admin done) and
# is ready for finance to mark-ready + execute payment.
EVT_BILL_FULLY_APPROVED = "supplier_payments.bill.fully_approved"
