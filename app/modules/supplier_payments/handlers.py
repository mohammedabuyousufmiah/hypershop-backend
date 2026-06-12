"""Outbox handlers for supplier-payment approval notifications.

Subscribes to the events emitted by ``ApprovalEngine._record_decision``
and emails the right people:

  - ``approval.needed``       → next-level approver list (per env-var)
  - ``bill.fully_approved``   → finance team (level-3 list — they
                                 mark-ready + execute payment)
  - ``bill.rejected``         → procurement team
  - ``bill.returned``         → procurement team

Uses the existing ``SmtpEmailTransport`` so the same SMTP config that
powers IAM password-reset emails powers these. If SMTP is not
configured (no SMTP_HOST set), the transport raises ServiceUnavailable
which the outbox dispatcher retries — eventually the message lands in
the dead-letter feed where ops can spot the misconfig.

Env-driven recipient lists keep the notification policy outside the
code: ops adds/removes approvers via env vars without a redeploy.
Empty list = no notification (handler logs + returns; not an error).
"""

from __future__ import annotations

import contextlib

from app.core.config import get_settings
from app.core.events.dispatcher import register_handler
from app.core.events.models import OutboxMessage
from app.core.logging import get_logger
from app.modules.iam.transport.email_smtp import SmtpEmailTransport
from app.modules.supplier_payments.events import (
    EVT_APPROVAL_NEEDED,
    EVT_BILL_FULLY_APPROVED,
    EVT_BILL_REJECTED,
    EVT_BILL_RETURNED,
)

_log = get_logger("hypershop.supplier_payments.handlers")
# Module-local transport instance. The SmtpEmailTransport reads its
# config inside ``send`` so a missing SMTP_HOST surfaces at send-time
# rather than at import-time.
_email = SmtpEmailTransport()


def _recipients_for_level(level: int) -> list[str]:
    """Return the env-configured email list for the next approval level.

    Level 1 → SUPPLIER_PAYMENT_APPROVER_EMAILS_L1 (and so on).
    Empty list = no recipients; handler returns early without sending.
    """
    s = get_settings()
    return list(getattr(
        s, f"supplier_payment_approver_emails_l{level}", [],
    ))


def _procurement_recipients() -> list[str]:
    return list(get_settings().supplier_payment_procurement_emails)


async def _send_email_safely(
    *,
    to_list: list[str],
    subject: str,
    text_body: str,
    log_event: str,
    bill_id: str,
) -> None:
    """Send to each recipient in turn.

    We send one email per recipient (instead of one with many To: lines)
    so a single bad address doesn't kill the whole batch. Per-send
    failures bubble up — the outbox redelivers the whole event, and
    on retry the previous successful sends ARE duplicated. That's
    acceptable for low-volume approval notifications (the alternative
    is per-recipient idempotency which is overkill for ops emails).
    """
    if not to_list:
        _log.info(
            f"{log_event}_no_recipients_configured",
            bill_id=bill_id,
        )
        return
    for to in to_list:
        await _email.send(
            to=to,
            subject=subject,
            text_body=text_body,
        )
        _log.info(log_event, bill_id=bill_id, to=to)


def _format_bill_line(payload: dict) -> str:
    return (
        f"Bill: {payload.get('bill_code', '—')}\n"
        f"Supplier: {payload.get('supplier_name', '—')}\n"
        f"Amount: {payload.get('grand_total', '0')} "
        f"{payload.get('currency', 'BDT')}\n"
        f"Workflow: {payload.get('workflow_code', '—')}\n"
    )


