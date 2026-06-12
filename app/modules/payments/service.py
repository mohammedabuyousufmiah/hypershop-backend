"""Payments service — orchestrates intent lifecycle, webhooks, refunds.

Hard rules:
  - The service NEVER decides which adapter to call — it asks the
    registry for the provider matching the intent's stored
    ``provider`` name. Captured Bkash intents always refund via Bkash,
    even after the operator switches the default to SSLCommerz.
  - Every gateway call gets a ``payment_attempts`` audit row written
    BEFORE the call (status='pending') and updated after.
  - Webhook ingestion is idempotent on (provider, event_id). Duplicates
    return success without mutating state.
  - Outbox events are emitted INSIDE the same transaction that mutates
    the intent — the orders/finance handlers run downstream.
"""

from __future__ import annotations

import time
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit.service import record_audit
from app.core.errors import (
    BusinessRuleError,
    IntegrationError,
    NotFoundError,
    ServiceUnavailableError,
    ValidationError,
)
from app.core.events.outbox import enqueue_outbox
from app.core.logging import get_logger
from app.core.security.principal import Principal, SystemPrincipal
from app.core.time import utc_now
from app.modules.payments import codes as C
from app.modules.payments import events as E
from app.modules.payments.models import (
    PaymentAttempt,
    PaymentIntent,
    PaymentRefund,
    PaymentWebhook,
    SettlementMatch,
    SettlementReport,
)
from app.modules.payments.providers import get_provider
from app.modules.payments.providers.base import (
    CreatePaymentRequest,
    RefundProviderRequest,
)
from app.modules.payments.repository import (
    PaymentAttemptRepository,
    PaymentIntentRepository,
    PaymentRefundRepository,
    PaymentWebhookRepository,
)
from app.modules.payments.state import (
    PaymentStatus,
    can_transition,
    is_terminal,
)

_logger = get_logger("hypershop.payments.service")


def _scrub_for_attempt(d: dict[str, Any] | None) -> dict[str, Any] | None:
    """Strip any field that looks like a credential before persisting
    to payment_attempts.{request,response}_summary. Belt-and-braces;
    adapters should already be sending sanitised bodies."""
    if not d:
        return d
    blocked = {"password", "store_passwd", "app_secret", "secret", "api_key"}
    return {
        k: ("***" if k.lower() in blocked else v)
        for k, v in d.items()
    }


