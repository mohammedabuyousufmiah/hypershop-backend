"""Provider names + audit action codes used by the payments module."""

from __future__ import annotations


# ---------------- Provider names ----------------
# String values match what's stored in payment_intents.provider and what
# webhook URLs use as their path segment. New gateway = new constant +
# new adapter + new factory branch + new webhook handler.
PROVIDER_BKASH = "bkash"
PROVIDER_SSLCOMMERZ = "sslcommerz"
PROVIDER_NAGAD = "nagad"
PROVIDER_ROCKET = "rocket"
# Dev/QA-only fake provider — auto-succeeds, never bound in production.
# See providers/fake.py + factory._try_bind_fake.
PROVIDER_FAKE = "fake"
ALL_PROVIDERS: frozenset[str] = frozenset({
    PROVIDER_BKASH,
    PROVIDER_SSLCOMMERZ,
    PROVIDER_NAGAD,
    PROVIDER_ROCKET,
    PROVIDER_FAKE,
})


# ---------------- Audit action codes ----------------
ACTION_PAYMENT_INITIATED = "payment.initiated"
ACTION_PAYMENT_AUTHORIZED = "payment.authorized"
ACTION_PAYMENT_CAPTURED = "payment.captured"
ACTION_PAYMENT_FAILED = "payment.failed"
ACTION_PAYMENT_CANCELLED = "payment.cancelled"
ACTION_PAYMENT_EXPIRED = "payment.expired"
ACTION_PAYMENT_REFUND_REQUESTED = "payment.refund.requested"
ACTION_PAYMENT_REFUND_SUCCEEDED = "payment.refund.succeeded"
ACTION_PAYMENT_REFUND_FAILED = "payment.refund.failed"
ACTION_PAYMENT_WEBHOOK_RECEIVED = "payment.webhook.received"
ACTION_PAYMENT_WEBHOOK_PROCESSED = "payment.webhook.processed"
ACTION_PAYMENT_WEBHOOK_REJECTED = "payment.webhook.rejected"
# Settlement reconciliation (added 2026-05-16) — emitted on a successful
# reconcile sweep. Finance worker consumes this to post the drift summary
# as a JournalEntry; payments deliberately doesn't couple to finance.
ACTION_PAYMENT_RECONCILE_COMPLETED = "payment.reconciliation.completed"
