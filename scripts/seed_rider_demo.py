"""Seed rider demo data so the COD → delivered → wallet flow can be
end-to-end tested without manually wiring riders/shifts/wallets.

Inserts:
  - 1 rider user (rider@hypershop.dev, role=rider, password set via
    create_superuser sister-script normally; here we just hash & insert).
  - 1 Rider row linked to that user
  - 1 RiderWallet zero-balance row

Idempotent — checks for existing rows by phone/email.
Run: ``python -m scripts.seed_rider_demo``
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
from app.core.security.passwords import hash_password  # noqa: E402
from app.core.time import utc_now  # noqa: E402


async def main() -> None:
    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        # 1. User row
        row = (
            await s.execute(
                text("SELECT id FROM users WHERE email='rider@hypershop.dev'"),
            )
        ).first()
        if row:
            user_id = row[0]
            print(f"  rider user already exists: {user_id}")
        else:
            user_id = (
                await s.execute(
                    text(
                        """
                        INSERT INTO users (
                            email, full_name, password_hash, status, email_verified_at
                        )
                        VALUES (:e, 'Test Rider', :p, 'active', :t)
                        RETURNING id
                        """,
                    ),
                    {
                        "e": "rider@hypershop.dev",
                        "p": hash_password("RiderTest2026!"),
                        "t": utc_now(),
                    },
                )
            ).scalar_one()
            print(f"  inserted rider user: {user_id}")

        # 2. Assign rider role
        rid = (
            await s.execute(text("SELECT id FROM roles WHERE name='rider'"))
        ).scalar_one()
        await s.execute(
            text(
                """
                INSERT INTO user_roles (user_id, role_id)
                VALUES (:u, :r)
                ON CONFLICT DO NOTHING
                """,
            ),
            {"u": user_id, "r": rid},
        )

        # 3. Rider row — discover columns dynamically so we don't crash
        #    on schema drift.
        cols = {
            c[0] for c in (
                await s.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name='riders'",
                    ),
                )
            ).all()
        }
        existing = (
            await s.execute(
                text("SELECT id FROM riders WHERE linked_user_id = :u"),
                {"u": user_id},
            )
        ).first()
        if existing:
            rider_id = existing[0]
            print(f"  rider row already exists: {rider_id}")
        else:
            rider_id = uuid4()
            insert_cols = ["id", "linked_user_id"]
            insert_vals = {"id": rider_id, "linked_user_id": user_id}
            # Required by the riders-table schema (discovered 2026-05-13
            # while running the COD E2E test):
            if "code" in cols:
                insert_cols.append("code")
                insert_vals["code"] = f"RIDER-{str(rider_id)[:8].upper()}"
            if "name" in cols:
                insert_cols.append("name")
                insert_vals["name"] = "Test Rider"
            if "full_name" in cols:
                insert_cols.append("full_name")
                insert_vals["full_name"] = "Test Rider"
            if "phone" in cols:
                insert_cols.append("phone")
                insert_vals["phone"] = "+8801555000001"
            if "vehicle_type" in cols:
                insert_cols.append("vehicle_type")
                # Schema check constraint allows: motorcycle/bicycle/van/other
                insert_vals["vehicle_type"] = "motorcycle"
            if "current_status" in cols:
                insert_cols.append("current_status")
                insert_vals["current_status"] = "offline"
            if "capacity_orders" in cols:
                insert_cols.append("capacity_orders")
                insert_vals["capacity_orders"] = 10
            if "max_cash_limit_bdt" in cols:
                insert_cols.append("max_cash_limit_bdt")
                insert_vals["max_cash_limit_bdt"] = 50000
            if "is_active" in cols:
                insert_cols.append("is_active")
                insert_vals["is_active"] = True
            if "status" in cols:
                insert_cols.append("status")
                insert_vals["status"] = "active"
            if "created_at" in cols:
                insert_cols.append("created_at")
                insert_vals["created_at"] = utc_now()
            if "updated_at" in cols:
                insert_cols.append("updated_at")
                insert_vals["updated_at"] = utc_now()

            placeholder = ", ".join(f":{c}" for c in insert_cols)
            col_list = ", ".join(insert_cols)
            await s.execute(
                text(f"INSERT INTO riders ({col_list}) VALUES ({placeholder})"),
                insert_vals,
            )
            print(f"  inserted rider row: {rider_id}")

        # 4. RiderWallet — same schema-tolerant approach
        wcols = {
            c[0] for c in (
                await s.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name='rider_wallets'",
                    ),
                )
            ).all()
        }
        existing_w = (
            await s.execute(
                text("SELECT id FROM rider_wallets WHERE rider_id = :r"),
                {"r": rider_id},
            )
        ).first()
        if existing_w:
            print(f"  wallet already exists: {existing_w[0]}")
        else:
            insert_cols = ["rider_id"]
            insert_vals = {"rider_id": rider_id}
            if "id" in wcols:
                insert_cols.append("id")
                insert_vals["id"] = uuid4()
            for name in (
                "balance", "balance_minor", "cod_balance",
                "cod_outstanding", "currency", "is_blocked", "status",
                "created_at", "updated_at",
            ):
                if name in wcols and name not in insert_vals:
                    insert_cols.append(name)
                    if name == "currency":
                        insert_vals[name] = "BDT"
                    elif name == "is_blocked":
                        insert_vals[name] = False
                    elif name == "status":
                        insert_vals[name] = "active"
                    elif "_at" in name:
                        insert_vals[name] = utc_now()
                    else:
                        insert_vals[name] = 0
            placeholder = ", ".join(f":{c}" for c in insert_cols)
            col_list = ", ".join(insert_cols)
            await s.execute(
                text(f"INSERT INTO rider_wallets ({col_list}) VALUES ({placeholder})"),
                insert_vals,
            )
            print("  inserted rider_wallet row")

        print()
        print("✓ done. Login: rider@hypershop.dev / RiderTest2026!")


if __name__ == "__main__":
    asyncio.run(main())
