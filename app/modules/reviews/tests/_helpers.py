"""Shared helpers for the reviews test suite.

The verified-purchase requirement makes test setup heavier than
product_videos — each review write needs at least one ``completed``
order containing a variant whose product matches the reviewed
product. We seed orders directly via SQLAlchemy so each test doesn't
have to go through cart → checkout → fulfilment.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from app.core.db.session import get_sessionmaker
from app.modules.catalog.models import Product, ProductStatus, ProductVariant
from app.modules.orders.models import Order, OrderLine


async def seed_product_with_variant() -> tuple[UUID, UUID]:
    """Returns (product_id, variant_id)."""
    sm = get_sessionmaker()
    pid = uuid4()
    vid = uuid4()
    suffix = pid.hex[:8]
    async with sm() as s, s.begin():
        p = Product(
            id=pid,
            slug=f"rev-product-{suffix}",
            name=f"Rev Product {suffix}",
            mother_sku=f"REV-{suffix.upper()}",
            status=ProductStatus.ACTIVE,
            base_currency="BDT",
            tax_class="standard",
            is_medicine=False,
            requires_prescription=False,
        )
        s.add(p)
        await s.flush()
        v = ProductVariant(
            id=vid,
            product_id=pid,
            sku=f"REV-{suffix.upper()}-V1",
            name="default",
            price=Decimal("100.00"),
            currency="BDT",
            is_active=True,
        )
        s.add(v)
    return pid, vid


async def seed_completed_order(
    *,
    customer_user_id: UUID,
    variant_id: UUID,
    completed_at: datetime | None = None,
) -> UUID:
    """Insert a ``completed`` order containing ``variant_id``."""
    sm = get_sessionmaker()
    oid = uuid4()
    when = completed_at or datetime.now(timezone.utc)
    async with sm() as s, s.begin():
        o = Order(
            id=oid,
            customer_user_id=customer_user_id,
            code=f"HS-TEST-{oid.hex[:6].upper()}",
            status="completed",
            currency="BDT",
            subtotal=Decimal("100.00"),
            grand_total=Decimal("100.00"),
            placed_at=when,
            completed_at=when,
        )
        s.add(o)
        await s.flush()
        line = OrderLine(
            order_id=oid,
            variant_id=variant_id,
            product_name="Rev Product",
            variant_sku="REV-X-V1",
            requires_prescription=False,
            quantity=1,
            unit_price=Decimal("100.00"),
            line_total=Decimal("100.00"),
        )
        s.add(line)
    return oid
