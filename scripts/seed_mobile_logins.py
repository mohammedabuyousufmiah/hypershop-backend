"""Seed DEFAULT mobile-app demo logins (idempotent).

Creates two ready-to-use accounts so every Hypershop mobile app can be
logged into out of the box:

  • Customer apps (customer-android / customer-hms / customer-ios)
        login: email + password  ->  POST /api/v1/auth/login
        customer@hypershop.dev / Customer@Local12   (phone +8801700000001)

  • Rider apps (rider-android / rider-hms)
        login: phone + OTP  ->  /api/v1/auth/otp/request-sms + /verify-sms
        rider@hypershop.dev / Rider@Local12          (phone +8801700000002)
        The rider also gets a password (same /auth/login works if the app
        exposes a password field). Phone OTP needs OTP_DEV_BYPASS=true on
        the target backend (any 6-digit code is then accepted) OR a real
        SMS provider.

Both users are created active + email/phone verified, with their role
assigned, and the rider gets a riders + rider_wallets row. Re-running
normalises existing rows back to these exact credentials.

Run inside the api container:
    python -m scripts.seed_mobile_logins
"""
from __future__ import annotations

import asyncio
import os
from uuid import uuid4

# Load .env if present (no-op in container where env is already injected).
if os.path.exists(".env"):
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

CUSTOMER = {
    "email": "customer@hypershop.dev",
    "full_name": "Demo Customer",
    "password": "Customer@Local12",
    "phone": "+8801700000001",
    "role": "customer",
}
RIDER = {
    "email": "rider@hypershop.dev",
    "full_name": "Demo Rider",
    "password": "Rider@Local12",
    "phone": "+8801700000002",
    "role": "rider",
}


async def _upsert_user(s, spec: dict, now) -> str:
    # Free the phone from any other holder so the unique constraint can't
    # block this normalisation.
    await s.execute(
        text("UPDATE users SET phone=NULL WHERE phone=:ph AND lower(email)<>:e"),
        {"ph": spec["phone"], "e": spec["email"].lower()},
    )
    row = (
        await s.execute(
            text("SELECT id FROM users WHERE lower(email)=:e"),
            {"e": spec["email"].lower()},
        )
    ).first()
    pwd = hash_password(spec["password"])
    if row:
        uid = row[0]
        await s.execute(
            text(
                """
                UPDATE users SET
                    password_hash=:p, phone=:ph, full_name=:fn, status='active',
                    email_verified_at=COALESCE(email_verified_at, :t),
                    phone_verified_at=COALESCE(phone_verified_at, :t),
                    locked_until=NULL
                WHERE id=:u
                """
            ),
            {"p": pwd, "ph": spec["phone"], "fn": spec["full_name"], "t": now, "u": uid},
        )
        print(f"  updated user {spec['email']} -> {uid}")
    else:
        uid = (
            await s.execute(
                text(
                    """
                    INSERT INTO users (
                        email, full_name, password_hash, phone, status,
                        email_verified_at, phone_verified_at
                    )
                    VALUES (:e, :fn, :p, :ph, 'active', :t, :t)
                    RETURNING id
                    """
                ),
                {
                    "e": spec["email"], "fn": spec["full_name"], "p": pwd,
                    "ph": spec["phone"], "t": now,
                },
            )
        ).scalar_one()
        print(f"  inserted user {spec['email']} -> {uid}")

    # Assign role (best-effort — only if the role exists).
    rid = (
        await s.execute(
            text("SELECT id FROM roles WHERE name=:n"), {"n": spec["role"]}
        )
    ).first()
    if rid:
        await s.execute(
            text(
                "INSERT INTO user_roles (user_id, role_id) VALUES (:u, :r) "
                "ON CONFLICT DO NOTHING"
            ),
            {"u": uid, "r": rid[0]},
        )
    return uid


async def _ensure_rider_rows(s, user_id, phone, now) -> None:
    cols = {
        c[0]
        for c in (
            await s.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name='riders'"
                )
            )
        ).all()
    }
    existing = (
        await s.execute(
            text("SELECT id FROM riders WHERE linked_user_id = :u"), {"u": user_id}
        )
    ).first()
    if existing:
        rider_id = existing[0]
        print(f"  rider row exists -> {rider_id}")
    else:
        rider_id = uuid4()
        ic = ["id", "linked_user_id"]
        iv = {"id": rider_id, "linked_user_id": user_id}
        defaults = {
            "code": f"RIDER-{str(rider_id)[:8].upper()}",
            "name": "Demo Rider", "full_name": "Demo Rider", "phone": phone,
            "vehicle_type": "motorcycle", "current_status": "offline",
            "capacity_orders": 10, "max_cash_limit_bdt": 50000,
            "is_active": True, "status": "active",
            "created_at": now, "updated_at": now,
        }
        for k, v in defaults.items():
            if k in cols:
                ic.append(k)
                iv[k] = v
        await s.execute(
            text(f"INSERT INTO riders ({', '.join(ic)}) VALUES ({', '.join(':'+c for c in ic)})"),
            iv,
        )
        print(f"  inserted rider row -> {rider_id}")

    wcols = {
        c[0]
        for c in (
            await s.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name='rider_wallets'"
                )
            )
        ).all()
    }
    if not wcols:
        return
    if (
        await s.execute(
            text("SELECT id FROM rider_wallets WHERE rider_id = :r"), {"r": rider_id}
        )
    ).first():
        print("  rider_wallet exists")
        return
    ic = ["rider_id"]
    iv = {"rider_id": rider_id}
    if "id" in wcols:
        ic.append("id"); iv["id"] = uuid4()
    for name in ("balance", "balance_minor", "cod_balance", "cod_outstanding",
                 "currency", "is_blocked", "status", "created_at", "updated_at"):
        if name in wcols and name not in iv:
            ic.append(name)
            iv[name] = ("BDT" if name == "currency" else False if name == "is_blocked"
                        else "active" if name == "status" else now if "_at" in name else 0)
    await s.execute(
        text(f"INSERT INTO rider_wallets ({', '.join(ic)}) VALUES ({', '.join(':'+c for c in ic)})"),
        iv,
    )
    print("  inserted rider_wallet row")


async def main() -> None:
    sm = get_sessionmaker()
    now = utc_now()
    async with sm() as s, s.begin():
        print("Customer:")
        await _upsert_user(s, CUSTOMER, now)
        print("Rider:")
        rider_uid = await _upsert_user(s, RIDER, now)
        await _ensure_rider_rows(s, rider_uid, RIDER["phone"], now)
    print()
    print("=" * 56)
    print("DEFAULT MOBILE LOGINS SEEDED")
    print(f"  Customer apps : {CUSTOMER['email']} / {CUSTOMER['password']}")
    print(f"                  (phone {CUSTOMER['phone']})")
    print(f"  Rider apps    : phone {RIDER['phone']}  + OTP (any 6 digits if")
    print(f"                  OTP_DEV_BYPASS=true)  |  {RIDER['email']} / {RIDER['password']}")
    print("=" * 56)


if __name__ == "__main__":
    asyncio.run(main())
