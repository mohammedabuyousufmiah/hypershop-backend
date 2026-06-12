"""Outbox handlers for the rider wallet module.

Listens for delivery events and posts the corresponding wallet ledger
rows. Decoupled — the deliveries module knows nothing about wallets;
the rider_wallet module subscribes via the dispatcher.

Registered:
  - ``deliveries.delivery.delivered``  → if the assignment had
    ``cod_collected > 0``, post a ``cod_collection`` ledger row.
    Idempotent on (delivery_assignment_id, entry_type).
  - ``deliveries.delivery.failed``     → defensive compensation. The
    state machine prevents DELIVERED → FAILED, so under normal
    operation this is a no-op. It exists for the case where a
    cod_collection somehow exists for an assignment that ends up
    FAILED/CANCELLED (e.g. manual ops correction).
  - ``deliveries.delivery.cancelled``  → same defensive compensation.

Event payloads from ``deliveries.service._transition`` only carry
identifiers (assignment_id, order_id, rider_id, from/to status,
reason). ``cod_collected`` is NOT in the payload — handlers must
load the assignment row and read it from there.
"""

from __future__ import annotations

import contextlib
from decimal import Decimal
from uuid import UUID

from app.core.db.uow import UnitOfWork
from app.core.events.dispatcher import register_handler
from app.core.events.models import OutboxMessage
from app.core.logging import get_logger
from app.core.security.principal import SystemPrincipal
from app.modules.deliveries.events import (
    EVT_DELIVERY_CANCELLED,
    EVT_DELIVERY_DELIVERED,
    EVT_DELIVERY_FAILED,
)
from app.modules.deliveries.models import DeliveryAssignment
from app.modules.rider_wallet.repository import (
    RiderWalletLedgerRepository,
)
from app.modules.rider_wallet.service import RiderWalletService
from app.modules.rider_wallet.state import (
    LedgerDirection,
    LedgerEntryType,
)

_log = get_logger("hypershop.rider_wallet.handlers")


# ----------------------------------------------------------------------
# DELIVERED → post cod_collection
# ----------------------------------------------------------------------
async def _handle_delivery_delivered(message: OutboxMessage) -> None:
    """When a delivery is marked DELIVERED, load the assignment and —
    if it carried COD — post a cod_collection ledger row.

    Idempotent: ``RiderWalletService.post_cod_collection`` checks the
    ledger for an existing cod_collection row keyed on assignment_id
    and skips if found, so at-least-once redelivery is safe.
    """
    payload = message.payload or {}
    assignment_id_str = payload.get("assignment_id")
    rider_id_str = payload.get("rider_id")
    if not assignment_id_str or not rider_id_str:
        _log.warning(
            "delivery_delivered_payload_missing_keys",
            keys=list(payload.keys()),
        )
        return

    assignment_id = UUID(assignment_id_str)
    rider_id = UUID(rider_id_str)

    async with UnitOfWork().transactional() as session:
        # The DELIVERED event payload only has IDs — read cod_collected
        # from the assignment row itself.
        a = await session.get(DeliveryAssignment, assignment_id)
        if a is None:
            _log.warning(
                "delivery_delivered_assignment_missing",
                assignment_id=str(assignment_id),
            )
            return
        if a.payment_method != "cod" or a.cod_collected is None:
            return  # online-paid; no wallet effect
        if a.cod_collected <= 0:
            return

        amount = Decimal(a.cod_collected)
        svc = RiderWalletService(session)
        await svc.post_cod_collection(
            rider_id=rider_id,
            delivery_assignment_id=assignment_id,
            amount=amount,
            principal=SystemPrincipal(),
            note=f"COD delivered (assignment {assignment_id})",
        )
    _log.info(
        "rider_wallet_cod_posted",
        rider_id=str(rider_id),
        assignment_id=str(assignment_id),
        amount=str(amount),
    )


# ----------------------------------------------------------------------
# FAILED / CANCELLED → defensive compensation
# ----------------------------------------------------------------------
async def _handle_delivery_reversed(message: OutboxMessage) -> None:
    """If a delivery is marked FAILED or CANCELLED but a cod_collection
    ledger row already exists for it, post a compensating credit so
    the wallet reflects reality.

    The state machine prevents DELIVERED → FAILED, so under normal
    operation this handler is a no-op. It exists defensively for:
      - manual ops corrections that bypass the state machine
      - a future state-machine relaxation
      - paranoid double-check against the COD ingest handler running
        twice with conflicting outcomes

    Uses ``CASH_DEPOSIT_CORRECTION`` (CREDIT direction) so the audit
    trail is unambiguous about what happened.
    """
    payload = message.payload or {}
    assignment_id_str = payload.get("assignment_id")
    rider_id_str = payload.get("rider_id")
    if not assignment_id_str or not rider_id_str:
        return

    assignment_id = UUID(assignment_id_str)
    rider_id = UUID(rider_id_str)

    async with UnitOfWork().transactional() as session:
        ledgers = RiderWalletLedgerRepository(session)
        # Did COD get posted earlier?
        had_collection = await ledgers.has_assignment_entry(
            delivery_assignment_id=assignment_id,
            entry_type=LedgerEntryType.COD_COLLECTION.value,
        )
        if not had_collection:
            return  # nothing to reverse — common case

        # Avoid double-reversing if this handler runs twice (outbox
        # at-least-once redelivery).
        already_reversed = await ledgers.has_assignment_entry(
            delivery_assignment_id=assignment_id,
            entry_type=LedgerEntryType.CASH_DEPOSIT_CORRECTION.value,
        )
        if already_reversed:
            return

        a = await session.get(DeliveryAssignment, assignment_id)
        if a is None or a.cod_collected is None:
            return
        amount = Decimal(a.cod_collected)
        if amount <= 0:
            return

        svc = RiderWalletService(session)
        wallet = await svc.get_or_create(
            rider_id=rider_id, principal=SystemPrincipal(),
        )
        new_payable = max(
            Decimal("0"),
            wallet.wallet_payable_to_company - amount,
        )
        new_cash = max(Decimal("0"), wallet.cash_in_hand - amount)
        await svc.wallets.update(
            wallet_id=wallet.id,
            wallet_payable_to_company=new_payable,
            cash_in_hand=new_cash,
            wallet_status=RiderWalletService._derive_status(
                wallet, payable=new_payable,
                pending=wallet.wallet_pending_settlement,
            ),
        )
        await ledgers.add(
            rider_id=rider_id,
            delivery_assignment_id=assignment_id,
            entry_type=LedgerEntryType.CASH_DEPOSIT_CORRECTION.value,
            direction=LedgerDirection.CREDIT.value,
            amount=amount,
            balance_after=new_payable,
            note=(
                f"Reversal: delivery {assignment_id} transitioned to "
                f"{payload.get('to_status', 'reversed')}"
            ),
        )
    _log.info(
        "rider_wallet_cod_reversed",
        rider_id=str(rider_id),
        assignment_id=str(assignment_id),
        amount=str(amount),
        trigger=message.type,
    )


