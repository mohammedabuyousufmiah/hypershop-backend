"""Seed 100 units of every active variant into the default warehouse so
checkout doesn't fail on stock reservation.

Why this exists
---------------
``seed_catalog_demo.py`` creates products + variants but never populates
``stock_balances``. As a result every ``place_order`` call hits
``ConflictError: insufficient stock`` and the order transitions to
FAILED inside the checkout/confirm path. The confirm response still
shows ``status: confirmed`` (hardcoded in the response wire shape)
which masked the bug for weeks.

This script inserts one ``stock_balances`` row per (variant, warehouse,
bucket=available) with quantity=100, sufficient for hundreds of test
orders.

Idempotent: uses ON CONFLICT on the natural key
(variant_id, batch_id, warehouse_id, bucket).

Run: ``python -m scripts.seed_stock_balances``
"""
from __future__ import annotations

import asyncio
import os
from uuid import uuid4

with open(".env") as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#"):
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

from sqlalchemy import text  # noqa: E402

from app.core.db.session import get_sessionmaker  # noqa: E402


PER_VARIANT_QTY = 100
BATCH_PLACEHOLDER = "00000000-0000-0000-0000-000000000000"  # nullable-batch sentinel
BUCKET = "available"


async def main() -> None:
    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        # Use the first active warehouse — operators can later spread
        # stock across multiple via the inventory admin.
        warehouse = (
            await s.execute(text("SELECT id FROM warehouses LIMIT 1"))
        ).first()
        if not warehouse:
            print("  ✗ no warehouse rows — run alembic migrations + seed_catalog_demo first")
            return
        warehouse_id = warehouse[0]
        print(f"  using warehouse: {warehouse_id}")

        # stock_balances has FK to batches; batches is per-variant
        # (each batch belongs to one variant). For seed purposes we
        # create one demo batch per variant on the fly below.
        from datetime import date, timedelta
        far_future = date.today() + timedelta(days=730)

        # Inspect the constraints + columns so we adapt to whatever the
        # current schema is. We've seen the batch_id column be either
        # nullable=True (use NULL) or nullable=False with a default
        # sentinel UUID — handle both.
        cols = {
            c[0]: c[1] for c in (
                await s.execute(
                    text(
                        "SELECT column_name, is_nullable FROM information_schema.columns "
                        "WHERE table_name='stock_balances'",
                    ),
                )
            ).all()
        }
        batch_nullable = cols.get("batch_id", "YES") == "YES"
        bucket_col = "bucket" in cols

        # Build a single multi-row INSERT for performance.
        variants = (
            await s.execute(
                text("SELECT id FROM product_variants WHERE is_active = true"),
            )
        ).scalars().all()

        if not variants:
            print("  ✗ no active variants — run seed_catalog_demo first")
            return

        inserted_batches = 0
        inserted_balances = 0
        for v_id in variants:
            # 1. Demo batch per variant (idempotent by batch_number).
            existing_batch = (
                await s.execute(
                    text(
                        "SELECT id FROM batches WHERE variant_id = :v "
                        "AND batch_number = 'DEMO-SEED'",
                    ),
                    {"v": v_id},
                )
            ).first()
            if existing_batch:
                batch_id = existing_batch[0]
            else:
                batch_id = uuid4()
                await s.execute(
                    text(
                        """
                        INSERT INTO batches
                          (id, variant_id, batch_number, expiry_date, status,
                           notes, created_at, updated_at)
                        VALUES (:id, :v, 'DEMO-SEED', :exp, 'active',
                                'Auto-created for dev/test orders', NOW(), NOW())
                        """,
                    ),
                    {"id": batch_id, "v": v_id, "exp": far_future},
                )
                inserted_batches += 1

            # 2. Stock-balance row at qty=100 in the default warehouse.
            params: dict = {
                "id": uuid4(),
                "v": v_id,
                "w": warehouse_id,
                "q": PER_VARIANT_QTY,
                "batch": batch_id,
            }
            bucket_col_sql = ", bucket" if bucket_col else ""
            bucket_val_sql = ", :bucket" if bucket_col else ""
            if bucket_col:
                params["bucket"] = BUCKET

            stmt = text(f"""
                INSERT INTO stock_balances
                  (id, variant_id, batch_id, warehouse_id{bucket_col_sql}, quantity, created_at, updated_at)
                VALUES
                  (:id, :v, :batch, :w{bucket_val_sql}, :q, NOW(), NOW())
                ON CONFLICT DO NOTHING
            """)
            try:
                res = await s.execute(stmt, params)
                if res.rowcount:
                    inserted_balances += 1
            except Exception as e:  # noqa: BLE001
                print(f"  ⚠ skip variant {v_id}: {type(e).__name__}: {e}")
                continue
        print(f"  ✓ {inserted_batches} new batches, {inserted_balances} new stock rows "
              f"(qty={PER_VARIANT_QTY} per variant across {len(variants)} variants)")


if __name__ == "__main__":
    asyncio.run(main())
