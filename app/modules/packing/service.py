"""Packing service.

Lifecycle entry points
----------------------
- :meth:`open_session` — order must be in ``packing`` state. Snapshots the
  expected items from ``order_lines`` × the inventory reservation ledger.
  Each session line carries the FEFO-chosen batch the picker is expected
  to scan from.
- :meth:`scan` — picker scans a unit (barcode + batch_id). Rules:
   1. **Unknown barcode** — variant lookup fails → outcome ``unknown_barcode``,
      blocked, scan logged.
   2. **Wrong item** — variant doesn't match any open session line →
      outcome ``wrong_item``, blocked.
   3. **Expired** — batch.expiry_date is past today → outcome ``expired``,
      blocked.
   4. **Batch mismatch** — scanned batch ≠ ``expected_batch_id`` →
      outcome ``batch_mismatch``, blocked. Picker must escalate to
      supervisor (use :meth:`override_scan`).
   5. **Over quantity** — line is already at expected_quantity → outcome
      ``over_quantity``, blocked.
   6. **Accepted** — increment scanned_quantity, mark line ``complete``
      when full. When the last open line completes, the session itself
      transitions to ``completed`` and emits an outbox event.
- :meth:`override_scan` — supervisor accepts a batch_mismatch. Same
  validation but the batch check is bypassed and the line's
  ``accepted_batch_id`` is recorded for audit.
- :meth:`cancel_session` — voids a session with reason; emits event.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit.service import record_audit
from app.core.errors import (
    BusinessRuleError,
    ConflictError,
    NotFoundError,
)
from app.core.events.outbox import enqueue_outbox
from app.core.logging import get_logger
from app.core.security.principal import Principal
from app.core.time import utc_now
from app.modules.catalog.models import ProductVariant
from app.modules.inventory.models import (
    Batch,
    BatchStatus,
    LedgerKind,
    StockBucket,
    StockLedger,
)
from app.modules.orders.models import Order
from app.modules.orders.state import OrderStatus
from app.modules.packing.events import (
    EVT_PACKING_BLOCKED,
    EVT_PACKING_SESSION_CANCELLED,
    EVT_PACKING_SESSION_COMPLETED,
    EVT_PACKING_SESSION_OPENED,
    EVT_PACKING_SUPERVISOR_OVERRIDE,
)
from app.modules.packing.models import PackingSession
from app.modules.packing.repository import PackingRepository, require_session
from app.modules.packing.state import (
    PackingLineStatus,
    PackingSessionStatus,
    ScanOutcome,
)

_logger = get_logger("hypershop.packing")


class PackingService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = PackingRepository(session)

    # ---------------- Session open ----------------

    async def open_session(
        self,
        *,
        principal: Principal,
        order_id: UUID,
        notes: str | None,
    ) -> PackingSession:
        order = await self.session.get(Order, order_id)
        if order is None:
            raise NotFoundError("Order not found.")
        if OrderStatus(order.status) != OrderStatus.PACKING:
            raise BusinessRuleError(
                "Order must be in PACKING state to open a packing session.",
                details={"status": order.status},
            )

        # Snapshot reserved batches for this order from the stock ledger.
        # FEFO chose specific batches at reserve time; the picker is expected
        # to grab from those.
        reserved = await self._reserved_lines_for_order(order_id)
        # reserved: dict[(variant_id, batch_id)] -> qty

        # Build session lines from order_lines × reserved.
        session = await self.repo.open_session_for_order(
            order_id=order_id, opened_by=principal.user_id, notes=notes,
        )
        await self.session.refresh(order, attribute_names=("lines",))
        for ol in order.lines:
            # Each order line maps to one or more reserved (batch, qty)
            # entries. Create one session line per (variant, batch) pair.
            mapped: dict[UUID, int] = {
                bid: q for (vid, bid), q in reserved.items() if vid == ol.variant_id
            }
            if not mapped:
                raise BusinessRuleError(
                    "No reserved stock found for an order line.",
                    details={"order_line_id": str(ol.id), "variant_id": str(ol.variant_id)},
                )
            total_reserved = sum(mapped.values())
            if total_reserved != ol.quantity:
                raise BusinessRuleError(
                    "Reserved quantity does not match order line quantity.",
                    details={
                        "order_line_id": str(ol.id),
                        "ordered": ol.quantity,
                        "reserved": total_reserved,
                    },
                )
            for batch_id, qty in mapped.items():
                await self.repo.add_line(
                    session_id=session.id,
                    order_line_id=ol.id,
                    variant_id=ol.variant_id,
                    expected_batch_id=batch_id,
                    expected_quantity=qty,
                    status=PackingLineStatus.OPEN.value,
                )

        await record_audit(
            actor=principal,
            action="packing.session.open",
            resource_type="packing_session",
            resource_id=session.id,
            metadata={"order_id": str(order_id), "line_count": len(order.lines)},
        )
        await enqueue_outbox(
            type=EVT_PACKING_SESSION_OPENED,
            payload={
                "session_id": str(session.id),
                "order_id": str(order_id),
            },
        )
        return require_session(await self.repo.get_session(session.id))

    async def _reserved_lines_for_order(
        self, order_id: UUID,
    ) -> dict[tuple[UUID, UUID], int]:
        """Sum +reserved ledger rows (after RELEASE/CONSUME debits) per
        ``(variant_id, batch_id)`` for an order. Net amount = currently
        reserved.
        """
        stmt = (
            select(
                StockLedger.variant_id,
                StockLedger.batch_id,
                StockLedger.bucket,
                StockLedger.quantity_delta,
            )
            .where(StockLedger.correlation_id == order_id)
        )
        rows = (await self.session.execute(stmt)).all()
        balance: dict[tuple[UUID, UUID], int] = {}
        for variant_id, batch_id, bucket, delta in rows:
            if bucket != StockBucket.RESERVED:
                continue
            key = (variant_id, batch_id)
            balance[key] = balance.get(key, 0) + int(delta)
        # Strip empty / negative entries.
        return {k: v for k, v in balance.items() if v > 0}

    # ---------------- Scan ----------------

    async def scan(
        self,
        *,
        principal: Principal,
        session_id: UUID,
        barcode: str,
        batch_id: UUID,
        notes: str | None,
    ) -> dict[str, Any]:
        sess = require_session(await self.repo.get_session_locked(session_id))
        if sess.status != PackingSessionStatus.OPEN.value:
            raise BusinessRuleError(
                "Packing session is not open.", details={"status": sess.status},
            )

        # 1. Look up variant by barcode.
        variant = await self._lookup_variant_by_barcode(barcode)
        if variant is None:
            scan = await self._record_blocked_scan(
                session=sess,
                outcome=ScanOutcome.UNKNOWN_BARCODE,
                barcode=barcode,
                matched_variant_id=None,
                scanned_batch_id=batch_id,
                principal=principal,
                notes=notes,
            )
            return self._scan_result(
                outcome=ScanOutcome.UNKNOWN_BARCODE,
                accepted=False,
                line=None,
                session_status=sess.status,
                session_completed=False,
                can_supervisor_override=False,
                message="Barcode not recognised.",
            )

        # 2. Find an open line for this variant.
        candidate_lines = [
            li
            for li in sess.lines
            if li.variant_id == variant.id and li.status == PackingLineStatus.OPEN.value
        ]
        if not candidate_lines:
            await self._record_blocked_scan(
                session=sess,
                outcome=ScanOutcome.WRONG_ITEM,
                barcode=barcode,
                matched_variant_id=variant.id,
                scanned_batch_id=batch_id,
                principal=principal,
                notes=notes,
            )
            # Distinguish "no such line" from "already complete" for the picker.
            already_done = any(
                li.variant_id == variant.id and li.status != PackingLineStatus.OPEN.value
                for li in sess.lines
            )
            return self._scan_result(
                outcome=ScanOutcome.WRONG_ITEM,
                accepted=False,
                line=None,
                session_status=sess.status,
                session_completed=False,
                can_supervisor_override=False,
                message=(
                    "This item is already fully packed."
                    if already_done
                    else "This item is not on the order."
                ),
            )

        # 3. Look up batch + reject if expired.
        batch = await self.session.get(Batch, batch_id)
        if batch is None:
            await self._record_blocked_scan(
                session=sess,
                outcome=ScanOutcome.WRONG_ITEM,
                barcode=barcode,
                matched_variant_id=variant.id,
                scanned_batch_id=batch_id,
                principal=principal,
                notes=notes,
            )
            return self._scan_result(
                outcome=ScanOutcome.WRONG_ITEM,
                accepted=False,
                line=None,
                session_status=sess.status,
                session_completed=False,
                can_supervisor_override=False,
                message="Unknown batch.",
            )

        today = utc_now().date()
        if batch.expiry_date <= today or batch.status == BatchStatus.EXPIRED:
            target_line = candidate_lines[0]
            await self._record_blocked_scan(
                session=sess,
                outcome=ScanOutcome.EXPIRED,
                barcode=barcode,
                matched_variant_id=variant.id,
                scanned_batch_id=batch_id,
                principal=principal,
                notes=notes,
                session_line_id=target_line.id,
            )
            return self._scan_result(
                outcome=ScanOutcome.EXPIRED,
                accepted=False,
                line=target_line,
                session_status=sess.status,
                session_completed=False,
                can_supervisor_override=False,
                message="Batch is expired — never pack.",
            )

        # 4. Match batch against expected. Pick the line that expects this batch
        # (if any); otherwise fall back to the first candidate for mismatch reporting.
        matching_line = next(
            (li for li in candidate_lines if li.expected_batch_id == batch_id),
            None,
        )
        if matching_line is None:
            target_line = candidate_lines[0]
            await self._record_blocked_scan(
                session=sess,
                outcome=ScanOutcome.BATCH_MISMATCH,
                barcode=barcode,
                matched_variant_id=variant.id,
                scanned_batch_id=batch_id,
                principal=principal,
                notes=notes,
                session_line_id=target_line.id,
            )
            return self._scan_result(
                outcome=ScanOutcome.BATCH_MISMATCH,
                accepted=False,
                line=target_line,
                session_status=sess.status,
                session_completed=False,
                can_supervisor_override=True,
                message=(
                    "Batch does not match the reserved batch — supervisor "
                    "override required to substitute."
                ),
            )

        # 5. Over-quantity guard (defence in depth — DB CHECK also enforces).
        if matching_line.scanned_quantity >= matching_line.expected_quantity:
            await self._record_blocked_scan(
                session=sess,
                outcome=ScanOutcome.OVER_QUANTITY,
                barcode=barcode,
                matched_variant_id=variant.id,
                scanned_batch_id=batch_id,
                principal=principal,
                notes=notes,
                session_line_id=matching_line.id,
            )
            return self._scan_result(
                outcome=ScanOutcome.OVER_QUANTITY,
                accepted=False,
                line=matching_line,
                session_status=sess.status,
                session_completed=False,
                can_supervisor_override=False,
                message="Line is already fully packed.",
            )

        # 6. ACCEPT
        return await self._accept(
            principal=principal,
            sess=sess,
            line=matching_line,
            barcode=barcode,
            batch_id=batch_id,
            outcome=ScanOutcome.ACCEPTED,
            is_supervisor_override=False,
            supervisor_user_id=None,
            notes=notes,
        )

    # ---------------- Supervisor override ----------------

    async def override_scan(
        self,
        *,
        principal: Principal,
        session_id: UUID,
        line_id: UUID,
        barcode: str,
        batch_id: UUID,
        reason: str,
    ) -> dict[str, Any]:
        sess = require_session(await self.repo.get_session_locked(session_id))
        if sess.status != PackingSessionStatus.OPEN.value:
            raise BusinessRuleError(
                "Packing session is not open.", details={"status": sess.status},
            )
        line = next((li for li in sess.lines if li.id == line_id), None)
        if line is None:
            raise NotFoundError("Session line not found.")
        if line.status != PackingLineStatus.OPEN.value:
            raise BusinessRuleError(
                "Line is not open.", details={"status": line.status},
            )

        variant = await self._lookup_variant_by_barcode(barcode)
        if variant is None or variant.id != line.variant_id:
            raise BusinessRuleError(
                "Barcode does not match the line being overridden.",
            )

        batch = await self.session.get(Batch, batch_id)
        if batch is None or batch.variant_id != variant.id:
            raise BusinessRuleError(
                "Batch is not for the line's variant.",
            )
        today = utc_now().date()
        if batch.expiry_date <= today or batch.status == BatchStatus.EXPIRED:
            raise BusinessRuleError(
                "Cannot override with an expired batch.",
            )
        if batch.status == BatchStatus.BLOCKED:
            raise BusinessRuleError(
                "Cannot override with a blocked batch.",
            )

        line.accepted_batch_id = batch_id
        line.status = PackingLineStatus.OVERRIDDEN.value
        await self.session.flush()

        await enqueue_outbox(
            type=EVT_PACKING_SUPERVISOR_OVERRIDE,
            payload={
                "session_id": str(sess.id),
                "line_id": str(line.id),
                "supervisor_user_id": str(principal.user_id),
                "expected_batch_id": str(line.expected_batch_id),
                "accepted_batch_id": str(batch_id),
                "reason": reason,
            },
        )

        return await self._accept(
            principal=principal,
            sess=sess,
            line=line,
            barcode=barcode,
            batch_id=batch_id,
            outcome=ScanOutcome.OVERRIDDEN,
            is_supervisor_override=True,
            supervisor_user_id=principal.user_id,
            notes=reason,
        )

    # ---------------- Accept (shared) ----------------

    async def _accept(
        self,
        *,
        principal: Principal,
        sess: PackingSession,
        line: Any,
        barcode: str,
        batch_id: UUID,
        outcome: ScanOutcome,
        is_supervisor_override: bool,
        supervisor_user_id: UUID | None,
        notes: str | None,
    ) -> dict[str, Any]:
        line.scanned_quantity = line.scanned_quantity + 1
        if line.scanned_quantity >= line.expected_quantity and (
            line.status == PackingLineStatus.OPEN.value
        ):
            line.status = PackingLineStatus.COMPLETE.value
        await self.session.flush()

        await self.repo.write_scan(
            session_id=sess.id,
            session_line_id=line.id,
            scanned_barcode=barcode,
            matched_variant_id=line.variant_id,
            scanned_batch_id=batch_id,
            outcome=outcome.value,
            is_supervisor_override=is_supervisor_override,
            supervisor_user_id=supervisor_user_id,
            scanned_by=principal.user_id,
            notes=notes,
        )

        # Reload to see all line statuses post-flush.
        await self.session.refresh(sess, attribute_names=("lines",))
        all_done = all(
            li.status in (PackingLineStatus.COMPLETE.value, PackingLineStatus.OVERRIDDEN.value)
            and li.scanned_quantity >= li.expected_quantity
            for li in sess.lines
        )
        session_completed = False
        if all_done:
            sess.status = PackingSessionStatus.COMPLETED.value
            sess.completed_at = utc_now()
            sess.completed_by = principal.user_id
            await self.session.flush()
            session_completed = True
            await record_audit(
                actor=principal,
                action="packing.session.complete",
                resource_type="packing_session",
                resource_id=sess.id,
                metadata={"order_id": str(sess.order_id)},
            )
            await enqueue_outbox(
                type=EVT_PACKING_SESSION_COMPLETED,
                payload={
                    "session_id": str(sess.id),
                    "order_id": str(sess.order_id),
                },
            )

        return self._scan_result(
            outcome=outcome,
            accepted=True,
            line=line,
            session_status=sess.status,
            session_completed=session_completed,
            can_supervisor_override=False,
            message=(
                "Item packed."
                if not session_completed
                else "Final item packed — session complete."
            ),
        )

    # ---------------- Cancel ----------------

    async def cancel_session(
        self,
        *,
        principal: Principal,
        session_id: UUID,
        reason: str,
    ) -> PackingSession:
        sess = require_session(await self.repo.get_session_locked(session_id))
        if sess.status != PackingSessionStatus.OPEN.value:
            raise BusinessRuleError("Only open sessions can be cancelled.")
        sess.status = PackingSessionStatus.CANCELLED.value
        sess.cancelled_at = utc_now()
        sess.cancellation_reason = reason
        await self.session.flush()
        await record_audit(
            actor=principal,
            action="packing.session.cancel",
            resource_type="packing_session",
            resource_id=sess.id,
            metadata={"order_id": str(sess.order_id), "reason": reason},
        )
        await enqueue_outbox(
            type=EVT_PACKING_SESSION_CANCELLED,
            payload={
                "session_id": str(sess.id),
                "order_id": str(sess.order_id),
                "reason": reason,
            },
        )
        return require_session(await self.repo.get_session(sess.id))

    # ---------------- Internal ----------------

    async def _lookup_variant_by_barcode(
        self, barcode: str,
    ) -> ProductVariant | None:
        return (
            await self.session.execute(
                select(ProductVariant).where(ProductVariant.barcode == barcode),
            )
        ).scalar_one_or_none()

    async def _record_blocked_scan(
        self,
        *,
        session: PackingSession,
        outcome: ScanOutcome,
        barcode: str,
        matched_variant_id: UUID | None,
        scanned_batch_id: UUID | None,
        principal: Principal,
        notes: str | None,
        session_line_id: UUID | None = None,
    ) -> None:
        await self.repo.write_scan(
            session_id=session.id,
            session_line_id=session_line_id,
            scanned_barcode=barcode,
            matched_variant_id=matched_variant_id,
            scanned_batch_id=scanned_batch_id,
            outcome=outcome.value,
            is_supervisor_override=False,
            supervisor_user_id=None,
            scanned_by=principal.user_id,
            notes=notes,
        )
        await enqueue_outbox(
            type=EVT_PACKING_BLOCKED,
            payload={
                "session_id": str(session.id),
                "order_id": str(session.order_id),
                "outcome": outcome.value,
                "barcode": barcode,
                "scanned_batch_id": str(scanned_batch_id) if scanned_batch_id else None,
                "matched_variant_id": (
                    str(matched_variant_id) if matched_variant_id else None
                ),
            },
        )

    @staticmethod
    def _scan_result(
        *,
        outcome: ScanOutcome,
        accepted: bool,
        line: Any | None,
        session_status: str,
        session_completed: bool,
        can_supervisor_override: bool,
        message: str,
    ) -> dict[str, Any]:
        return {
            "outcome": outcome.value,
            "accepted": accepted,
            "line_id": line.id if line is not None else None,
            "line_status": line.status if line is not None else None,
            "line_scanned_quantity": (
                line.scanned_quantity if line is not None else None
            ),
            "line_expected_quantity": (
                line.expected_quantity if line is not None else None
            ),
            "session_status": session_status,
            "session_completed": session_completed,
            "can_supervisor_override": can_supervisor_override,
            "message": message,
        }


def _silence() -> None:  # pragma: no cover
    _ = (LedgerKind, ConflictError)
