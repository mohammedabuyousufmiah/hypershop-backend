"""Seed all 12 per-role dev login users for the admin-panel.

LOCAL-DEV ONLY. Each FE page under
``apps/admin-panel/app/[locale]/login/<role>/page.tsx`` pre-fills the
form with one of these (email, password) pairs and posts to
``/api/v1/auth/login``. Without this seed, those pages return 401 because
the user rows don't exist.

Idempotent: re-runs upsert by email, replace the password hash, mark
the account email-verified, purge any existing role assignments, and
attach exactly one role from the canonical catalog. Re-running after
``iam-bootstrap`` is the right way to refresh hashes after a password
rotation.

Usage::

    python -m scripts.seed_dev_logins

Pre-req: run ``hypershop-cli iam-bootstrap`` first so the catalog roles
exist (the script asserts this and bails with a clear error otherwise).

Mapping — keep in sync with the FE login pages:

    /login/super-admin     owner@hypershop.dev        super_admin
    /login/admin           admin@hypershop.dev        admin
    /login/manager         supervisor@hypershop.dev   manager
    /login/staff           staff@hypershop.dev        staff
    /login/dispatcher      superadmin@hypershop.dev   dispatcher
    /login/finance         finance@hypershop.dev      finance_manager
    /login/marketing       marketing@hypershop.dev    marketing_manager
    /login/seller-manager  sellermgr@hypershop.dev    seller_manager
    /login/rider-manager   ridermgr@hypershop.dev     rider_manager
    /login/ai              ai@hypershop.dev           ai_manager
    /login/support         support@hypershop.dev      support_agent
    /login/supervisor      audit@hypershop.dev        supervisor

The two legacy emails (``supervisor@`` for manager, ``superadmin@`` for
dispatcher) are kept for backwards compatibility with existing FE pages
and runbooks; new roles use clean, role-named emails.
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass

from sqlalchemy import text

from app.core.db.session import get_sessionmaker
from app.core.security.passwords import hash_password
from app.core.time import utc_now


@dataclass(frozen=True, slots=True)
class DevLogin:
    email: str
    password: str
    full_name: str
    role_name: str


# Order: legacy 4 first (so re-runs touch the most-used rows first),
# then the 8-role expansion. Email + password are also hard-coded in the
# FE RoleLoginClient props — change in lockstep.
DEV_LOGINS: tuple[DevLogin, ...] = (
    # Legacy 4-tier
    DevLogin("admin@hypershop.dev",       "adminlocal12",       "Dev Admin",         "admin"),
    DevLogin("supervisor@hypershop.dev",  "managerlocal12",     "Dev Manager",       "manager"),
    DevLogin("staff@hypershop.dev",       "stafflocal12",       "Dev Staff",         "staff"),
    DevLogin("superadmin@hypershop.dev",  "dispatchlocal12",    "Dev Dispatcher",    "dispatcher"),
    # Owner / break-glass
    DevLogin("owner@hypershop.dev",       "superadminlocal12",  "Dev Super Admin",   "super_admin"),
    # Functional managers (8-role expansion 2026-05-16)
    DevLogin("audit@hypershop.dev",       "supervisorlocal12",  "Dev Supervisor",    "supervisor"),
    DevLogin("finance@hypershop.dev",     "financelocal12",     "Dev Finance",       "finance_manager"),
    DevLogin("marketing@hypershop.dev",   "marketinglocal12",   "Dev Marketing",     "marketing_manager"),
    DevLogin("sellermgr@hypershop.dev",   "sellermgrlocal12",   "Dev Seller Mgr",    "seller_manager"),
    DevLogin("ridermgr@hypershop.dev",    "ridermgrlocal12",    "Dev Rider Mgr",     "rider_manager"),
    DevLogin("ai@hypershop.dev",          "ailocal12",          "Dev AI Manager",    "ai_manager"),
    DevLogin("support@hypershop.dev",     "supportlocal12",     "Dev Support",       "support_agent"),
    # 15-panel responsibility split (2026-05-29 — 8 new roles)
    DevLogin("director@hypershop.dev",    "directorlocal12",    "Dev Director",      "director"),
    DevLogin("catalogmgr@hypershop.dev",  "catalogmgrlocal12",  "Dev Catalog Mgr",   "catalog_manager"),
    DevLogin("ordermgr@hypershop.dev",    "ordermgrlocal12",    "Dev Order Mgr",     "order_manager"),
    DevLogin("warehousemgr@hypershop.dev","warehousemgrlocal12","Dev Warehouse Mgr", "warehouse_manager"),
    DevLogin("risk@hypershop.dev",        "risklocal12",        "Dev Risk Officer",  "risk_officer"),
    DevLogin("content@hypershop.dev",     "contentlocal12",     "Dev Content Mgr",   "content_manager"),
    DevLogin("analyst@hypershop.dev",     "analystlocal12",     "Dev Data Analyst",  "data_analyst"),
    DevLogin("sysadmin@hypershop.dev",    "sysadminlocal12",    "Dev System Admin",  "system_admin"),
    # CC Lead door (/login/cc-lead) — customercare_admin uses the CC PWA,
    # not the admin shell, but needs a seeded login for the door to work.
    DevLogin("cclead@hypershop.dev",      "ccleadlocal12",      "Dev CC Lead",       "customercare_admin"),
)


async def _seed_one(s, login: DevLogin) -> str:
    """Upsert user, attach exactly the one role. Returns 'created' or 'updated'."""
    email = login.email.lower().strip()
    role_id = (
        await s.execute(text("SELECT id FROM roles WHERE name = :n"), {"n": login.role_name})
    ).scalar_one_or_none()
    if role_id is None:
        raise RuntimeError(
            f"Role {login.role_name!r} missing from DB. "
            "Run `hypershop-cli iam-bootstrap` first."
        )
    existing = (
        await s.execute(text("SELECT id FROM users WHERE email = :e"), {"e": email})
    ).scalar_one_or_none()
    pwd = hash_password(login.password)
    now = utc_now()
    if existing is None:
        user_id = (
            await s.execute(
                text(
                    """
                    INSERT INTO users (
                        email, full_name, password_hash, status, email_verified_at
                    )
                    VALUES (:e, :n, :p, 'active', :t)
                    RETURNING id
                    """
                ),
                {"e": email, "n": login.full_name, "p": pwd, "t": now},
            )
        ).scalar_one()
        outcome = "created"
    else:
        user_id = existing
        await s.execute(
            text(
                """
                UPDATE users
                SET password_hash = :p,
                    full_name = :n,
                    status = 'active',
                    email_verified_at = COALESCE(email_verified_at, :t)
                WHERE id = :id
                """
            ),
            {"p": pwd, "n": login.full_name, "t": now, "id": user_id},
        )
        outcome = "updated"
    # Purge any existing role assignments + reattach exactly one. Stops
    # accidentally accumulating roles across re-runs (which would
    # silently turn a "support" user into a "support + admin" user).
    await s.execute(text("DELETE FROM user_roles WHERE user_id = :u"), {"u": user_id})
    await s.execute(
        text("INSERT INTO user_roles (user_id, role_id) VALUES (:u, :r)"),
        {"u": user_id, "r": role_id},
    )
    return outcome


async def _run() -> int:
    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        results: list[tuple[DevLogin, str]] = []
        for login in DEV_LOGINS:
            outcome = await _seed_one(s, login)
            results.append((login, outcome))
    width = max(len(l.email) for l, _ in results)
    for login, outcome in results:
        print(
            f"  {outcome:<7} {login.email:<{width}}  → role={login.role_name}"
        )
    print(f"\nseed_dev_logins: {len(results)} users in sync with the FE login pages.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_run()))
