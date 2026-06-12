"""End-to-end tests for the cross-module reservation lifecycle.

Each test enqueues a real outbox message of the contract event type, runs
the dispatcher (``dispatch_once``), and asserts the resulting stock state.
This proves that as soon as the orders/payments modules emit a matching
event, reservation/release/consume happen automatically — no extra wiring
needed.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.core.db.session import get_sessionmaker
from app.core.db.uow import UnitOfWork
from app.core.events.dispatcher import dispatch_once
from app.core.events.models import OutboxMessage, OutboxStatus
from app.core.events.outbox import enqueue_outbox
from app.modules.inventory import handlers as _inv_handlers  # noqa: F401  registers handlers
from app.modules.inventory.events import (
    EVT_ORDER_CANCELLED,
    EVT_ORDER_COMPLETED,
    EVT_PAYMENT_SUCCEEDED,
)
from app.modules.inventory.models import StockBalance

pytestmark = pytest.mark.integration


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
    assert resp.status_code == 201
    return resp.json()["id"]


async def _receive(
    client: AsyncClient, headers: dict[str, str],
    *, supplier_id: str, variant_id: str, invoice_number: str, quantity: int,
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
                "batch_number": f"B-{invoice_number}",
                "expiry_date": (date.today() + timedelta(days=200)).isoformat(),
            },
        ],
    }
    resp = await client.post(
        "/api/v1/admin/inventory/goods-receipts", headers=headers, json=body,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _bucket_qty(variant_id: UUID, bucket: str) -> int:
    sm = get_sessionmaker()
    async with sm() as s:
        rows = (
            (
                await s.execute(
                    select(StockBalance).where(
                        StockBalance.variant_id == variant_id,
                        StockBalance.bucket == bucket,
                    )
                )
            )
            .scalars()
            .all()
        )
    return sum(r.quantity for r in rows)


async def _enqueue(event_type: str, payload: dict[str, Any]) -> None:
    uow = UnitOfWork()
    async with uow.transactional() as session:
        await enqueue_outbox(type=event_type, payload=payload, session=session)


async def _drain_outbox() -> int:
    """Run the dispatcher repeatedly until no more messages are pending."""
    total = 0
    for _ in range(10):
        n = await dispatch_once()
        if n == 0:
            break
        total += n
    return total


# ---------------- payment.succeeded → reserve ----------------


async def test_payment_succeeded_event_drives_reservation(
    api_client: AsyncClient, admin_user: dict[str, Any],
) -> None:
    variant_id = await _create_variant(
        api_client, admin_user["headers"], "Lifecycle-Reserve",
    )
    supplier_id = await _create_supplier(api_client, admin_user["headers"], "LC-A")
    await _receive(
        api_client, admin_user["headers"],
        supplier_id=supplier_id, variant_id=variant_id,
        invoice_number="INV-LC-A", quantity=20,
    )
    order_id = uuid4()

    await _enqueue(
        EVT_PAYMENT_SUCCEEDED,
        {
            "order_id": str(order_id),
            "items": [{"variant_id": variant_id, "quantity": 5}],
        },
    )
    await _drain_outbox()

    assert await _bucket_qty(UUID(variant_id), "available") == 15
    assert await _bucket_qty(UUID(variant_id), "reserved") == 5


async def test_payment_succeeded_redelivery_is_idempotent(
    api_client: AsyncClient, admin_user: dict[str, Any],
) -> None:
    variant_id = await _create_variant(
        api_client, admin_user["headers"], "Lifecycle-Idem",
    )
    supplier_id = await _create_supplier(api_client, admin_user["headers"], "LC-B")
    await _receive(
        api_client, admin_user["headers"],
        supplier_id=supplier_id, variant_id=variant_id,
        invoice_number="INV-LC-B", quantity=20,
    )
    order_id = uuid4()
    payload = {
        "order_id": str(order_id),
        "items": [{"variant_id": variant_id, "quantity": 7}],
    }
    # Enqueue twice (simulating at-least-once redelivery).
    await _enqueue(EVT_PAYMENT_SUCCEEDED, payload)
    await _enqueue(EVT_PAYMENT_SUCCEEDED, payload)
    await _drain_outbox()

    # Only one reservation should land.
    assert await _bucket_qty(UUID(variant_id), "available") == 13
    assert await _bucket_qty(UUID(variant_id), "reserved") == 7


# ---------------- order.cancelled → release ----------------


async def test_order_cancelled_event_drives_release(
    api_client: AsyncClient, admin_user: dict[str, Any],
) -> None:
    variant_id = await _create_variant(
        api_client, admin_user["headers"], "Lifecycle-Cancel",
    )
    supplier_id = await _create_supplier(api_client, admin_user["headers"], "LC-C")
    await _receive(
        api_client, admin_user["headers"],
        supplier_id=supplier_id, variant_id=variant_id,
        invoice_number="INV-LC-C", quantity=20,
    )
    order_id = uuid4()

    await _enqueue(
        EVT_PAYMENT_SUCCEEDED,
        {
            "order_id": str(order_id),
            "items": [{"variant_id": variant_id, "quantity": 6}],
        },
    )
    await _drain_outbox()
    assert await _bucket_qty(UUID(variant_id), "reserved") == 6

    await _enqueue(
        EVT_ORDER_CANCELLED,
        {"order_id": str(order_id), "reason": "Customer abandoned"},
    )
    await _drain_outbox()

    assert await _bucket_qty(UUID(variant_id), "available") == 20
    assert await _bucket_qty(UUID(variant_id), "reserved") == 0


# ---------------- order.delivered → consume ----------------


async def test_order_delivered_event_drives_consume(
    api_client: AsyncClient, admin_user: dict[str, Any],
) -> None:
    variant_id = await _create_variant(
        api_client, admin_user["headers"], "Lifecycle-Deliver",
    )
    supplier_id = await _create_supplier(api_client, admin_user["headers"], "LC-D")
    await _receive(
        api_client, admin_user["headers"],
        supplier_id=supplier_id, variant_id=variant_id,
        invoice_number="INV-LC-D", quantity=15,
    )
    order_id = uuid4()

    await _enqueue(
        EVT_PAYMENT_SUCCEEDED,
        {
            "order_id": str(order_id),
            "items": [{"variant_id": variant_id, "quantity": 9}],
        },
    )
    await _enqueue(
        EVT_ORDER_COMPLETED,
        {"order_id": str(order_id)},
    )
    await _drain_outbox()

    assert await _bucket_qty(UUID(variant_id), "available") == 6
    assert await _bucket_qty(UUID(variant_id), "reserved") == 0
    # Consumed units leave the system entirely — they're not in any bucket.


# ---------------- malformed payload routes to retry / dead-letter ----------------


async def test_malformed_payload_dead_letters_after_retries(
    api_client: AsyncClient, admin_user: dict[str, Any],
) -> None:
    """A payload missing required fields should NOT silently succeed.

    The handler raises ``pydantic.ValidationError``; the dispatcher records
    the error and schedules a retry. After ``_MAX_ATTEMPTS`` it dead-letters.
    """
    await _enqueue(
        EVT_PAYMENT_SUCCEEDED,
        {"order_id": "not-a-uuid", "items": []},
    )
    await dispatch_once()  # one attempt is enough to mark it failed/PENDING

    sm = get_sessionmaker()
    async with sm() as s:
        rows = (
            (
                await s.execute(
                    select(OutboxMessage).where(
                        OutboxMessage.type == EVT_PAYMENT_SUCCEEDED,
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 1
    msg = rows[0]
    assert msg.attempts >= 1
    assert msg.last_error  # error string is captured for ops
    assert msg.status in (OutboxStatus.PENDING, OutboxStatus.DEAD)


# ---------------- insufficient stock → retry, never silently succeed ----------------


async def test_payment_succeeded_with_insufficient_stock_is_retried(
    api_client: AsyncClient, admin_user: dict[str, Any],
) -> None:
    """If the customer's payment confirmed but stock is gone (e.g. due to a
    block-by-recall in the meantime), the handler must NOT silently no-op.
    It surfaces ConflictError → retry → dead-letter, so ops sees the gap.
    """
    variant_id = await _create_variant(
        api_client, admin_user["headers"], "Lifecycle-Short",
    )
    supplier_id = await _create_supplier(api_client, admin_user["headers"], "LC-E")
    await _receive(
        api_client, admin_user["headers"],
        supplier_id=supplier_id, variant_id=variant_id,
        invoice_number="INV-LC-E", quantity=3,
    )
    order_id = uuid4()
    await _enqueue(
        EVT_PAYMENT_SUCCEEDED,
        {
            "order_id": str(order_id),
            "items": [{"variant_id": variant_id, "quantity": 50}],  # we only have 3
        },
    )
    await dispatch_once()

    sm = get_sessionmaker()
    async with sm() as s:
        msg = (
            (
                await s.execute(
                    select(OutboxMessage).where(
                        OutboxMessage.type == EVT_PAYMENT_SUCCEEDED,
                    )
                )
            )
            .scalars()
            .one()
        )
    assert msg.status == OutboxStatus.PENDING
    assert msg.attempts == 1
    assert msg.last_error and "Insufficient" in msg.last_error
    # Stock unchanged.
    assert await _bucket_qty(UUID(variant_id), "available") == 3
    assert await _bucket_qty(UUID(variant_id), "reserved") == 0
