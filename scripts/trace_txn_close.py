"""Targeted SQLAlchemy txn-lifecycle tracer.

Attaches event listeners to the SyncEngine that backs the asyncpg
adapter, then places one COD order and prints every transaction
event in order so we can see precisely where the outer txn closes.

Usage: ``python -m scripts.trace_txn_close``
"""
from __future__ import annotations

import asyncio
import os

with open(".env") as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#"):
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

import urllib.request  # noqa: E402
import urllib.error    # noqa: E402
import json            # noqa: E402

from sqlalchemy import event  # noqa: E402

from app.core.db.session import get_engine  # noqa: E402


events_recorded: list[str] = []


def _hook(event_name: str):
    def handler(*args, **kwargs):
        # Args differ per event; just record the name + arg count
        events_recorded.append(f"{event_name}")
    return handler


def attach_listeners() -> None:
    engine = get_engine()
    sync_engine = engine.sync_engine
    for evt in (
        "begin",
        "begin_twophase",
        "savepoint",
        "release_savepoint",
        "rollback_savepoint",
        "commit",
        "rollback",
        "after_transaction_create",
        "after_transaction_end",
    ):
        try:
            event.listen(sync_engine, evt, _hook(evt))
        except Exception as e:  # noqa: BLE001
            print(f"  couldn't attach {evt}: {e}")


API = "http://127.0.0.1:8000"


def _post(path: str, body: dict | None = None, token: str | None = None):
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if token: headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        f"{API}{path}",
        data=json.dumps(body).encode("utf-8") if body else b"",
        method="POST",
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def _get(path: str, token: str | None = None):
    headers = {"Accept": "application/json"}
    if token: headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"{API}{path}", headers=headers)
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read().decode("utf-8"))


async def main() -> None:
    print("Attaching SA event listeners…")
    attach_listeners()

    # Drive a COD order
    _, login = _post("/api/v1/auth/login", {"email": "admin@hypershop.dev", "password": "FunnelProd2026!"})
    token = login.get("access_token") or login.get("tokens", {}).get("access_token")
    prod = _get("/api/v1/catalog/products/apple-watch-se-44")
    offer_id = prod["variants"][0]["id"]
    _post("/api/v1/cart", token=token)  # ignore
    _post("/api/v1/cart/items", {"offer_id": offer_id, "quantity": 1}, token=token)

    sess_code, sess = _post(
        "/api/v1/checkout/preview",
        {
            "shipping_address": {
                "full_name": "Tracer", "line1": "DH 27", "city": "Dhaka",
                "postal_code": "1209", "country_code": "BD",
                "phone": "+8801711000001",
            },
            "payment_method": "cod",
        },
        token=token,
    )
    sid = sess["id"]
    print(f"  preview ok: session={sid}")

    events_recorded.clear()
    print(f"\nCalling /confirm — watching events...\n")

    code, body = _post(f"/api/v1/checkout/{sid}/confirm", {"payment_method": "cod"}, token=token)

    print(f"  /confirm response HTTP {code}")
    if code >= 400:
        print(f"  body: {body}")

    print("\n=== TXN EVENT SEQUENCE during confirm ===")
    for i, e in enumerate(events_recorded, 1):
        print(f"  {i:3d}. {e}")


if __name__ == "__main__":
    asyncio.run(main())
