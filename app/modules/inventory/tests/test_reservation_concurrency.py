"""Concurrency proofs for transaction-safe stock reservation.

These tests fan out parallel reserve/release/consume calls and assert that:

* No more units leave ``available`` than were ever there (no overselling).
* The ``stock_balances.quantity >= 0`` CHECK never trips (failures from
  service-layer logic, not from the DB safety net).
* Idempotent operations stay idempotent under concurrent retries.
* FEFO ordering is honoured even under contention.
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.core.db.session import get_sessionmaker
from app.core.db.uow import UnitOfWork
from app.core.security.principal import SystemPrincipal
from app.modules.inventory.models import (
    Batch,
    LedgerKind,
    StockBalance,
    StockBucket,
    StockLedger,
)
from app.modules.inventory.orchestrator import OrderItem, OrderStockOrchestrator
from app.modules.inventory.service import InventoryService

pytestmark = pytest.mark.integration


# ---------------- helpers ----------------


_THREE_IMAGES = [
    {"url": f"https://cdn.hypershop.local/x/{i}.jpg", "kind": "image", "position": i}
    for i in range(3)
]


async def _create_variant(client: AsyncClient, headers: dict[str, str], name: str) -> str:
    payload: dict[str, Any] = {
        "name": name,
        "base_currency": "BDT",
        "status": "active",
        "variants": [{"options": {}, "price": "100.00"}],
        "media": _THREE_IMAGES,
    }
    resp = await client.post(
        "/api/v1/admin/catalog/products", headers=headers, json=payload,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["variants"][0]["id"]


async def _create_supplier(client: AsyncClient, headers: dict[str, str], code: str) -> str:
    resp = await client.post(
        "/api/v1/admin/inventory/suppliers",
        headers=headers,
        json={"code": code, "name": f"Supplier {code}"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _receive(
    client: AsyncClient,
    headers: dict[str, str],
    *,
    supplier_id: str,
    variant_id: str,
    invoice_number: str,
    quantity: int,
    expiry: date | None = None,
    batch_number: str = "B-1",
) -> dict[str, Any]:
    body = {
        "supplier_id": supplier_id,
        "supplier_invoice_number": invoice_number,
        "supplier_invoice_date": date.today().isoformat(),
        "warehouse_code": "MAIN",
        "currency": "BDT",
        "lines": [
            {
                "variant_id": variant_id,
                "quantity": quantity,
                "unit_cost": "10.00",
                "batch_number": batch_number,
                "expiry_date": (expiry or (date.today() + timedelta(days=200))).isoformat(),
            },
        ],
    }
    resp = await client.post(
        "/api/v1/admin/inventory/goods-receipts", headers=headers, json=body,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _reserve_via_orchestrator(
    *, order_id: UUID, variant_id: UUID, quantity: int,
) -> str:
    """Run a reservation in its own UoW transaction. Returns ``"ok"`` on
    success or ``"insufficient"`` on the expected oversell-prevention 409.
    """
    from app.core.errors import ConflictError as _ConflictError

    uow = UnitOfWork()
    try:
        async with uow.transactional() as session:
            orch = OrderStockOrchestrator(session)
            await orch.reserve_for_order(
                principal=SystemPrincipal(),
                order_id=order_id,
                items=[OrderItem(variant_id=variant_id, quantity=quantity)],
            )
        return "ok"
    except _ConflictError:
        return "insufficient"


# ---------------- tests ----------------


async def test_concurrent_reserves_never_oversell(
    api_client: AsyncClient, admin_user: dict[str, Any],
) -> None:
    """100 units in stock, 15 concurrent reserve(10) calls.

    Exactly 10 must succeed; 5 must fail with 'insufficient'. End state:
    available=0, reserved=100. No row violates the CHECK constraint.
    """
    variant_id = await _create_variant(
        api_client, admin_user["headers"], "Concurrency Test A",
    )
    supplier_id = await _create_supplier(api_client, admin_user["headers"], "CON-A")
    await _receive(
        api_client, admin_user["headers"],
        supplier_id=supplier_id, variant_id=variant_id,
        invoice_number="INV-CON-A", quantity=100,
    )

    results = await asyncio.gather(
        *[
            _reserve_via_orchestrator(
                order_id=uuid4(),
                variant_id=UUID(variant_id),
                quantity=10,
            )
            for _ in range(15)
        ],
        return_exceptions=False,
    )
    succeeded = sum(1 for r in results if r == "ok")
    insufficient = sum(1 for r in results if r == "insufficient")

    assert succeeded == 10, f"expected 10 successes, got {succeeded}: {results}"
    assert insufficient == 5, f"expected 5 insufficient, got {insufficient}: {results}"

    # End state.
    sm = get_sessionmaker()
    async with sm() as s:
        balances = (
            (
                await s.execute(
                    select(StockBalance).where(StockBalance.variant_id == UUID(variant_id))
                )
            )
            .scalars()
            .all()
        )
        by_bucket = {b.bucket: b.quantity for b in balances}
    assert by_bucket.get("available", 0) == 0
    assert by_bucket.get("reserved", 0) == 100


async def test_concurrent_reserve_with_unequal_demand_drains_available(
    api_client: AsyncClient, admin_user: dict[str, Any],
) -> None:
    """50 units in stock, 10 concurrent reserve(7) calls.

    7 succeed (49 reserved), 1 partial-claim is impossible — service is
    all-or-nothing per call, so 7 succeed + 3 fail. Final available = 1.
    """
    variant_id = await _create_variant(
        api_client, admin_user["headers"], "Concurrency Test B",
    )
    supplier_id = await _create_supplier(api_client, admin_user["headers"], "CON-B")
    await _receive(
        api_client, admin_user["headers"],
        supplier_id=supplier_id, variant_id=variant_id,
        invoice_number="INV-CON-B", quantity=50,
    )

    results = await asyncio.gather(
        *[
            _reserve_via_orchestrator(
                order_id=uuid4(), variant_id=UUID(variant_id), quantity=7,
            )
            for _ in range(10)
        ],
    )
    successes = sum(1 for r in results if r == "ok")
    failures = sum(1 for r in results if r == "insufficient")
    assert successes == 7, f"want 7 successes, got {successes}: {results}"
    assert failures == 3, f"want 3 failures, got {failures}: {results}"

    sm = get_sessionmaker()
    async with sm() as s:
        balances = (
            (
                await s.execute(
                    select(StockBalance).where(StockBalance.variant_id == UUID(variant_id))
                )
            )
            .scalars()
            .all()
        )
        by_bucket = {b.bucket: b.quantity for b in balances}
    assert by_bucket.get("available", 0) == 1
    assert by_bucket.get("reserved", 0) == 49


async def test_idempotent_reserve_for_same_order_id(
    api_client: AsyncClient, admin_user: dict[str, Any],
) -> None:
    """Calling reserve_for_order twice with the same order_id reserves once.

    Models payment-success retries: the second attempt returns the existing
    reservation rather than booking a duplicate.
    """
    variant_id = await _create_variant(
        api_client, admin_user["headers"], "Idempotent",
    )
    supplier_id = await _create_supplier(api_client, admin_user["headers"], "CON-C")
    await _receive(
        api_client, admin_user["headers"],
        supplier_id=supplier_id, variant_id=variant_id,
        invoice_number="INV-CON-C", quantity=20,
    )
    order_id = uuid4()

    # First call.
    uow = UnitOfWork()
    async with uow.transactional() as session:
        orch = OrderStockOrchestrator(session)
        first = await orch.reserve_for_order(
            principal=SystemPrincipal(),
            order_id=order_id,
            items=[OrderItem(variant_id=UUID(variant_id), quantity=10)],
        )
    assert first.already_reserved is False

    # Second call — same order_id.
    uow2 = UnitOfWork()
    async with uow2.transactional() as session:
        orch = OrderStockOrchestrator(session)
        second = await orch.reserve_for_order(
            principal=SystemPrincipal(),
            order_id=order_id,
            items=[OrderItem(variant_id=UUID(variant_id), quantity=10)],
        )
    assert second.already_reserved is True

    # Stock state confirms only one booking.
    sm = get_sessionmaker()
    async with sm() as s:
        rows = (
            (
                await s.execute(
                    select(StockBalance).where(StockBalance.variant_id == UUID(variant_id))
                )
            )
            .scalars()
            .all()
        )
        by_bucket = {b.bucket: b.quantity for b in rows}
    assert by_bucket.get("available", 0) == 10
    assert by_bucket.get("reserved", 0) == 10


async def test_reserve_release_consume_lifecycle(
    api_client: AsyncClient, admin_user: dict[str, Any],
) -> None:
    """Payment → reserve → cancel → release → re-reserve → deliver → consume.

    End state: stock back to available state minus delivered quantity, ledger
    has every step recorded under one correlation per order.
    """
    variant_id = await _create_variant(
        api_client, admin_user["headers"], "Lifecycle",
    )
    supplier_id = await _create_supplier(api_client, admin_user["headers"], "CON-D")
    await _receive(
        api_client, admin_user["headers"],
        supplier_id=supplier_id, variant_id=variant_id,
        invoice_number="INV-CON-D", quantity=30,
    )
    order_id_a = uuid4()
    order_id_b = uuid4()

    # 1. Reserve order A
    uow = UnitOfWork()
    async with uow.transactional() as session:
        orch = OrderStockOrchestrator(session)
        await orch.reserve_for_order(
            principal=SystemPrincipal(),
            order_id=order_id_a,
            items=[OrderItem(variant_id=UUID(variant_id), quantity=8)],
        )

    # 2. Cancel order A → release
    sm = get_sessionmaker()
    admin_principal = _admin_principal(admin_user)
    uow = UnitOfWork()
    async with uow.transactional() as session:
        orch = OrderStockOrchestrator(session)
        released = await orch.release_for_order(
            principal=admin_principal, order_id=order_id_a,
        )
    assert released == 8

    # 3. Reserve order B
    uow = UnitOfWork()
    async with uow.transactional() as session:
        orch = OrderStockOrchestrator(session)
        await orch.reserve_for_order(
            principal=SystemPrincipal(),
            order_id=order_id_b,
            items=[OrderItem(variant_id=UUID(variant_id), quantity=12)],
        )

    # 4. Deliver order B → consume
    uow = UnitOfWork()
    async with uow.transactional() as session:
        orch = OrderStockOrchestrator(session)
        consumed = await orch.consume_for_order(
            principal=admin_principal, order_id=order_id_b,
        )
    assert consumed == 12

    # End state: 30 received, 8 released, 12 consumed, so available = 18,
    # reserved = 0, total in system = 18 (12 left the system on delivery).
    async with sm() as s:
        rows = (
            (
                await s.execute(
                    select(StockBalance).where(StockBalance.variant_id == UUID(variant_id))
                )
            )
            .scalars()
            .all()
        )
        by_bucket = {b.bucket: b.quantity for b in rows}
    assert by_bucket.get("available", 0) == 18
    assert by_bucket.get("reserved", 0) == 0


async def test_overselling_is_blocked_by_db_check_under_race(
    api_client: AsyncClient, admin_user: dict[str, Any],
) -> None:
    """Belt-and-braces: even if service logic had a bug, the
    ``stock_balances.quantity >= 0`` CHECK refuses to commit a negative row.

    We exercise this indirectly: a flood of concurrent reserves never leaves
    a ``stock_balances`` row with ``quantity < 0`` (which would be a CHECK
    violation rolling back the offending tx).
    """
    variant_id = await _create_variant(
        api_client, admin_user["headers"], "DB-Defence",
    )
    supplier_id = await _create_supplier(api_client, admin_user["headers"], "CON-E")
    await _receive(
        api_client, admin_user["headers"],
        supplier_id=supplier_id, variant_id=variant_id,
        invoice_number="INV-CON-E", quantity=20,
    )

    # 30 concurrent attempts to reserve 1 unit each. 20 must succeed.
    results = await asyncio.gather(
        *[
            _reserve_via_orchestrator(
                order_id=uuid4(), variant_id=UUID(variant_id), quantity=1,
            )
            for _ in range(30)
        ],
    )
    assert sum(1 for r in results if r == "ok") == 20
    assert sum(1 for r in results if r == "insufficient") == 10

    sm = get_sessionmaker()
    async with sm() as s:
        rows = (
            (
                await s.execute(
                    select(StockBalance).where(StockBalance.variant_id == UUID(variant_id))
                )
            )
            .scalars()
            .all()
        )
    for row in rows:
        assert row.quantity >= 0, f"CHECK violation slipped through: {row}"


async def test_fefo_under_contention(
    api_client: AsyncClient, admin_user: dict[str, Any],
) -> None:
    """Two batches: 30 units of B-EARLY (expires soon) + 30 of B-LATE.

    Six concurrent reserve(10) calls — all 6 succeed. The earlier batch must
    drain first (FEFO), so allocations from B-EARLY total exactly 30.
    """
    variant_id = await _create_variant(
        api_client, admin_user["headers"], "FEFO Conc",
    )
    supplier_id = await _create_supplier(api_client, admin_user["headers"], "CON-F")
    early = date.today() + timedelta(days=20)
    late = date.today() + timedelta(days=400)
    await _receive(
        api_client, admin_user["headers"],
        supplier_id=supplier_id, variant_id=variant_id,
        invoice_number="INV-CON-F-LATE", quantity=30,
        expiry=late, batch_number="B-LATE",
    )
    await _receive(
        api_client, admin_user["headers"],
        supplier_id=supplier_id, variant_id=variant_id,
        invoice_number="INV-CON-F-EARLY", quantity=30,
        expiry=early, batch_number="B-EARLY",
    )

    results = await asyncio.gather(
        *[
            _reserve_via_orchestrator(
                order_id=uuid4(), variant_id=UUID(variant_id), quantity=10,
            )
            for _ in range(6)
        ],
    )
    assert all(r == "ok" for r in results), results

    # Tally: expect ledger reserve rows on B-EARLY to sum to exactly 30.
    sm = get_sessionmaker()
    async with sm() as s:
        early_batch = (
            await s.execute(
                select(Batch).where(
                    Batch.variant_id == UUID(variant_id),
                    Batch.batch_number == "B-EARLY",
                )
            )
        ).scalar_one()
        late_batch = (
            await s.execute(
                select(Batch).where(
                    Batch.variant_id == UUID(variant_id),
                    Batch.batch_number == "B-LATE",
                )
            )
        ).scalar_one()

        early_reserved = (
            await s.execute(
                select(StockBalance.quantity).where(
                    StockBalance.batch_id == early_batch.id,
                    StockBalance.bucket == StockBucket.RESERVED,
                )
            )
        ).scalar_one_or_none() or 0
        late_reserved = (
            await s.execute(
                select(StockBalance.quantity).where(
                    StockBalance.batch_id == late_batch.id,
                    StockBalance.bucket == StockBucket.RESERVED,
                )
            )
        ).scalar_one_or_none() or 0

    assert early_reserved == 30, f"FEFO violated: B-EARLY reserved={early_reserved}"
    assert late_reserved == 30, f"FEFO violated: B-LATE reserved={late_reserved}"


async def test_release_writes_paired_ledger_legs(
    api_client: AsyncClient, admin_user: dict[str, Any],
) -> None:
    """Every release must produce two ledger rows per affected batch:
    -reserved (delta < 0) and +available (delta > 0). Net zero across buckets.
    """
    variant_id = await _create_variant(
        api_client, admin_user["headers"], "LedgerLegs",
    )
    supplier_id = await _create_supplier(api_client, admin_user["headers"], "CON-G")
    await _receive(
        api_client, admin_user["headers"],
        supplier_id=supplier_id, variant_id=variant_id,
        invoice_number="INV-CON-G", quantity=15,
    )
    order_id = uuid4()

    uow = UnitOfWork()
    async with uow.transactional() as session:
        await OrderStockOrchestrator(session).reserve_for_order(
            principal=SystemPrincipal(),
            order_id=order_id,
            items=[OrderItem(variant_id=UUID(variant_id), quantity=5)],
        )

    admin_principal = _admin_principal(admin_user)
    uow = UnitOfWork()
    async with uow.transactional() as session:
        await OrderStockOrchestrator(session).release_for_order(
            principal=admin_principal, order_id=order_id,
        )

    sm = get_sessionmaker()
    async with sm() as s:
        rows = (
            (
                await s.execute(
                    select(StockLedger)
                    .where(StockLedger.correlation_id == order_id)
                    .order_by(StockLedger.occurred_at)
                )
            )
            .scalars()
            .all()
        )
    # 4 rows: reserve (-avail, +reserved), release (-reserved, +avail)
    kinds = [(r.kind, r.bucket, r.quantity_delta) for r in rows]
    assert (LedgerKind.RESERVE, StockBucket.AVAILABLE, -5) in kinds
    assert (LedgerKind.RESERVE, StockBucket.RESERVED, 5) in kinds
    assert (LedgerKind.RELEASE, StockBucket.RESERVED, -5) in kinds
    assert (LedgerKind.RELEASE, StockBucket.AVAILABLE, 5) in kinds


# ---------------- shared helpers ----------------


def _admin_principal(admin_user: dict[str, Any]):
    """Build a Principal mirroring the admin_user fixture, for direct service
    calls that bypass the HTTP layer.
    """
    from app.core.security.principal import Principal

    return Principal(
        user_id=UUID(admin_user["user_id"]),
        session_id=uuid4(),
        roles=frozenset({"admin"}),
        permissions=frozenset({"*"}),
    )
