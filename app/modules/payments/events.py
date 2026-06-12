"""Domain events emitted by the payments module to the outbox.

Subscribed by:
  - orders module:  EVT_PAYMENT_CAPTURED → transition order to
                    ``payment_confirmed`` and set ``payment_confirmed_at``.
                    EVT_PAYMENT_FAILED   → transition order to ``failed``.
  - finance module: EVT_PAYMENT_CAPTURED → post the cash receipt journal
                    entry against the bank/gateway clearing account.
                    EVT_PAYMENT_REFUND_SUCCEEDED → post the refund
                    journal entry.
"""

from __future__ import annotations

# Money-moved events
EVT_PAYMENT_CAPTURED = "payment.captured"
EVT_PAYMENT_FAILED = "payment.failed"
EVT_PAYMENT_CANCELLED = "payment.cancelled"
EVT_PAYMENT_EXPIRED = "payment.expired"

# Refund events
EVT_PAYMENT_REFUND_SUCCEEDED = "payment.refund.succeeded"
EVT_PAYMENT_REFUND_FAILED = "payment.refund.failed"

# Reconciliation events (added 2026-05-16).
# Finance subscribes to post the drift summary as a JournalEntry.
EVT_PAYMENT_RECONCILE_COMPLETED = "payment.reconciliation.completed"