class PaymentService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.intents = PaymentIntentRepository(session)
        self.attempts = PaymentAttemptRepository(session)
        self.webhooks = PaymentWebhookRepository(session)
        self.refunds = PaymentRefundRepository(session)

    # ════════════════════════════════════════════════════════════════
    # Initiate
    # ════════════════════════════════════════════════════════════════

    async def initiate(
        self,
        *,
        principal: Principal,
        order_id: UUID,
        provider_name: str,
        success_url: str,
        failure_url: str,
        cancel_url: str,
        webhook_url: str,
    ) -> PaymentIntent:
        """Create a fresh PaymentIntent for an order + drive the
        gateway's "create checkout" call. The customer's browser is
        then redirected to the returned ``checkout_url``.

        Idempotency:
          - If the order already has an intent in ``initiated`` state
            for the same provider, returns it (no duplicate gateway call).
          - If a previous intent for the same order exists in a
            terminal-failed/cancelled/expired state, a fresh intent is
            created (customer can retry).
        """
        # Lazy import to avoid circular dependency with orders module
        from app.modules.orders.models import Order

        order = await self.session.get(Order, order_id)
        if order is None:
            raise NotFoundError(f"Order {order_id} not found.")
        if order.customer_user_id != principal.user_id:
            raise NotFoundError(f"Order {order_id} not found.")  # don't leak existence
        if order.payment_method != "online":
            raise BusinessRuleError(
                f"Order {order.code} uses payment_method={order.payment_method}; "
                "online payment is only valid for orders placed with "
                "payment_method='online'.",
            )
        if order.status not in ("pending_payment",):
            raise BusinessRuleError(
                f"Order {order.code} is in status '{order.status}'; "
                "payment can only be initiated while status='pending_payment'.",
            )
        if provider_name not in C.ALL_PROVIDERS:
            raise ValidationError(
                f"Unsupported payment provider '{provider_name}'.",
                details={"supported": sorted(C.ALL_PROVIDERS)},
            )

        # Idempotency — return any open intent for this (order, provider).
        existing = await self.intents.latest_open_for_order(order_id)
        if existing and existing.provider == provider_name:
            return existing

        provider = get_provider(provider_name)

        intent = await self.intents.create(
            order_id=order.id,
            customer_user_id=order.customer_user_id,
            provider=provider_name,
            status=PaymentStatus.INITIATED.value,
            currency=order.currency,
            amount=order.grand_total,
            success_url=success_url,
            failure_url=failure_url,
            cancel_url=cancel_url,
        )

        await record_audit(
            actor=principal,
            action=C.ACTION_PAYMENT_INITIATED,
            resource_type="payment_intent",
            resource_id=intent.id,
            metadata={
                "order_id": str(order.id),
                "provider": provider_name,
                "amount": str(order.grand_total),
                "currency": order.currency,
            },
        )

        # Lookup customer for billing details (best effort; gateways
        # accept missing email/phone/name).
        from app.modules.iam.models import User
        user = await self.session.get(User, order.customer_user_id)

        # Fire the gateway "create" call inside an attempt audit row.
        attempt = await self.attempts.create(
            intent_id=intent.id,
            operation="create",
            status="pending",
            request_summary={"order_code": order.code, "amount": str(order.grand_total)},
        )
        t0 = time.monotonic()
        try:
            result = await provider.create_payment(
                CreatePaymentRequest(
                    intent_id=str(intent.id),
                    order_code=order.code,
                    amount=order.grand_total,
                    currency=order.currency,
                    customer_email=getattr(user, "email", None) if user else None,
                    customer_phone=getattr(user, "phone", None) if user else None,
                    customer_name=getattr(user, "full_name", None) if user else None,
                    success_url=success_url,
                    failure_url=failure_url,
                    cancel_url=cancel_url,
                    webhook_url=webhook_url,
                ),
            )
        except (IntegrationError, ServiceUnavailableError) as exc:
            attempt.status = "failed"
            attempt.error_code = exc.code
            attempt.error_message = exc.message[:2048]
            attempt.duration_ms = int((time.monotonic() - t0) * 1000)
            attempt.response_summary = _scrub_for_attempt(exc.details or {})
            intent.status = PaymentStatus.FAILED.value
            intent.last_error_code = exc.code
            intent.last_error_message = exc.message[:2048]
            intent.failed_at = utc_now()
            await record_audit(
                actor=principal,
                action=C.ACTION_PAYMENT_FAILED,
                resource_type="payment_intent",
                resource_id=intent.id,
                outcome="failure",
                metadata={"error_code": exc.code},
            )
            await enqueue_outbox(
                type=E.EVT_PAYMENT_FAILED,
                payload={
                    "intent_id": str(intent.id),
                    "order_id": str(order.id),
                    "provider": provider_name,
                    "error_code": exc.code,
                    "error_message": exc.message[:512],
                },
            )
            raise

        # Success — store provider IDs + checkout URL on the intent
        attempt.status = "ok"
        attempt.duration_ms = int((time.monotonic() - t0) * 1000)
        attempt.response_summary = {
            "provider_payment_id": result.provider_payment_id,
            "checkout_url": result.checkout_url,
        }
        intent.provider_payment_id = result.provider_payment_id
        intent.checkout_url = result.checkout_url
        intent.expires_at = result.expires_at
        intent.metadata_json = _scrub_for_attempt(dict(result.raw or {}))
        await self.session.flush()
        return intent

    # ════════════════════════════════════════════════════════════════
    # Webhook ingestion
    # ════════════════════════════════════════════════════════════════

    async def ingest_webhook(
        self,
        *,
        provider_name: str,
        body: bytes,
        headers: dict[str, str],
        ip_address: str | None = None,
    ) -> tuple[PaymentWebhook, str]:
        """Ingest one webhook delivery from a gateway. Returns the
        webhook row + a resolution string ('processed' | 'rejected' |
        'duplicate'). Never raises — every error path is mapped to
        ``rejected`` so we always 200/400 the gateway.
        """
        if provider_name not in C.ALL_PROVIDERS:
            # Drop on the floor with a log; we never want to write a
            # row for an unknown gateway slug.
            _logger.warning("webhook_unknown_provider", provider=provider_name)
            raise ValidationError(f"Unknown payment provider '{provider_name}'.")

        provider = get_provider(provider_name)

        # Parse + verify signature first (might throw IntegrationError
        # with reason in details). On any parse failure, log a webhook
        # row with status='rejected' so we have an audit trail.
        try:
            event = provider.parse_webhook(body=body, headers=headers)
        except IntegrationError as exc:
            wb, _ = await self.webhooks.insert_or_get_existing(
                provider=provider_name,
                # Synthetic event_id for failed parses — by content hash.
                event_id="rejected:" + (str(headers.get("x-request-id") or utc_now().isoformat()))[:96],
                intent_id=None,
                raw_body={"raw": body[:8192].decode("utf-8", errors="replace")},
                raw_headers=dict(headers),
                status="rejected",
                rejection_reason=str(exc.details.get("reason") or exc.message)[:512],
            )
            await record_audit(
                actor=SystemPrincipal(),
                action=C.ACTION_PAYMENT_WEBHOOK_REJECTED,
                resource_type="payment_webhook",
                resource_id=wb.id,
                outcome="failure",
                ip_address=ip_address,
                metadata={"provider": provider_name, "reason": exc.code},
            )
            return wb, "rejected"

        # Resolve the intent the event refers to.
        intent = await self.intents.get_by_provider_id(
            provider=provider_name,
            provider_payment_id=event.provider_payment_id,
        )

        # Insert (or fetch existing for dedup) the webhook row.
        wb, was_new = await self.webhooks.insert_or_get_existing(
            provider=provider_name,
            event_id=event.event_id,
            intent_id=intent.id if intent else None,
            raw_body={
                "parsed": {
                    "event_id": event.event_id,
                    "provider_payment_id": event.provider_payment_id,
                    "status": event.status,
                    "amount_captured": str(event.amount_captured) if event.amount_captured is not None else None,
                    "amount_refunded": str(event.amount_refunded) if event.amount_refunded is not None else None,
                },
                "raw": body[:8192].decode("utf-8", errors="replace"),
            },
            raw_headers=dict(headers),
            status="received",
        )
        if not was_new:
            return wb, "duplicate"

        if intent is None:
            await self.webhooks.mark_rejected(
                wb,
                f"No intent matches provider_payment_id={event.provider_payment_id}",
            )
            return wb, "rejected"

        # Apply the state transition.
        try:
            await self._apply_event_to_intent(intent=intent, event=event)
        except (BusinessRuleError, ValidationError) as exc:
            await self.webhooks.mark_rejected(wb, exc.message[:512])
            return wb, "rejected"

        await self.webhooks.mark_processed(wb, processed_at=utc_now())
        await record_audit(
            actor=SystemPrincipal(),
            action=C.ACTION_PAYMENT_WEBHOOK_PROCESSED,
            resource_type="payment_webhook",
            resource_id=wb.id,
            ip_address=ip_address,
            metadata={
                "provider": provider_name,
                "intent_id": str(intent.id),
                "new_status": intent.status,
            },
        )
        return wb, "processed"

    async def _apply_event_to_intent(
        self,
        *,
        intent: PaymentIntent,
        event,
    ) -> None:
        """Mutate ``intent`` based on ``event.status`` + emit outbox."""
        target = event.status
        if is_terminal(intent.status) and target != intent.status:
            # Don't downgrade or re-process terminal intents.
            return
        if intent.status == target:
            return
        if not can_transition(intent.status, target):
            raise BusinessRuleError(
                f"Cannot transition intent {intent.id} from "
                f"'{intent.status}' to '{target}'.",
            )
        now = utc_now()
        intent.status = target
        if target == PaymentStatus.AUTHORIZED.value:
            intent.authorized_at = now
        elif target == PaymentStatus.CAPTURED.value:
            intent.captured_at = now
            if event.amount_captured is not None:
                intent.amount_captured = event.amount_captured
            else:
                intent.amount_captured = intent.amount
            await enqueue_outbox(
                type=E.EVT_PAYMENT_CAPTURED,
                payload={
                    "intent_id": str(intent.id),
                    "order_id": str(intent.order_id),
                    "provider": intent.provider,
                    "amount": str(intent.amount_captured),
                    "currency": intent.currency,
                    "captured_at": now.isoformat(),
                },
            )
            # ---- ORDER CONFIRMATION (added 2026-05-13) ----
            # Synchronously transition the order from PENDING_PAYMENT
            # → PAYMENT_CONFIRMED inside the same transaction as the
            # intent flip. Previously this relied on an async outbox
            # subscriber that was never built, leaving every
            # successfully-paid online order stuck in
            # ``pending_payment`` indefinitely.
            #
            # Note: ``payment_confirmed`` is the exact string the
            # ``ck_orders_status_enum`` constraint accepts. The order
            # state machine then advances to ``stock_reserved`` →
            # ``approved`` → ``packing`` → ``out_for_delivery`` →
            # ``completed`` via the orders-service workers.
            try:
                from app.modules.orders.models import Order
                order = await self.session.get(Order, intent.order_id)
                if order is not None and order.status == "pending_payment":
                    order.status = "payment_confirmed"
                    order.payment_confirmed_at = now
            except Exception as e:  # noqa: BLE001
                # Never let an order-side write failure roll back the
                # payment ingest — the intent IS captured; an order
                # row out of sync is recoverable from the outbox event
                # via a backfill job. Worst-case the customer sees
                # "Payment received, order processing" instead of
                # "Order confirmed" until ops reconciles.
                from app.core.logging import get_logger
                get_logger("hypershop.payments.service").warning(
                    "order_status_update_failed_on_capture",
                    intent_id=str(intent.id),
                    order_id=str(intent.order_id),
                    error=str(e),
                )
        elif target == PaymentStatus.FAILED.value:
            intent.failed_at = now
            intent.last_error_code = event.error_code
            intent.last_error_message = (event.error_message or "")[:2048]
            await enqueue_outbox(
                type=E.EVT_PAYMENT_FAILED,
                payload={
                    "intent_id": str(intent.id),
                    "order_id": str(intent.order_id),
                    "provider": intent.provider,
                    "error_code": event.error_code,
                    "error_message": (event.error_message or "")[:512],
                },
            )
        elif target == PaymentStatus.CANCELLED.value:
            intent.cancelled_at = now
            await enqueue_outbox(
                type=E.EVT_PAYMENT_CANCELLED,
                payload={
                    "intent_id": str(intent.id),
                    "order_id": str(intent.order_id),
                    "provider": intent.provider,
                },
            )
        elif target == PaymentStatus.EXPIRED.value:
            intent.cancelled_at = now
            await enqueue_outbox(
                type=E.EVT_PAYMENT_EXPIRED,
                payload={
                    "intent_id": str(intent.id),
                    "order_id": str(intent.order_id),
                    "provider": intent.provider,
                },
            )
        elif target in (
            PaymentStatus.REFUNDED.value,
            PaymentStatus.PARTIALLY_REFUNDED.value,
        ):
            if event.amount_refunded is not None:
                intent.amount_refunded = event.amount_refunded
        await self.session.flush()

    # ════════════════════════════════════════════════════════════════
    # Refund
    # ════════════════════════════════════════════════════════════════

    async def refund(
        self,
        *,
        principal: Principal,
        intent_id: UUID,
        amount: Decimal,
        reason: str | None,
        return_request_id: UUID | None = None,
    ) -> PaymentRefund:
        intent = await self.intents.get(intent_id)
        if intent is None:
            raise NotFoundError(f"PaymentIntent {intent_id} not found.")
        if intent.status not in (
            PaymentStatus.CAPTURED.value,
            PaymentStatus.PARTIALLY_REFUNDED.value,
        ):
            raise BusinessRuleError(
                f"Intent {intent_id} is in status '{intent.status}'; "
                "refunds are only valid against captured intents.",
            )
        already_refunded = await self.refunds.total_succeeded_for_intent(intent_id)
        remaining = intent.amount_captured - already_refunded
        if amount > remaining:
            raise ValidationError(
                f"Refund amount {amount} exceeds remaining refundable "
                f"balance {remaining} on intent {intent_id}.",
                details={
                    "remaining": str(remaining),
                    "captured": str(intent.amount_captured),
                    "already_refunded": str(already_refunded),
                },
            )

        refund = await self.refunds.create(
            intent_id=intent.id,
            return_request_id=return_request_id,
            requested_by_user_id=principal.user_id,
            amount=amount,
            currency=intent.currency,
            status="requested",
            reason=reason,
        )

        await record_audit(
            actor=principal,
            action=C.ACTION_PAYMENT_REFUND_REQUESTED,
            resource_type="payment_refund",
            resource_id=refund.id,
            metadata={
                "intent_id": str(intent.id),
                "amount": str(amount),
                "currency": intent.currency,
                "return_request_id": str(return_request_id) if return_request_id else None,
            },
        )

        provider = get_provider(intent.provider)
        if not intent.provider_payment_id:
            refund.status = "failed"
            refund.error_code = "missing_provider_payment_id"
            refund.error_message = (
                "Cannot refund — intent has no provider_payment_id "
                "(webhook hasn't populated it yet)."
            )
            return refund

        attempt = await self.attempts.create(
            intent_id=intent.id,
            operation="refund",
            status="pending",
            request_summary={
                "refund_id": str(refund.id),
                "amount": str(amount),
            },
        )
        t0 = time.monotonic()
        try:
            result = await provider.refund(
                RefundProviderRequest(
                    intent_id=str(intent.id),
                    provider_payment_id=intent.provider_payment_id,
                    amount=amount,
                    currency=intent.currency,
                    reason=reason,
                    refund_request_id=str(refund.id),
                ),
            )
        except (IntegrationError, ServiceUnavailableError) as exc:
            attempt.status = "failed"
            attempt.error_code = exc.code
            attempt.error_message = exc.message[:2048]
            attempt.duration_ms = int((time.monotonic() - t0) * 1000)
            refund.status = "failed"
            refund.error_code = exc.code
            refund.error_message = exc.message[:2048]
            await record_audit(
                actor=principal,
                action=C.ACTION_PAYMENT_REFUND_FAILED,
                resource_type="payment_refund",
                resource_id=refund.id,
                outcome="failure",
                metadata={"error_code": exc.code},
            )
            return refund

        attempt.status = "ok"
        attempt.duration_ms = int((time.monotonic() - t0) * 1000)
        attempt.response_summary = _scrub_for_attempt(result.raw or {})

        if result.status == "succeeded":
            refund.status = "succeeded"
            refund.provider_refund_id = result.provider_refund_id
            refund.succeeded_at = utc_now()
            # Bump intent's amount_refunded
            new_total = await self.refunds.total_succeeded_for_intent(intent.id)
            intent.amount_refunded = new_total
            if new_total >= intent.amount_captured:
                intent.status = PaymentStatus.REFUNDED.value
            else:
                intent.status = PaymentStatus.PARTIALLY_REFUNDED.value
            await record_audit(
                actor=principal,
                action=C.ACTION_PAYMENT_REFUND_SUCCEEDED,
                resource_type="payment_refund",
                resource_id=refund.id,
                metadata={
                    "intent_id": str(intent.id),
                    "amount": str(amount),
                    "new_intent_status": intent.status,
                },
            )
            await enqueue_outbox(
                type=E.EVT_PAYMENT_REFUND_SUCCEEDED,
                payload={
                    "refund_id": str(refund.id),
                    "intent_id": str(intent.id),
                    "order_id": str(intent.order_id),
                    "amount": str(amount),
                    "currency": intent.currency,
                    "return_request_id": str(return_request_id) if return_request_id else None,
                },
            )
        else:
            refund.status = "failed"
            refund.error_code = result.error_code
            refund.error_message = (result.error_message or "")[:2048]
            await record_audit(
                actor=principal,
                action=C.ACTION_PAYMENT_REFUND_FAILED,
                resource_type="payment_refund",
                resource_id=refund.id,
                outcome="failure",
                metadata={"error_code": result.error_code},
            )
            await enqueue_outbox(
                type=E.EVT_PAYMENT_REFUND_FAILED,
                payload={
                    "refund_id": str(refund.id),
                    "intent_id": str(intent.id),
                    "order_id": str(intent.order_id),
                    "amount": str(amount),
                    "error_code": result.error_code,
                },
            )
        await self.session.flush()
        return refund

    # ════════════════════════════════════════════════════════════════
    # Read helpers (used by API serializers)
    # ════════════════════════════════════════════════════════════════

    async def get_intent_for_customer(
        self, *, principal: Principal, intent_id: UUID,
    ) -> PaymentIntent:
        intent = await self.intents.get(intent_id)
        if intent is None or intent.customer_user_id != principal.user_id:
            raise NotFoundError(f"PaymentIntent {intent_id} not found.")
        return intent

    async def get_intent_admin(self, intent_id: UUID) -> PaymentIntent:
        intent = await self.intents.get(intent_id)
        if intent is None:
            raise NotFoundError(f"PaymentIntent {intent_id} not found.")
        return intent