# ----------------------------------------------------------------------
# Settlement notifications (SMS to rider)
# ----------------------------------------------------------------------
async def _send_settlement_sms(
    *,
    rider_id_str: str,
    text: str,
    log_event: str,
) -> None:
    """Common path: load rider phone, hand off to bound SMS transport.

    Bound transport may be NotConfiguredSmsTransport in dev/test —
    its ``send`` raises ServiceUnavailableError, which the outbox
    dispatcher catches + retries. We don't bypass that intentionally
    so misconfiguration shows up in the dead-letter queue rather
    than silently dropping notifications.
    """
    from app.modules.deliveries.models import Rider
    from app.modules.iam.transport.sms_registry import get_transport

    rider_id = UUID(rider_id_str)
    async with UnitOfWork().transactional() as session:
        rider = await session.get(Rider, rider_id)
        if rider is None:
            _log.warning("settlement_sms_rider_missing", rider_id=str(rider_id))
            return
        phone = rider.phone

    transport = get_transport()
    await transport.send(to=phone, text=text)
    _log.info(log_event, rider_id=str(rider_id), to=phone)


async def _handle_settlement_verified(message: OutboxMessage) -> None:
    payload = message.payload or {}
    amount = payload.get("verified_amount", "0")
    txn = payload.get("transaction_id", "—")
    text = (
        f"Hypershop: Your COD settlement of BDT {amount} (txn {txn}) "
        f"has been verified. Wallet credited. Thank you."
    )
    await _send_settlement_sms(
        rider_id_str=payload["rider_id"],
        text=text,
        log_event="settlement_verified_sms_sent",
    )


async def _handle_settlement_adjusted(message: OutboxMessage) -> None:
    """Partial verify — verified < submitted, or excess refund applied."""
    payload = message.payload or {}
    submitted = payload.get("submitted_amount", "0")
    verified = payload.get("verified_amount", "0")
    txn = payload.get("transaction_id", "—")
    text = (
        f"Hypershop: Your COD settlement of BDT {submitted} (txn {txn}) "
        f"was adjusted to BDT {verified}. Check your wallet ledger or "
        f"contact accounts."
    )
    await _send_settlement_sms(
        rider_id_str=payload["rider_id"],
        text=text,
        log_event="settlement_adjusted_sms_sent",
    )


async def _handle_settlement_rejected(message: OutboxMessage) -> None:
    payload = message.payload or {}
    submitted = payload.get("submitted_amount", "0")
    reason = (payload.get("reason") or "").strip()[:120]
    text = (
        f"Hypershop: Your COD settlement of BDT {submitted} was "
        f"REJECTED. Reason: {reason or 'see ops'}. Please re-submit "
        f"with the correct transaction details."
    )
    await _send_settlement_sms(
        rider_id_str=payload["rider_id"],
        text=text,
        log_event="settlement_rejected_sms_sent",
    )


def register_rider_wallet_handlers() -> None:
    """Idempotent — safe to call multiple times (tests + reload)."""
    from app.modules.rider_wallet.events import (
        EVT_SETTLEMENT_ADJUSTED,
        EVT_SETTLEMENT_REJECTED,
        EVT_SETTLEMENT_VERIFIED,
    )
    with contextlib.suppress(ValueError):
        register_handler(EVT_DELIVERY_DELIVERED, _handle_delivery_delivered)
    with contextlib.suppress(ValueError):
        register_handler(EVT_DELIVERY_FAILED, _handle_delivery_reversed)
    with contextlib.suppress(ValueError):
        register_handler(EVT_DELIVERY_CANCELLED, _handle_delivery_reversed)
    with contextlib.suppress(ValueError):
        register_handler(EVT_SETTLEMENT_VERIFIED, _handle_settlement_verified)
    with contextlib.suppress(ValueError):
        register_handler(EVT_SETTLEMENT_ADJUSTED, _handle_settlement_adjusted)
    with contextlib.suppress(ValueError):
        register_handler(EVT_SETTLEMENT_REJECTED, _handle_settlement_rejected)


register_rider_wallet_handlers()
