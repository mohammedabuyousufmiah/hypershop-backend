"""Seed a demo marketplace seller + assign 10 products to them so the
payout aggregator can be exercised end-to-end.

Idempotent: looked up by slug ``demo-seller``.

Run: ``python -m scripts.seed_seller_demo``
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from uuid import uuid4

with open(".env") as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#"):
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

from sqlalchemy import text  # noqa: E402

from app.core.db.session import get_sessionmaker  # noqa: E402


async def main() -> None:
    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        # Check check-constraint allowed values for status + payout_cadence
        # to avoid the trial-and-error loop we hit with riders.
        rows = (
            await s.execute(
                text(
                    "SELECT pg_get_constraintdef(oid), conname "
                    "FROM pg_constraint WHERE conrelid='sellers'::regclass "
                    "AND contype='c'",
                ),
            )
        ).all()
        for d, n in rows:
            print(f"  constraint {n}: {d}")

        # ---- Create or find the seller ----
        existing = (
            await s.execute(
                text("SELECT id, status FROM sellers WHERE slug = 'demo-seller'"),
            )
        ).first()
        if existing:
            seller_id = existing[0]
            print(f"  seller already exists: {seller_id} status={existing[1]}")
        else:
            seller_id = uuid4()
            await s.execute(
                text(
                    """
                    INSERT INTO sellers
                      (id, business_name, slug, contact_email, contact_phone,
                       status, tin, nid, trade_license_no,
                       bank_account_name, bank_account_number, bank_name,
                       bank_branch, commission_percent, payout_cadence,
                       payout_method, created_at, updated_at)
                    VALUES
                      (:id, :name, :slug, :email, :phone,
                       :status, :tin, :nid, :tl,
                       :bn, :ba, :bk, :br, :comm, :cad, :pm,
                       NOW(), NOW())
                    """,
                ),
                {
                    "id": seller_id,
                    "name": "Demo Seller Bangladesh Ltd",
                    "slug": "demo-seller",
                    "email": "seller@demoseller.bd",
                    "phone": "+8801799000001",
                    "status": "approved",
                    "tin": "1234567890",
                    "nid": "0000111122223",
                    "tl": "DSCC-2026-0001",
                    "bn": "Demo Seller Bangladesh Ltd",
                    "ba": "1234567890123",
                    "bk": "Dutch-Bangla Bank",
                    "br": "Banani Branch",
                    "comm": "15.00",   # 15% commission
                    "cad": "biweekly",
                    "pm": "bank_transfer",
                },
            )
            print(f"  inserted seller: {seller_id}")

        # ---- Assign 10 products to this seller ----
        # Take any 10 active products that currently have NULL seller_id.
        product_ids = [
            r[0]
            for r in (
                await s.execute(
                    text(
                        "SELECT id FROM products WHERE seller_id IS NULL "
                        "AND status = 'active' LIMIT 10",
                    ),
                )
            ).all()
        ]
        if product_ids:
            await s.execute(
                text(
                    "UPDATE products SET seller_id = :sid, updated_at = NOW() "
                    "WHERE id = ANY(:ids)",
                ),
                {"sid": seller_id, "ids": product_ids},
            )
            print(f"  assigned {len(product_ids)} products to demo-seller")
        else:
            print("  no NULL-seller_id products to assign (already seeded?)")

        print()
        print(f"  ✓ ready. Seller id: {seller_id}")


if __name__ == "__main__":
    asyncio.run(main())