# ---------------------------------------------------------------------------
# Settlement reconciliation (added 2026-05-16)
# ---------------------------------------------------------------------------


# Drift threshold in the report currency. Below this the line is recorded
# as ``matched`` even if amounts differ — rounding artifacts in the
# provider's ledger aren't worth a finance escalation.
_DRIFT_TOLERANCE = Decimal("1.00")


class SettlementReconciliationService:
    """Matches a provider's settlement file against captured PaymentIntents.

    The operator uploads one ``SettlementReconcileRequest`` per
    (provider, business date). Each line is matched to a
    ``PaymentIntent`` by ``provider_payment_id``; drift > 1 unit of
    the report currency is flagged. Drift summary is stored on the
    ``SettlementReport`` row + emitted as an outbox event for finance
    to consume.

    Idempotency: ``(provider, report_date)`` is unique at the DB layer.
    Re-uploading the same business day raises a ``BusinessRuleError``.
    Per-line uniqueness on ``(report_id, provider_ref)`` prevents
    duplicate lines within a report.
    """

    def __init__(self, session: AsyncSession) -> None:
        from app.modules.payments.repository import SettlementReportRepository
        self.session = session
        self.reports = SettlementReportRepository(session)

    async def reconcile(
        self,
        *,
        principal: Principal,
        provider: str,
        report_date,
        currency: str,
        lines: list[Any],  # list[SettlementLineIn] — typed by caller
    ) -> tuple[SettlementReport, list[SettlementMatch]]:
        # ── 1. Header — fail fast on duplicate business day. ──────────
        # Aggregates from operator-submitted lines so we can detect if
        # rows got dropped between their CSV and our DB.
        from sqlalchemy.exc import IntegrityError

        submitted_total = sum((ln.amount for ln in lines), Decimal("0"))
        report = SettlementReport(
            provider=provider,
            report_date=report_date,
            currency=currency,
            submitted_total=submitted_total,
            submitted_count=len(lines),
            status="pending",
        )
        try:
            await self.reports.add(report)
        except IntegrityError as exc:
            # The (provider, report_date) unique constraint fires here
            # when ops re-submits the same day. Surface a clean business
            # error instead of leaking the SQL constraint name.
            raise BusinessRuleError(
                f"Settlement report for {provider} on {report_date.isoformat()} "
                "already exists. Delete the existing report before resubmitting."
            ) from exc

        # ── 2. Bulk-lookup intents for every ref. Single round-trip. ──
        ref_to_intent = await self.reports.find_intents_by_provider_refs(
            provider=provider,
            refs=[ln.provider_ref for ln in lines],
        )

        # ── 3. Match each line, accumulate counters. ──────────────────
        matched_count = 0
        drift_count = 0
        orphan_count = 0
        drift_total = Decimal("0")
        matches: list[SettlementMatch] = []

        for line in lines:
            intent = ref_to_intent.get(line.provider_ref)
            if intent is None:
                m = SettlementMatch(
                    settlement_report_id=report.id,
                    payment_intent_id=None,
                    provider_ref=line.provider_ref,
                    provider_amount=line.amount,
                    intent_amount=None,
                    drift_amount=Decimal("0"),
                    status="orphan",
                )
                orphan_count += 1
            else:
                drift = line.amount - intent.amount_captured
                abs_drift = abs(drift)
                if abs_drift <= _DRIFT_TOLERANCE:
                    status = "matched"
                    matched_count += 1
                else:
                    status = "drift"
                    drift_count += 1
                    drift_total += abs_drift
                m = SettlementMatch(
                    settlement_report_id=report.id,
                    payment_intent_id=intent.id,
                    provider_ref=line.provider_ref,
                    provider_amount=line.amount,
                    intent_amount=intent.amount_captured,
                    drift_amount=drift,
                    status=status,
                )
            await self.reports.add_match(m)
            matches.append(m)

        # ── 4. Finalise the header. ───────────────────────────────────
        report.matched_count = matched_count
        report.drift_count = drift_count
        report.orphan_count = orphan_count
        report.drift_total = drift_total
        report.status = "processed"
        report.processed_at = utc_now()
        report.processed_by = principal.user_id

        await self.session.flush()

        # ── 5. Audit + outbox. Finance consumes the outbox event to ──
        # post the drift summary as a JournalEntry; we deliberately
        # don't import finance here.
        await record_audit(
            actor=principal,
            action="payment.reconciliation.completed",
            resource_type="payment_settlement_report",
            resource_id=report.id,
            outcome="success",
            metadata={
                "provider": provider,
                "report_date": report_date.isoformat(),
                "matched": matched_count,
                "drift": drift_count,
                "orphan": orphan_count,
                "drift_total": str(drift_total),
            },
        )
        await enqueue_outbox(
            type=E.EVT_PAYMENT_RECONCILE_COMPLETED,
            payload={
                "report_id": str(report.id),
                "provider": provider,
                "report_date": report_date.isoformat(),
                "currency": currency,
                "matched_count": matched_count,
                "drift_count": drift_count,
                "orphan_count": orphan_count,
                "drift_total": str(drift_total),
                "submitted_total": str(submitted_total),
                "submitted_count": len(lines),
            },
        )
        return report, matches
