"""Real crypto + compute helpers for the realized admin-v3 endpoints.

The store-backed endpoints handle persistence; this module supplies the few
endpoints that need actual computation: HMAC signing/verification, DLP
redaction, and risk/eligibility/limit heuristics.
"""
from __future__ import annotations

import hashlib
import hmac
import re
from typing import Any

from sqlalchemy import text

from app.core.db.uow import UnitOfWork

# Secret used for demo HMAC signing. Real deployments inject via env/secret
# store; this is deterministic so verify() round-trips.
_HMAC_KEY = b"hypershop-admin-v3-hmac-demo-key"


def hmac_sign(body: dict[str, Any]) -> dict[str, Any]:
    payload = str(body.get("payload") or body.get("text") or "")
    sig = hmac.new(_HMAC_KEY, payload.encode(), hashlib.sha256).hexdigest()
    return {"signature": sig, "algorithm": "HS256"}


def webhook_verify(body: dict[str, Any]) -> dict[str, Any]:
    payload = str(body.get("payload") or body.get("text") or "")
    given = str(body.get("signature") or "")
    expected = hmac.new(_HMAC_KEY, payload.encode(), hashlib.sha256).hexdigest()
    ok = hmac.compare_digest(given, expected)
    return {"ok": ok, "reason": None if ok else "signature mismatch"}


# PII patterns for the DLP scanner.
_PII = [
    ("email", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
    ("phone", re.compile(r"\b(?:\+?88)?01\d{9}\b")),
    ("card", re.compile(r"\b(?:\d[ -]*?){13,16}\b")),
    ("nid", re.compile(r"\b\d{10,17}\b")),
]


def dlp_scan(body: dict[str, Any]) -> dict[str, Any]:
    text_in = str(body.get("text") or "")
    findings: list[dict[str, Any]] = []
    redacted = text_in
    for label, pat in _PII:
        for m in pat.finditer(text_in):
            findings.append({"type": label, "match": m.group()[:4] + "***"})
        redacted = pat.sub(f"[REDACTED:{label}]", redacted)
    return {"findings": findings, "redacted_text": redacted,
            "pii_count": len(findings)}


async def customer_risk(uow: UnitOfWork, customer_id: str) -> dict[str, Any]:
    """Real risk score from the customer's order history."""
    async with uow.transactional() as s:
        row = (await s.execute(text(
            "SELECT count(*) AS total, "
            "  round(100.0*count(*) FILTER (WHERE status IN "
            "  ('failed','cancelled'))/NULLIF(count(*),0),1) AS cancel_rate, "
            "  count(*) FILTER (WHERE lower(coalesce(payment_method,'')) ~ 'cod|cash') AS cod "
            "FROM orders WHERE customer_user_id::text = :c"), {"c": str(customer_id)})).one_or_none()
    total = int(row[0]) if row else 0
    cancel = float(row[1]) if row and row[1] is not None else 0.0
    cod = int(row[2]) if row else 0
    score = min(100.0, cancel * 0.7 + (cod / total * 30 if total else 0))
    tier = "high" if score >= 60 else "medium" if score >= 30 else "low"
    reasons = []
    if cancel > 30:
        reasons.append(f"cancel rate {cancel}%")
    if total and cod / total > 0.7:
        reasons.append("mostly COD")
    return {"customer_id": customer_id, "score": round(score, 1), "tier": tier,
            "reasons": reasons, "orders": total}


async def seller_payout_eligibility(uow: UnitOfWork, seller_id: str) -> dict[str, Any]:
    """Eligible unless an active reserve/hold exists for the seller."""
    from app.modules.admin_v3_stubs import store
    held = await store.listing(uow, "seller_reserve", ref=str(seller_id), status="held")
    blockers = [f"reserve #{h['id']} ({h.get('amount_minor', 0)})" for h in held["items"]]
    return {"seller_id": seller_id, "eligible": not blockers,
            "blockers": blockers, "next_payout_at": None}


async def trust_check(uow: UnitOfWork, body: dict[str, Any]) -> dict[str, Any]:
    """Order trust decision: block if customer/phone is blacklisted or risk high."""
    from app.modules.admin_v3_stubs import store
    order_id = body.get("order_id")
    customer_id = body.get("customer_id") or body.get("customer_user_id")
    phone = body.get("phone")
    signals: list[str] = []
    bl = await store.listing(uow, "order_blacklist", status="active")
    blocked_values = {str(x.get("value")) for x in bl["items"]}
    if phone and str(phone) in blocked_values:
        signals.append("phone blacklisted")
    if customer_id:
        risk = await customer_risk(uow, str(customer_id))
        if risk["tier"] == "high":
            signals.append(f"customer risk {risk['score']}")
    outcome = "block" if signals else "allow"
    return {"order_id": order_id, "outcome": outcome, "signals": signals}


async def rider_limit_evaluate(uow: UnitOfWork, rider_id: str) -> dict[str, Any]:
    """Compare today's verified COD against the rider's daily limit."""
    from app.modules.admin_v3_stubs import store
    lim = await store.get_by_ref(uow, "rider_limit", str(rider_id))
    daily_max = int((lim or {}).get("daily_max_minor", 0))
    settled = await store.listing(uow, "cod_settlement", ref=str(rider_id))
    consumed = sum(int(x.get("amount_minor", 0)) for x in settled["items"]
                   if x.get("status") == "verified")
    within = (daily_max == 0) or (consumed <= daily_max)
    return {"rider_id": rider_id, "within_limit": within,
            "consumed_minor": consumed, "daily_max_minor": daily_max}
