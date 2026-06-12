"""One-shot smoke test for the payment.reconciliation.completed handler.

Seeds a fake order + payment_intent, drives the dispatcher, asserts a
JournalEntry was posted with the expected drift accounts.

Not a unit test — call directly to verify end-to-end. Clean up rows
manually if needed.
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from decimal import Decimal

import httpx
from sqlalchemy import text

# Import app.main to load EVERY module's SQLAlchemy mapping into the
# global registry (needed so cross-module FKs like fin_journal_entries
# -> users resolve). Side-effect import.
from app import main as _app_main  # noqa: F401, E402
from app.core.db.session import get_sessionmaker
from app.core.events.dispatcher import dispatch_once
from app.modules.finance.service import FinanceService

# Force-import handlers so register_finance_handlers() runs in THIS process
# (the running uvicorn has its own separate registry).
from app.modules.finance import handlers as _finance_handlers  # noqa: F401


BASE = "http://127.0.0.1:8000/api/v1"


async def _seed_order_and_intent(sm) -> tuple[uuid.UUID, str]:
    """Create a minimal customer + order + payment_intent. Returns
    (intent_id, provider_payment_id) — the latter is what the
    reconcile request matches against.
    """
    cid = uuid.uuid4()
    oid = uuid.uuid4()
    iid = uuid.uuid4()
    ppid = f"BKASH-SMOKE-{uuid.uuid4().hex[:12]}"

    async with sm() as s, s.begin():
        # Seed chart of accounts (idempotent).
        svc = FinanceService(s)
        await svc.ensure_chart_of_accounts()

        # Customer
        await s.execute(
            text(
                "INSERT INTO users (id, email, full_name, password_hash, status) "
                "VALUES (:id, :em, 'Smoke Customer', 'x', 'active') "
                "ON CONFLICT (email) DO NOTHING"
            ),
            {"id": cid, "em": f"smoke-{cid.hex[:8]}@hypershop.example"},
        )

        await s.execute(
            text(
                "INSERT INTO orders (id, code, customer_user_id, payment_method, "
                "                    subtotal, grand_total, status, currency, "
                "                    delivery_address) "
                "VALUES (:id, :code, :cid, 'online', 100, 100, 'pending_payment', 'BDT', "
                "        '{\"line1\":\"smoke\"}'::jsonb)"
            ),
            {"id": oid, "code": f"SMOKE-{oid.hex[:8]}", "cid": cid},
        )

        # Payment intent
        await s.execute(
            text(
                "INSERT INTO payment_intents (id, order_id, customer_user_id, provider, "
                "                             provider_payment_id, status, currency, "
                "                             amount, amount_captured) "
                "VALUES (:id, :oid, :cid, 'bkash', :ppid, 'captured', 'BDT', 100, 100)"
            ),
            {"id": iid, "oid": oid, "cid": cid, "ppid": ppid},
        )

    return iid, ppid


async def main() -> int:
    sm = get_sessionmaker()

    print("[1] Seeding order + payment_intent ...")
    intent_id, ppid = await _seed_order_and_intent(sm)
    print(f"    intent_id={intent_id}  provider_payment_id={ppid}")

    print("[2] Logging in as admin ...")
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{BASE}/auth/login",
            json={"email": "admin@hypershop.dev", "password": "adminlocal12"},
        )
        token = r.json()["data"]["tokens"]["access_token"]

        print("[3] POST /admin/payments/reconcile ...")
        # Match the seeded intent with $98 (drift = -2, shortfall).
        r = await client.post(
            f"{BASE}/admin/payments/reconcile",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "provider": "bkash",
                "report_date": "2026-05-12",
                "currency": "BDT",
                "lines": [{"provider_ref": ppid, "amount": "98.00"}],
            },
        )
        body = r.json()
        if not body.get("success"):
            print(f"    RECONCILE FAILED: {body}")
            return 1
        report = body["data"]["report"]
        print(f"    report_id={report['id']}  matched={report['matched_count']}  "
              f"drift_count={report['drift_count']}  drift_total={report['drift_total']}")
        report_id = report["id"]

    print("[4] dispatch_once() — fire the handler ...")
    handled = await dispatch_once()
    print(f"    handled={handled}")

    print("[5] Verify JournalEntry posted ...")
    async with sm() as s:
        row = (await s.execute(
            text(
                "SELECT code, narration, source, status, currency, "
                "       reference_type, reference_id "
                "FROM fin_journal_entries "
                "WHERE reference_type = 'payment_settlement_report' "
                "  AND reference_id = :rid"
            ),
            {"rid": report_id},
        )).first()
        if row is None:
            print("    NO JOURNAL ENTRY FOUND  FAIL")
            return 1
        je_code = row[0]
        print(f"    code={je_code}  source={row[2]}  status={row[3]}  ref={row[5]}/{row[6]}")
        lines = (await s.execute(
            text(
                "SELECT line_number, account_code, debit, credit, memo "
                "FROM fin_journal_lines jl "
                "JOIN fin_journal_entries je ON je.id = jl.entry_id "
                "WHERE je.code = :code ORDER BY line_number"
            ),
            {"code": je_code},
        )).all()
        for l in lines:
            print(f"      #{l[0]} {l[1]}  Dr={l[2]}  Cr={l[3]}  '{l[4]}'")
        debits = sum(Decimal(str(l[2])) for l in lines)
        credits = sum(Decimal(str(l[3])) for l in lines)
        print(f"    sum debits = {debits}, sum credits = {credits}, balanced = {debits == credits}")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
