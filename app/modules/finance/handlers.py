"""Outbox handlers that drive finance postings off cross-module events.

Mirror of the inventory.handlers / iam.handlers pattern. Each handler:

1. Validates the payload via its consumer-side Pydantic schema (ignores
   unknown fields so producer evolution doesn't break us).
2. Opens its own ``UnitOfWork.transactional()`` scope so the JE write +
   audit + outbox handler-status update commit atomically.
3. Calls into :class:`FinanceService` whose posting helpers are
   idempotent on (source, reference_id) — at-least-once redelivery is
   safe and produces no duplicate JEs.

Importing this module registers the handlers as a side effect.
"""

from __future__ import annotations

import contextlib
from decimal import Decimal

from app.core.db.uow import UnitOfWork
from app.core.events.dispatcher import register_handler
from app.core.events.models import OutboxMessage
from app.core.logging import get_logger
from app.core.security.principal import SystemPrincipal
from app.modules.finance.events import (
    EVT_DELIVERY_COMPLETED,
    EVT_ORDER_CANCELLED,
    EVT_ORDER_COMPLETED,
    EVT_ORDER_PAYMENT_CONFIRMED,
    EVT_PAYMENT_RECONCILE_COMPLETED,
    EVT_RETURN_COMPLETED,
    DeliveryCompletedPayload,
    OrderCancelledPayload,
    OrderCompletedPayload,
    OrderPaymentConfirmedPayload,
    PaymentReconcileCompletedPayload,
    ReturnCompletedPayload,
)
from app.modules.finance.service import FinanceService

_logger = get_logger("hypershop.finance.handlers")


async def _handle_order_payment_confirmed(message: OutboxMessage) -> None:
    payload = OrderPaymentConfirmedPayload.model_validate(message.payload)
    uow = UnitOfWork()
    async with uow.transactional() as session:
        svc = FinanceService(session)
        entry = await svc.post_order_revenue(
            actor=SystemPrincipal(), order_id=payload.order_id,
        )
    _logger.info(
        "finance_order_revenue_handled",
        order_id=str(payload.order_id),
        entry_id=str(entry.id) if entry else None,
        skipped=entry is None,
    )


async def _handle_order_completed(message: OutboxMessage) -> None:
    payload = OrderCompletedPayload.model_validate(message.payload)
    uow = UnitOfWork()
    async with uow.transactional() as session:
        svc = FinanceService(session)
        entry = await svc.post_order_cogs(
            actor=SystemPrincipal(), order_id=payload.order_id,
        )
    _logger.info(
        "finance_order_cogs_handled",
        order_id=str(payload.order_id),
        entry_id=str(entry.id) if entry else None,
        skipped=entry is None,
    )


async def _handle_order_cancelled(message: OutboxMessage) -> None:
    payload = OrderCancelledPayload.model_validate(message.payload)
    uow = UnitOfWork()
    async with uow.transactional() as session:
        svc = FinanceService(session)
        entry = await svc.reverse_order_revenue(
            actor=SystemPrincipal(),
            order_id=payload.order_id,
            reason=payload.reason or "order cancelled",
        )
    _logger.info(
        "finance_order_cancelled_handled",
        order_id=str(payload.order_id),
        reversal_id=str(entry.id) if entry else None,
        skipped=entry is None,
    )


async def _handle_delivery_completed(message: OutboxMessage) -> None:
    payload = DeliveryCompletedPayload.model_validate(message.payload)
    uow = UnitOfWork()
    async with uow.transactional() as session:
        from app.modules.deliveries.models import DeliveryAssignment

        assignment = await session.get(DeliveryAssignment, payload.assignment_id)
        if assignment is None:
            _logger.warning(
                "finance_cod_collection_skipped_assignment_missing",
                assignment_id=str(payload.assignment_id),
            )
            return
        if assignment.payment_method != "cod":
            return
        cod_amount = assignment.cod_collected
        if cod_amount is None or Decimal(cod_amount) <= 0:
            return
        svc = FinanceService(session)
        entry = await svc.post_cod_collection_for_delivery(
            actor=SystemPrincipal(),
            order_id=payload.order_id,
            delivery_id=payload.assignment_id,
            amount=Decimal(cod_amount),
        )
    _logger.info(
        "finance_cod_collection_handled",
        assignment_id=str(payload.assignment_id),
        order_id=str(payload.order_id),
        entry_id=str(entry.id) if entry else None,
        skipped=entry is None,
    )


