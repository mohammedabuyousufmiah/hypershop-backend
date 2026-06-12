"""Seed a dev seller account for the seller-panel.

LOCAL-DEV ONLY. Creates / upserts:

  1. A user ``seller@hypershop.dev`` with password ``sellerlocal12``,
     email-verified, status=active
  2. The ``seller`` role attached to that user
  3. A ``sellers`` row owned by that user with status=APPROVED, so the
     seller-panel's auth gate (which checks status === "APPROVED")
     lets them through
  4. A ``seller_users`` linkage row (role=owner) joining the user to
     the seller row

After this script runs, sign in at the customer-web login form with
the credentials below and navigate to ``http://localhost:3001/bn/seller``
to land on the seller dashboard.

Usage::

    python -m scripts.seed_dev_seller

Pre-req: ``hypershop-cli iam-bootstrap`` must have run so the ``seller``
role exists. Re-running is idempotent — all writes upsert.
"""
from __future__ import annotations

import asyncio
import sys

from sqlalchemy import text

from app.core.db.session import get_sessionmaker
from app.core.security.passwords import hash_password
from app.core.time import utc_now

EMAIL = "seller@hypershop.dev"
PASSWORD = "sellerlocal12"
FULL_NAME = "Dev Seller"
BUSINESS_NAME = "Dev Storefront"
BUSINESS_SLUG = "dev-storefront"
CONTACT_EMAIL = EMAIL
CONTACT_PHONE = "+8801911000000"


async def _run() -> int:
    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        # 1) Ensure the seller role exists.
        role_id = (
            await s.execute(
                text("SELECT id FROM roles WHERE name = 'seller'"),
            )
        ).scalar_one_or_none()
        if role_id is None:
            raise RuntimeError(
                "Role 'seller' missing. Run hypershop-cli iam-bootstrap first."
            )

        # 2) Upsert the user.
        email = EMAIL.lower()
        pwd = hash_password(PASSWORD)
        now = utc_now()
        user_id = (
            await s.execute(
                text("SELECT id FROM users WHERE email = :e"), {"e": email},
            )
        ).scalar_one_or_none()
        if user_id is None:
            user_id = (
                await s.execute(
                    text(
                        """
                        INSERT INTO users (email, full_name, password_hash, status, email_verified_at)
                        VALUES (:e, :n, :p, 'active', :t)
                        RETURNING id
                        """
                    ),
                    {"e": email, "n": FULL_NAME, "p": pwd, "t": now},
                )
            ).scalar_one()
            user_outcome = "created"
        else:
            await s.execute(
                text(
                    """
                    UPDATE users SET
                        password_hash = :p,
                        full_name     = :n,
                        status        = 'active',
                        email_verified_at = COALESCE(email_verified_at, :t)
                    WHERE id = :id
                    """
                ),
                {"p": pwd, "n": FULL_NAME, "t": now, "id": user_id},
            )
            user_outcome = "updated"

        # 3) Attach exactly the seller role (purge prior assignments to
        #    keep the test user clean).
        await s.execute(
            text("DELETE FROM user_roles WHERE user_id = :u"), {"u": user_id},
        )
        await s.execute(
            text("INSERT INTO user_roles (user_id, role_id) VALUES (:u, :r)"),
            {"u": user_id, "r": role_id},
        )

        # 4) Upsert the seller row (by slug) — APPROVED so the auth
        #    gate doesn't bounce them to an onboarding screen.
        seller_id = (
            await s.execute(
                text("SELECT id FROM sellers WHERE slug = :s"),
                {"s": BUSINESS_SLUG},
            )
        ).scalar_one_or_none()
        if seller_id is None:
            seller_id = (
                await s.execute(
                    text(
                        """
                        INSERT INTO sellers (
                            business_name, slug, contact_email, contact_phone,
                            status, commission_percent, payout_cadence, payout_method
                        )
                        VALUES (:bn, :sl, :ce, :cp, 'approved', 10.00, 'weekly', 'bkash')
                        RETURNING id
                        """
                    ),
                    {
                        "bn": BUSINESS_NAME,
                        "sl": BUSINESS_SLUG,
                        "ce": CONTACT_EMAIL,
                        "cp": CONTACT_PHONE,
                    },
                )
            ).scalar_one()
            seller_outcome = "created"
        else:
            await s.execute(
                text(
                    """
                    UPDATE sellers SET
                        business_name = :bn,
                        contact_email = :ce,
                        contact_phone = :cp,
                        status        = 'approved'
                    WHERE id = :id
                    """
                ),
                {
                    "bn": BUSINESS_NAME,
                    "ce": CONTACT_EMAIL,
                    "cp": CONTACT_PHONE,
                    "id": seller_id,
                },
            )
            seller_outcome = "updated"

        # 5) Link user to seller as owner. Phase-1 constraint: one user
        #    can only belong to one seller — wipe any prior linkage first.
        await s.execute(
            text("DELETE FROM seller_users WHERE user_id = :u"), {"u": user_id},
        )
        await s.execute(
            text(
                """
                INSERT INTO seller_users (seller_id, user_id, role)
                VALUES (:s, :u, 'owner')
                """
            ),
            {"s": seller_id, "u": user_id},
        )

    print(f"  {user_outcome:<7} user    {email}  ->  role=seller")
    print(f"  {seller_outcome:<7} seller  {BUSINESS_NAME}  ({BUSINESS_SLUG}, status=approved)")
    print(f"  linked  user -> seller as owner")
    print()
    print("============================================================")
    print(" SELLER LOGIN CREDENTIALS (LOCAL DEV ONLY)")
    print("============================================================")
    print(f"  email:    {EMAIL}")
    print(f"  password: {PASSWORD}")
    print(f"  role:     seller (status=approved)")
    print()
    print("  Sign in:    http://localhost:3000/bn/login")
    print("  Panel:      http://localhost:3001/bn/seller")
    print("============================================================")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_run()))
