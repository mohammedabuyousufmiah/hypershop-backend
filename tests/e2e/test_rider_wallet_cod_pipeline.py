"""End-to-end pipeline test for the COD → rider wallet ledger flow.

Covers the bug class that unit + smoke tests can't catch: the outbox
event payload doesn't carry ``cod_collected`` (only IDs), so the
handler MUST load the assignment from the DB. If the handler reads
from the payload, the wallet never gets credited and unit tests
won't notice.

Tests the full pipeline:
  1. Set up a rider + minimal order/assignment via direct SQL
  2. Update the assignment with cod_collected (mimics
     ``DeliveryService.deliver`` writing the row)
  3. Enqueue ``deliveries.delivery.delivered`` event
  4. Drain the outbox dispatcher
  5. Assert: rider_wallet row exists with the right balance,
     rider_wallet_ledger has a cod_collection entry keyed on the
     assignment_id, idempotency works (re-drain → no duplicate).

Also covers the FAILED reversal path: enqueue a delivery.failed
event, drain, assert a cash_deposit_correction credit landed.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select, text

from app.core.db.session import get_sessionmaker
from app.core.events.dispatcher import dispatch_once
from app.core.events.outbox import enqueue_outbox
from app.core.db.uow import UnitOfWork
from app.modules.deliveries.events import (
    EVT_DELIVERY_CANCELLED,
    EVT_DELIVERY_DELIVERED,
)
from app.modules.rider_wallet.models import (
    RiderWallet,
    RiderWalletLedger,
)

pytestmark = pytest.mark.integration


async def _drain_outbox(max_iterations: int = 20) -> int:
    """Run the dispatcher until no more pending messages.

    Returns total dispatched count for assertion convenience.
    """
    total = 0
    for _ in range(max_iterations):
        n = await dispatch_once()
        if n == 0:
            return total
        total += n
    return total


async def _seed_rider_and_order_with_cod(
    *, cod_amount: Decimal,
) -> dict[str, str]:
    """Create the minimum DB rows the handler needs:
    rider + order + delivery_assignment with cod_collected set.

    Bypasses HTTP / OrderService because we want to isolate the
    handler — the e2e in test_full_pipeline.py covers the HTTP path.
    """
    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        # Minimal user (admin role) to satisfy the user FK on
        # delivery_assignments.assigned_by.
        admin_id = uuid4()
        await s.execute(
            text(
                "INSERT INTO users (id, email, password_hash, "
                "is_active, is_verified) "
                "VALUES (:id, :email, :pw, true, true)",
            ),
            {
                "id": admin_id,
                "email": f"admin-{admin_id.hex[:8]}@e2e.dev",
                "pw": "$argon2id$v=19$m=8192,t=1,p=1$abcdefgh$abcdefgh",
            },
        )
        # Rider.
        rider_id = (
            await s.execute(
                text(
                    "INSERT INTO riders (code, name, phone, vehicle_type, "
                    "current_status, is_active) "
                    "VALUES (:c, :n, :p, 'motorcycle', 'available', true) "
                    "RETURNING id",
                ),
                {
                    "c": f"R-{uuid4().hex[:6]}",
                    "n": "E2E Rider",
                    "p": f"+88019{uuid4().hex[:8]}",
                },
            )
        ).scalar_one()
        # Customer (acts as the order's user_id).
        customer_id = uuid4()
        await s.execute(
            text(
                "INSERT INTO users (id, email, password_hash, "
                "is_active, is_verified) "
                "VALUES (:id, :email, :pw, true, true)",
            ),
            {
                "id": customer_id,
                "email": f"cust-{customer_id.hex[:8]}@e2e.dev",
                "pw": "$argon2id$v=19$m=8192,t=1,p=1$abcdefgh$abcdefgh",
            },
        )
        # Order — minimal columns; the handler doesn't read order data.
        order_id = (
            await s.execute(
                text(
                    "INSERT INTO orders (code, user_id, status, "
                    "payment_method, requires_prescription, currency, "
                    "subtotal, tax_total, shipping_total, discount_total, "
                    "grand_total, placed_at) "
                    "VALUES (:code, :uid, 'out_for_delivery', 'cod', false, "
                    "'BDT', 100, 0, 0, 0, :amt, now()) "
                    "RETURNING id",
                ),
                {
                    "code": f"O-{uuid4().hex[:8]}",
                    "uid": customer_id,
                    "amt": cod_amount,
                },
            )
        ).scalar_one()
        # Delivery assignment with COD already collected (mimics what
        # DeliveryService.deliver writes before emitting the event).
        assignment_id = (
            await s.execute(
                text(
                    "INSERT INTO delivery_assignments "
                    "(order_id, rider_id, status, payment_method, "
                    "cod_expected, cod_collected, cod_status, "
                    "assigned_by) "
                    "VALUES (:oid, :rid, 'delivered', 'cod', :exp, :coll, "
                    "'reconciled', :ab) "
                    "RETURNING id",
                ),
                {
                    "oid": order_id,
                    "rid": rider_id,
                    "exp": cod_amount,
                    "coll": cod_amount,
                    "ab": admin_id,
                },
            )
        ).scalar_one()
    return {
        "rider_id": str(rider_id),
        "order_id": str(order_id),
        "assignment_id": str(assignment_id),
    }


# ----------------------------------------------------------------------
# Happy path: deliver → outbox → ledger credited
# ----------------------------------------------------------------------
async def test_cod_collection_lands_in_ledger_after_outbox_drain() -> None:
    """The CRITICAL bug Tier-1 fix #5 addressed: original handler read
    cod_collected from the event payload (which doesn't carry it) and
    silently no-op'd. This test would have caught it.
    """
    cod = Decimal("450.00")
    seeded = await _seed_rider_and_order_with_cod(cod_amount=cod)

    # Mimic what DeliveryService._transition does after writing the
    # delivered row: enqueue the outbox event with the standard payload
    # shape (IDs only, no cod_collected).
    async with UnitOfWork().transactional():
        await enqueue_outbox(
            type=EVT_DELIVERY_DELIVERED,
            payload={
                "assignment_id": seeded["assignment_id"],
                "order_id": seeded["order_id"],
                "rider_id": seeded["rider_id"],
                "from_status": "picked_up",
                "to_status": "delivered",
                "reason": "delivered with POD",
            },
        )

    drained = await _drain_outbox()
    assert drained >= 1, "outbox dispatcher didn't fire any handlers"

    # Wallet should now exist with the right balance.
    sm = get_sessionmaker()
    async with sm() as s:
        wallet = (
            await s.execute(
                select(RiderWallet)
                .where(RiderWallet.rider_id == seeded["rider_id"]),
            )
        ).scalar_one_or_none()
        assert wallet is not None, "wallet not created"
        assert wallet.cash_in_hand == cod
        assert wallet.wallet_payable_to_company == cod
        assert wallet.wallet_status == "has_cod_balance"

        # Ledger should have exactly one cod_collection row keyed on
        # the assignment_id.
        ledger_rows = (
            await s.execute(
                select(RiderWalletLedger)
                .where(
                    RiderWalletLedger.delivery_assignment_id
                    == seeded["assignment_id"],
                ),
            )
        ).scalars().all()
        assert len(ledger_rows) == 1
        row = ledger_rows[0]
        assert row.entry_type == "cod_collection"
        assert row.direction == "debit"
        assert row.amount == cod
        assert row.balance_after == cod


# ----------------------------------------------------------------------
# Idempotency: redelivery of the same outbox event doesn't double-credit
# ----------------------------------------------------------------------
async def test_outbox_redelivery_is_idempotent() -> None:
    cod = Decimal("200.00")
    seeded = await _seed_rider_and_order_with_cod(cod_amount=cod)

    # Enqueue the same event twice — the outbox dispatcher will fire
    # the handler twice; the handler MUST be idempotent.
    async with UnitOfWork().transactional():
        for _ in range(2):
            await enqueue_outbox(
                type=EVT_DELIVERY_DELIVERED,
                payload={
                    "assignment_id": seeded["assignment_id"],
                    "order_id": seeded["order_id"],
                    "rider_id": seeded["rider_id"],
                    "from_status": "picked_up",
                    "to_status": "delivered",
                    "reason": "delivered with POD",
                },
            )

    await _drain_outbox()

    sm = get_sessionmaker()
    async with sm() as s:
        wallet = (
            await s.execute(
                select(RiderWallet)
                .where(RiderWallet.rider_id == seeded["rider_id"]),
            )
        ).scalar_one()
        # Balance reflects ONE collection, not two.
        assert wallet.cash_in_hand == cod
        # Exactly one cod_collection row.
        n_collections = (
            await s.execute(
                select(RiderWalletLedger)
                .where(
                    RiderWalletLedger.delivery_assignment_id
                    == seeded["assignment_id"],
                    RiderWalletLedger.entry_type == "cod_collection",
                ),
            )
        ).scalars().all()
        assert len(n_collections) == 1


# ----------------------------------------------------------------------
# Reversal path: cancelled delivery posts a compensating credit
# ----------------------------------------------------------------------
async def test_delivery_cancelled_posts_compensating_credit() -> None:
    """Defensive flow from Tier-1 fix #5: if a cod_collection ledger
    entry exists for an assignment that ends up CANCELLED, the
    reversal handler should credit-back the rider's wallet.
    """
    cod = Decimal("300.00")
    seeded = await _seed_rider_and_order_with_cod(cod_amount=cod)

    # First: post the COD via the delivered event.
    async with UnitOfWork().transactional():
        await enqueue_outbox(
            type=EVT_DELIVERY_DELIVERED,
            payload={
                "assignment_id": seeded["assignment_id"],
                "order_id": seeded["order_id"],
                "rider_id": seeded["rider_id"],
                "from_status": "picked_up",
                "to_status": "delivered",
                "reason": "delivered",
            },
        )
    await _drain_outbox()

    # Now: enqueue cancelled. The reversal handler should fire.
    async with UnitOfWork().transactional():
        await enqueue_outbox(
            type=EVT_DELIVERY_CANCELLED,
            payload={
                "assignment_id": seeded["assignment_id"],
                "order_id": seeded["order_id"],
                "rider_id": seeded["rider_id"],
                "from_status": "delivered",
                "to_status": "cancelled",
                "reason": "test reversal",
            },
        )
    await _drain_outbox()

    sm = get_sessionmaker()
    async with sm() as s:
        wallet = (
            await s.execute(
                select(RiderWallet)
                .where(RiderWallet.rider_id == seeded["rider_id"]),
            )
        ).scalar_one()
        # Net of debit + credit → zero.
        assert wallet.cash_in_hand == Decimal("0")
        assert wallet.wallet_payable_to_company == Decimal("0")

        # Ledger has both rows.
        all_rows = (
            await s.execute(
                select(RiderWalletLedger)
                .where(
                    RiderWalletLedger.delivery_assignment_id
                    == seeded["assignment_id"],
                )
                .order_by(RiderWalletLedger.created_at),
            )
        ).scalars().all()
        entry_types = [r.entry_type for r in all_rows]
        assert "cod_collection" in entry_types
        assert "cash_deposit_correction" in entry_types


# ----------------------------------------------------------------------
# Online-paid delivery doesn't touch the wallet
# ----------------------------------------------------------------------
async def test_online_paid_delivery_does_not_touch_wallet() -> None:
    """If payment_method is 'online' and cod_collected is None, the
    handler should return early — no wallet, no ledger row.
    """
    cod = Decimal("0")
    seeded = await _seed_rider_and_order_with_cod(cod_amount=cod)
    # Patch the assignment to look like an online-paid delivery.
    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        await s.execute(
            text(
                "UPDATE delivery_assignments "
                "SET payment_method='online', cod_collected=NULL, "
                "cod_status='n/a' "
                "WHERE id = :id",
            ),
            {"id": seeded["assignment_id"]},
        )

    async with UnitOfWork().transactional():
        await enqueue_outbox(
            type=EVT_DELIVERY_DELIVERED,
            payload={
                "assignment_id": seeded["assignment_id"],
                "order_id": seeded["order_id"],
                "rider_id": seeded["rider_id"],
                "from_status": "picked_up",
                "to_status": "delivered",
                "reason": "online-paid",
            },
        )
    await _drain_outbox()

    async with sm() as s:
        wallet = (
            await s.execute(
                select(RiderWallet)
                .where(RiderWallet.rider_id == seeded["rider_id"]),
            )
        ).scalar_one_or_none()
        # Wallet may or may not exist depending on whether get_or_create
        # was called; but the ledger MUST be empty.
        ledger_rows = (
            await s.execute(
                select(RiderWalletLedger)
                .where(
                    RiderWalletLedger.delivery_assignment_id
                    == seeded["assignment_id"],
                ),
            )
        ).scalars().all()
        assert len(ledger_rows) == 0, (
            "online-paid delivery must not produce a ledger row"
        )
        if wallet is not None:
            assert wallet.cash_in_hand == Decimal("0")
            assert wallet.wallet_payable_to_company == Decimal("0")
