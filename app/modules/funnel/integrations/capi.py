"""Server-side Conversions-API push adapters (Meta / TikTok / Google).

Sends a consent-filtered audience to each platform's server-side events API.
Env-gated — if a platform's credentials are absent, returns
``{"status": "not_configured"}`` (no-op) instead of failing, mirroring the
SIM-gateway / OpenAI pattern. PII is SHA-256 hashed per each platform's spec.

Env vars (set on the backend; never commit real values):
    META_CAPI_PIXEL_ID, META_CAPI_ACCESS_TOKEN
    TIKTOK_EVENTS_PIXEL_CODE, TIKTOK_EVENTS_ACCESS_TOKEN
    GOOGLE_ADS_CUSTOMER_ID, GOOGLE_ADS_DEVELOPER_TOKEN, GOOGLE_ADS_ACCESS_TOKEN
"""
from __future__ import annotations

import hashlib
import os
from typing import Any

import httpx

from app.core.logging import get_logger

_log = get_logger("hypershop.funnel.capi")

_SUPPORTED = {"meta", "tiktok", "google"}


def _sha256(value: str | None) -> str | None:
    if not value:
        return None
    return hashlib.sha256(value.strip().lower().encode("utf-8")).hexdigest()


def _hashed_users(audience: list[dict[str, Any]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for a in audience:
        em = _sha256(a.get("email"))
        ph = _sha256(a.get("phone"))
        if not em and not ph:
            continue
        rec: dict[str, str] = {}
        if em:
            rec["em"] = em
        if ph:
            rec["ph"] = ph
        out.append(rec)
    return out


async def push_audience(
    *, platform: str, segment: str, audience: list[dict[str, Any]],
    timeout: float = 20.0,
) -> dict[str, Any]:
    """Push the audience to a platform's server-side API. Never raises —
    returns a status dict the endpoint can surface to the operator."""
    if platform not in _SUPPORTED:
        return {"status": "unsupported", "platform": platform, "sent": 0}

    users = _hashed_users(audience)
    if not users:
        return {"status": "empty", "platform": platform, "sent": 0,
                "reason": "no hashable email/phone in audience"}

    try:
        if platform == "meta":
            return await _push_meta(users, segment, timeout)
        if platform == "tiktok":
            return await _push_tiktok(users, segment, timeout)
        if platform == "google":
            return await _push_google(users, segment, timeout)
    except httpx.HTTPError as e:
        _log.warning("capi_push_failed", platform=platform, error=str(e))
        return {"status": "error", "platform": platform, "sent": 0, "error": str(e)[:200]}
    return {"status": "unsupported", "platform": platform, "sent": 0}


async def _push_meta(users: list[dict], segment: str, timeout: float) -> dict[str, Any]:
    pixel = os.getenv("META_CAPI_PIXEL_ID")
    token = os.getenv("META_CAPI_ACCESS_TOKEN")
    if not pixel or not token:
        return {"status": "not_configured", "platform": "meta", "sent": 0}
    data = [
        {
            "event_name": "CustomAudience",
            "event_time": int(__import__("time").time()),
            "action_source": "system_generated",
            "user_data": u,
            "custom_data": {"segment": segment},
        }
        for u in users
    ]
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(
            f"https://graph.facebook.com/v19.0/{pixel}/events",
            params={"access_token": token},
            json={"data": data},
        )
        r.raise_for_status()
        _log.info("capi_push_meta", count=len(users), segment=segment)
        return {"status": "sent", "platform": "meta", "sent": len(users),
                "response": r.json()}


async def _push_tiktok(users: list[dict], segment: str, timeout: float) -> dict[str, Any]:
    pixel = os.getenv("TIKTOK_EVENTS_PIXEL_CODE")
    token = os.getenv("TIKTOK_EVENTS_ACCESS_TOKEN")
    if not pixel or not token:
        return {"status": "not_configured", "platform": "tiktok", "sent": 0}
    payload = {
        "pixel_code": pixel,
        "event": "AddToWishlist",
        "context": {"segment": segment},
        "batch": [{"user": u} for u in users],
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(
            "https://business-api.tiktok.com/open_api/v1.3/event/track/",
            headers={"Access-Token": token, "Content-Type": "application/json"},
            json=payload,
        )
        r.raise_for_status()
        _log.info("capi_push_tiktok", count=len(users), segment=segment)
        return {"status": "sent", "platform": "tiktok", "sent": len(users),
                "response": r.json()}


async def _push_google(users: list[dict], segment: str, timeout: float) -> dict[str, Any]:
    # Google Customer Match needs OAuth2 + an existing user-list resource.
    # Gate on all three creds; otherwise no-op. (Full OAuth flow is left to
    # the operator's service account — this confirms wiring + auth presence.)
    cid = os.getenv("GOOGLE_ADS_CUSTOMER_ID")
    dev = os.getenv("GOOGLE_ADS_DEVELOPER_TOKEN")
    token = os.getenv("GOOGLE_ADS_ACCESS_TOKEN")
    if not (cid and dev and token):
        return {"status": "not_configured", "platform": "google", "sent": 0}
    # Minimal offline-user-data upload stub (real impl needs a user_list id).
    return {"status": "configured_pending_userlist", "platform": "google",
            "sent": 0, "note": "creds present; supply GOOGLE_ADS_USER_LIST_ID to upload"}