async def _handle_return_completed(message: OutboxMessage) -> None:
    payload = ReturnCompletedPayload.model_validate(message.payload)
    uow = UnitOfWork()
    async with uow.transactional() as session:
        from sqlalchemy import select

        from app.modules.orders.models import OrderLine
        from app.modules.returns.models import ReturnRequestLine

        # Refund = sum(unit_price × inspected_quantity) for inspected lines.
        stmt = (
            select(
                ReturnRequestLine.inspected_quantity,
                OrderLine.unit_price,
            )
            .join(OrderLine, OrderLine.id == ReturnRequestLine.order_line_id)
            .where(
                ReturnRequestLine.return_request_id == payload.return_request_id,
                ReturnRequestLine.inspected_quantity.isnot(None),
            )
        )
        rows = (await session.execute(stmt)).all()
        amount = Decimal("0")
        for inspected_qty, unit_price in rows:
            if inspected_qty and unit_price:
                amount += Decimal(unit_price) * Decimal(inspected_qty)
        if amount <= 0:
            _logger.info(
                "finance_refund_skipped_zero",
                return_request_id=str(payload.return_request_id),
            )
            return

        svc = FinanceService(session)
        record = await svc.accrue_refund_for_return(
            actor=SystemPrincipal(),
            return_request_id=payload.return_request_id,
            order_id=payload.order_id,
            customer_user_id=payload.customer_user_id,
            amount=amount,
        )
    _logger.info(
        "finance_refund_accrued",
        return_request_id=str(payload.return_request_id),
        record_id=str(record.id) if record else None,
        amount=str(amount),
        skipped=record is None,
    )


