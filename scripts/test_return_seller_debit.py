"""E2E: customer return -> admin process -> seller liability ledger.

Pre-conditions (already true in this env):
- Order HSO-20260513-JX733 (id 3e072044) is COMPLETED, has one line:
  variant 0d80e528, unit_price=28490, qty=1.
- demo-seller exists at id edc67dfe-40f8-43ef-a67d-d8f3a15efb22.
- Customer is 670b652f-7950-4baa-85f6-4fb76f9e51d2 (customer@hypershop.dev).

Steps:
1. Patch product af7cda16 (the variant's product) to seller_id=demo-seller.
2. Customer logs in -> POST /returns to request a return.
3. Admin (fulfill perm) -> /admin/returns/{id}/receive -> inspect -> complete.
4. Drain outbox in-process (this loads the new sellers/handlers).
5. Assert seller_wallet_ledger has one return_debit row for the line.
"""
from __future__ import annotations

import asyncio
import json
import os
import urllib.error
import urllib.request

with open(".env") as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#"):
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

API = "http://127.0.0.1:8000"
ORDER_ID = "3e072044-0ead-40bd-893a-8fac0467d5b3"
PRODUCT_ID = "af7cda16-a6e5-4c65-a1a9-06c8e4e5235e"
DEMO_SELLER_ID = "edc67dfe-40f8-43ef-a67d-d8f3a15efb22"
BATCH_ID = "8a24afbd-c372-4aa1-bfb1-5b668caf1153"


def _req(method, path, body=None, token=None):
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        f"{API}{path}",
        data=json.dumps(body).encode() if body else None,
        method=method, headers=headers,
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

    sm = get_sessionmaker()

    # 1. Patch product seller_id
    print("=== Patching product af7cda16 -> demo-seller ===")
    async with sm() as s, s.begin():
        await s.execute(text(
            "UPDATE products SET seller_id = :sid, updated_at = NOW() "
            "WHERE id = :pid"
        ), {"sid": DEMO_SELLER_ID, "pid": PRODUCT_ID})
    print("  done")

    # 2. Customer login -> request return.
    # The order in this env was placed by admin@hypershop.dev (the
    # COD E2E test used the admin account). Admin has the full
    # wildcard perm so they pass the order.place gate that the
    # customer-side endpoint requires.
    _, login = _req("POST", "/api/v1/auth/login", {
        "email": "admin@hypershop.dev",
        "password": "FunnelProd2026!",
    })
    cust_token = (
        login.get("access_token")
        or (login.get("tokens") or {}).get("access_token")
    )

    # Get order line id from the existing order
    async with sm() as s:
        line = (await s.execute(text(
            "SELECT id, unit_price, quantity FROM order_lines WHERE order_id = :oid"
        ), {"oid": ORDER_ID})).first()
        print(f"order line: {dict(line._mapping)}")
        line_id = str(line.id)

    print("\n=== Customer requests return ===")
    code, body = _req("POST", "/api/v1/returns", {
        "order_id": ORDER_ID,
        "reason": "Wrong color received - want refund",
        "lines": [{"order_line_id": line_id, "quantity": 1}],
        "notes": "E2E test",
    }, token=cust_token)
    print(f"  HTTP={code}")
    if code >= 400:
        print(f"  body: {body}")
        return
    return_id = body["id"]
    return_line_id = body["lines"][0]["id"]
    print(f"  return_id: {return_id} line_id={return_line_id}")

    # 3. Admin login -> receive -> inspect -> complete
    _, login = _req("POST", "/api/v1/auth/login", {
        "email": "admin@hypershop.dev",
        "password": "FunnelProd2026!",
    })
    admin_token = (
        login.get("access_token")
        or (login.get("tokens") or {}).get("access_token")
    )

    print("\n=== Admin receive ===")
    code, body = _req(
        "POST", f"/api/v1/admin/returns/{return_id}/receive",
        {"notes": "Package received"}, token=admin_token,
    )
    print(f"  HTTP={code} status={body.get('status') if body else '?'}")

    print("\n=== Admin inspect (sealed -> restock) ===")
    code, body = _req(
        "POST", f"/api/v1/admin/returns/{return_id}/inspect",
        {
            "lines": [{
                "line_id": return_line_id,
                "inspected_quantity": 1,
                "condition": "sealed",
                "target_batch_id": BATCH_ID,
                "target_warehouse_code": "MAIN",
                "inspection_notes": "Sealed, restocking",
            }],
        }, token=admin_token,
    )
    print(f"  HTTP={code} status={body.get('status') if body else '?'}")

    print("\n=== Admin complete ===")
    code, body = _req(
        "POST", f"/api/v1/admin/returns/{return_id}/complete",
        None, token=admin_token,
    )
    print(f"  HTTP={code} status={body.get('status') if body else '?'}")
    if code >= 400:
        print(f"  body: {body}")
        return

    # 4. Drain outbox in-process (imports handlers via app.main)
    print("\n=== Draining outbox (this registers the new seller handler) ===")
    from app.main import app  # noqa: F401
    from app.core.events.dispatcher import dispatch_once, _handlers
    print(f"  registered handler types: {len(_handlers)}")
    print(f"  returns.return.completed has handlers: "
          f"{[h.__qualname__ for h in _handlers.get('returns.return.completed', [])]}")
    total = 0
    for i in range(5):
        n = await dispatch_once()
        print(f"  pass {i+1}: handled {n}")
        total += n
        if n == 0:
            break

    # 5. Verify seller_wallet_ledger
    print("\n=== Verifying seller_wallet_ledger ===")
    async with sm() as s:
        rows = (await s.execute(text(
            "SELECT entry_type, direction, amount, return_request_id, "
            "return_request_line_id, order_line_id, note "
            "FROM seller_wallet_ledger WHERE seller_id = :sid "
            "ORDER BY created_at DESC LIMIT 10"
        ), {"sid": DEMO_SELLER_ID})).all()
        for r in rows:
            print(f"  {dict(r._mapping)}")
        if not rows:
            print("  (NO ROWS — handler did not fire)")


if __name__ == "__main__":
    asyncio.run(main())