# ----------------------------------------------------------------------
# approval.needed
# ----------------------------------------------------------------------
async def _handle_approval_needed(message: OutboxMessage) -> None:
    payload = message.payload or {}
    next_level = int(payload.get("next_level", 0) or 0)
    if next_level < 1 or next_level > 4:
        _log.warning(
            "approval_needed_invalid_level",
            level=next_level,
            bill_id=payload.get("bill_id"),
        )
        return
    subject = (
        f"[Hypershop AP] Approval needed — Level {next_level} "
        f"({payload.get('supplier_name', '—')})"
    )
    body = (
        f"A supplier bill is waiting for your approval at Level {next_level}.\n\n"
        + _format_bill_line(payload) +
        f"\nNote from prior reviewer: {payload.get('note') or '(none)'}\n"
        f"\nAction in admin: /admin/supplier-payments/queue\n"
    )
    await _send_email_safely(
        to_list=_recipients_for_level(next_level),
        subject=subject,
        text_body=body,
        log_event="approval_needed_email_sent",
        bill_id=str(payload.get("bill_id", "")),
    )


# ----------------------------------------------------------------------
# bill.fully_approved
# ----------------------------------------------------------------------
async def _handle_fully_approved(message: OutboxMessage) -> None:
    payload = message.payload or {}
    subject = (
        f"[Hypershop AP] Ready to pay — {payload.get('supplier_name', '—')} "
        f"({payload.get('grand_total', '0')} {payload.get('currency', 'BDT')})"
    )
    body = (
        "All approval levels cleared. Finance can mark-ready + execute "
        "payment.\n\n"
        + _format_bill_line(payload) +
        "\nAction in admin: /admin/supplier-payments/queue?status=approved_final\n"
    )
    # Finance team = level-3 list (they're the ones who execute payments).
    await _send_email_safely(
        to_list=_recipients_for_level(3),
        subject=subject,
        text_body=body,
        log_event="bill_fully_approved_email_sent",
        bill_id=str(payload.get("bill_id", "")),
    )


# ----------------------------------------------------------------------
# bill.rejected / bill.returned
# ----------------------------------------------------------------------
async def _handle_rejected(message: OutboxMessage) -> None:
    payload = message.payload or {}
    subject = (
        f"[Hypershop AP] Bill REJECTED — {payload.get('supplier_name', '—')}"
    )
    body = (
        f"A supplier bill was rejected at Level {payload.get('level', '?')}.\n\n"
        + _format_bill_line(payload) +
        f"\nReason: {payload.get('note') or '(no note given)'}\n"
        "\nThe bill won't be paid. Procurement: review with the supplier.\n"
    )
    await _send_email_safely(
        to_list=_procurement_recipients(),
        subject=subject,
        text_body=body,
        log_event="bill_rejected_email_sent",
        bill_id=str(payload.get("bill_id", "")),
    )


async def _handle_returned(message: OutboxMessage) -> None:
    payload = message.payload or {}
    subject = (
        f"[Hypershop AP] Bill returned for correction — "
        f"{payload.get('supplier_name', '—')}"
    )
    body = (
        f"A supplier bill was returned for correction at Level "
        f"{payload.get('level', '?')}.\n\n"
        + _format_bill_line(payload) +
        f"\nWhat to fix: {payload.get('note') or '(see ops)'}\n"
        "\nAction: edit the bill, then resubmit via /admin/supplier-payments.\n"
    )
    await _send_email_safely(
        to_list=_procurement_recipients(),
        subject=subject,
        text_body=body,
        log_event="bill_returned_email_sent",
        bill_id=str(payload.get("bill_id", "")),
    )


def register_supplier_payment_handlers() -> None:
    """Idempotent — safe to call multiple times (tests + reload)."""
    with contextlib.suppress(ValueError):
        register_handler(EVT_APPROVAL_NEEDED, _handle_approval_needed)
    with contextlib.suppress(ValueError):
        register_handler(EVT_BILL_FULLY_APPROVED, _handle_fully_approved)
    with contextlib.suppress(ValueError):
        register_handler(EVT_BILL_REJECTED, _handle_rejected)
    with contextlib.suppress(ValueError):
        register_handler(EVT_BILL_RETURNED, _handle_returned)


register_supplier_payment_handlers()