async def _handle_payment_reconcile_completed(message: OutboxMessage) -> None:
    """Post the per-report drift summary as a JournalEntry.

    Strategy: read the SettlementReport + its matches; compute
    ``expected_total = sum(payment_intent.amount)`` over matched lines;
    post one balanced entry per report:

      Dr  Cash-in-Bank            submitted_total
      Cr  AR-Gateway-Pending      expected_total
      Dr/Cr  Gateway-Drift        |drift|   (plug)

    The drift sign convention follows the producer:
      drift_total > 0  → gateway over-reported (we got more than expected)
                         → Credit Gateway-Drift (gain / contra-expense)
      drift_total < 0  → gateway under-reported (shortfall)
                         → Debit Gateway-Drift (expense)

    Idempotency: short-circuit if a journal_entry already exists with
    ``reference_type='payment_settlement_report'`` and
    ``reference_id=report_id``.

    Orphans (settled with no matching intent) are intentionally NOT
    auto-booked here — they could be duplicates, fraud, or unrelated
    transactions; ops reconciles via the existing UI. A log warning is
    emitted so the issue is visible.
    """
    from datetime import date as _date

    from sqlalchemy import text as _t

    from app.modules.finance.accounts import (
        A_AR_GATEWAY_PENDING,
        A_CASH_IN_BANK,
        X_GATEWAY_DRIFT,
    )
    from app.modules.finance.service import LineSpec
    from app.modules.finance.state import JournalEntrySource

    payload = PaymentReconcileCompletedPayload.model_validate(message.payload)
    uow = UnitOfWork()
    async with uow.transactional() as session:
        # Idempotency guard — skip if already posted.
        existing = (
            await session.execute(
                _t(
                    "SELECT id FROM fin_journal_entries "
                    "WHERE reference_type = 'payment_settlement_report' "
                    "  AND reference_id = :rid LIMIT 1"
                ),
                {"rid": payload.report_id},
            )
        ).scalar_one_or_none()
        if existing is not None:
            _logger.info(
                "finance_payment_reconcile_skipped_already_posted",
                report_id=str(payload.report_id),
                existing_entry_id=str(existing),
            )
            return

        # expected_total = sum of payment_intent.amount across matched lines.
        # Orphans (payment_intent_id IS NULL) are excluded from clearing.
        expected_total = (
            await session.execute(
                _t(
                    "SELECT COALESCE(SUM(pi.amount), 0) "
                    "FROM payment_settlement_matches m "
                    "JOIN payment_intents pi ON pi.id = m.payment_intent_id "
                    "WHERE m.settlement_report_id = :rid"
                ),
                {"rid": payload.report_id},
            )
        ).scalar_one()
        expected_total = Decimal(str(expected_total or 0)).quantize(Decimal("0.01"))
        submitted_total = Decimal(payload.submitted_total).quantize(Decimal("0.01"))
        drift = (submitted_total - expected_total).quantize(Decimal("0.01"))

        if payload.orphan_count:
            _logger.warning(
                "finance_payment_reconcile_orphans_skipped",
                report_id=str(payload.report_id),
                orphan_count=payload.orphan_count,
                hint="Orphan settlement lines are not auto-booked; reconcile via UI.",
            )

        # Skip if nothing matched-or-drifted (i.e. all lines are orphans).
        # Without a baseline of expected_total the "drift" is meaningless —
        # booking submitted_total as pure drift would misrepresent orphans
        # as gateway overpayment. Ops handles all-orphan reports via the
        # existing reconcile UI. Note: matched_count = exact-match count
        # only; drift_count = matched-but-out-of-tolerance count — both
        # have a real intent_id and are eligible for clearing.
        matched_or_drifted = payload.matched_count + payload.drift_count
        if matched_or_drifted == 0 or expected_total <= 0:
            _logger.info(
                "finance_payment_reconcile_skipped_no_match",
                report_id=str(payload.report_id),
                matched_count=payload.matched_count,
                drift_count=payload.drift_count,
                orphan_count=payload.orphan_count,
            )
            return

        # The amount cleared from the bank for matched intents only.
        # Orphan amounts are excluded — they stay on the report row.
        cash_in = expected_total + drift  # = matched portion of submitted
        if cash_in <= 0:
            _logger.info(
                "finance_payment_reconcile_skipped_zero",
                report_id=str(payload.report_id),
            )
            return

        narration = (
            f"Gateway settlement reconciliation — {payload.provider} "
            f"{payload.report_date.isoformat()}"
        )
        lines: list[LineSpec] = [
            LineSpec(
                A_CASH_IN_BANK, debit=cash_in,
                memo=f"Settled to bank by {payload.provider} (matched portion)",
            ),
            LineSpec(
                A_AR_GATEWAY_PENDING, credit=expected_total,
                memo=f"Clearing matched intents on {payload.report_date.isoformat()}",
            ),
        ]
        if drift > 0:
            # Over-payment: credit drift account (gain side).
            lines.append(LineSpec(
                X_GATEWAY_DRIFT, credit=drift,
                memo="Gateway over-reported vs matched intents",
            ))
        elif drift < 0:
            # Shortfall: debit drift account (expense side).
            lines.append(LineSpec(
                X_GATEWAY_DRIFT, debit=-drift,
                memo="Gateway under-reported vs matched intents",
            ))

        svc = FinanceService(session)
        entry = await svc.post_entry(
            actor=SystemPrincipal(),
            entry_date=payload.report_date,
            narration=narration,
            lines=lines,
            source=JournalEntrySource.GATEWAY_RECONCILIATION.value,
            currency=payload.currency,
            reference_type="payment_settlement_report",
            reference_id=payload.report_id,
            extra={
                "provider": payload.provider,
                "matched_count": payload.matched_count,
                "drift_count": payload.drift_count,
                "orphan_count": payload.orphan_count,
                "submitted_count": payload.submitted_count,
                "drift_total_payload": str(payload.drift_total),
                "drift_total_computed": str(drift),
            },
        )

    _logger.info(
        "finance_payment_reconcile_posted",
        report_id=str(payload.report_id),
        entry_id=str(entry.id),
        submitted_total=str(submitted_total),
        expected_total=str(expected_total),
        drift=str(drift),
        orphan_count=payload.orphan_count,
    )


def register_finance_handlers() -> None:
    """Idempotent — safe to call multiple times (tests do)."""
    for event_type, handler in (
        (EVT_ORDER_PAYMENT_CONFIRMED, _handle_order_payment_confirmed),
        (EVT_ORDER_COMPLETED, _handle_order_completed),
        (EVT_ORDER_CANCELLED, _handle_order_cancelled),
        (EVT_DELIVERY_COMPLETED, _handle_delivery_completed),
        (EVT_RETURN_COMPLETED, _handle_return_completed),
        (EVT_PAYMENT_RECONCILE_COMPLETED, _handle_payment_reconcile_completed),
    ):
        with contextlib.suppress(ValueError):
            register_handler(event_type, handler)


register_finance_handlers()
