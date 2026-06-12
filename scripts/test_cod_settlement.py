"""E2E COD settlement test.

Rider submits MFS settlement claim for the COD balance, admin verifies,
wallet is drained, ledger entry posted. Idempotent — re-runnable.

Usage: ``python -m scripts.test_cod_settlement``
"""
from __future__ import annotations

import asyncio
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from uuid import uuid4

with open(".env") as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#"):
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

API = "http://127.0.0.1:8000"
RIDER_ID = "c62b740b-4b8f-4597-ad01-12ac4e1875e1"


def _req(method: str, path: str, body=None, token=None):
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        f"{API}{path}",
        data=json.dumps(body).encode() if body else None,
        method=method,
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, None


async def main() -> None:
    from sqlalchemy import text
    from app.core.db.session import get_sessionmaker

    # 1. Rider login
    _, login = _req("POST", "/api/v1/auth/login", {
        "email": "rider@hypershop.dev",
        "password": "RiderTest2026!",
    })
    rider_token = (
        login.get("access_token")
        or (login.get("tokens") or {}).get("access_token")
    )
    print(f"rider login: token len={len(rider_token or '')}")

    # 2. Admin login (for verify)
    _, login = _req("POST", "/api/v1/auth/login", {
        "email": "admin@hypershop.dev",
        "password": "FunnelProd2026!",
    })
    admin_token = (
        login.get("access_token")
        or (login.get("tokens") or {}).get("access_token")
    )
    print(f"admin login: token len={len(admin_token or '')}")

    # 3. Check wallet pre-state
    sm = get_sessionmaker()
    async with sm() as s:
        w = (await s.execute(text(
            "SELECT cash_in_hand, wallet_payable_to_company, "
            "wallet_pending_settlement, wallet_status "
            "FROM rider_wallets WHERE rider_id=:r"
        ), {"r": RIDER_ID})).first()
        print(f"\nPRE wallet: {dict(w._mapping) if w else None}")

    # 4. Rider submits MFS settlement claim for full balance (28550)
    print(f"\n=== Rider submits settlement BDT 28550 (bkash) ===")
    txn_id = f"TX-{uuid4().hex[:10].upper()}"
    code, body = _req("POST", "/api/v1/rider/wallet/settlements", {
        "mfs_provider": "bkash",
        "sender_mfs_number": "+8801911740672",
        "submitted_amount": "28550.00",
        "transaction_id": txn_id,
        "transaction_time": datetime.now(timezone.utc).isoformat(),
    }, token=rider_token)
    print(f"  HTTP={code}")
    if code >= 400:
        print(f"  body: {body}")
        return
    settlement_id = body["id"]
    print(f"  settlement_id: {settlement_id}")
    print(f"  status: {body['status']}")
    print(f"  submitted_amount: {body['submitted_amount']}")

    # 5. Check mid-state — pending should go up
    async with sm() as s:
        w = (await s.execute(text(
            "SELECT cash_in_hand, wallet_payable_to_company, "
            "wallet_pending_settlement, wallet_status "
            "FROM rider_wallets WHERE rider_id=:r"
        ), {"r": RIDER_ID})).first()
        print(f"\nMID wallet (after submit): {dict(w._mapping)}")

    # 6. Admin verifies in full
    print(f"\n=== Admin verifies settlement (full 28550) ===")
    code, body = _req(
        "POST",
        f"/api/v1/admin/rider-wallets/settlements/{settlement_id}/verify",
        {"verified_amount": "28550.00", "review_note": "E2E test verify"},
        token=admin_token,
    )
    print(f"  HTTP={code}")
    if code >= 400:
        print(f"  body: {body}")
        return
    print(f"  status: {body['status']}")
    print(f"  verified_amount: {body['verified_amount']}")

    # 7. Check final wallet — payable should be 0, pending 0
    async with sm() as s:
        w = (await s.execute(text(
            "SELECT cash_in_hand, wallet_payable_to_company, "
            "wallet_pending_settlement, wallet_status "
            "FROM rider_wallets WHERE rider_id=:r"
        ), {"r": RIDER_ID})).first()
        print(f"\nFINAL wallet: {dict(w._mapping)}")
        print()
        print("--- ledger entries ---")
        rows = (await s.execute(text(
            "SELECT entry_type, direction, amount, balance_after, note "
            "FROM rider_wallet_ledger WHERE rider_id=:r "
            "ORDER BY created_at DESC LIMIT 10"
        ), {"r": RIDER_ID})).all()
        for r in rows:
            print(f"  {dict(r._mapping)}")


if __name__ == "__main__":
    asyncio.run(main())
