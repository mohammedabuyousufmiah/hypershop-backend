"""Storefront revalidation hook — the "single hop" between admin save
and the customer-facing page refresh.

When an admin write lands, we:

1. Bump an in-process version counter so any consumer pulling the
   unified layout sees a new ``version`` field and knows the cached
   payload is stale.
2. POST to the storefront's ``/api/revalidate`` webhook (HMAC-signed
   with ``STOREFRONT_REVALIDATE_SECRET``) which calls Next's
   ``revalidateTag('storefront')`` on every render server in the
   fleet.

Both steps soft-fail. The cache layer is best-effort — a missed
revalidation just means a stale page for at most 60s (the
``revalidate: 60`` fallback on the fetch call).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger

_log = get_logger("hypershop.storefront_cms.revalidate")

# In-process version counter — bumped on every admin write. Embedded
# into the unified layout response so consumers can short-circuit a
# render when the version hasn't changed since their last fetch.
_version: int = int(time.time())


def current_version() -> int:
    return _version


def bump_version() -> int:
    """Bump + return the new version."""
    global _version
    _version = max(_version + 1, int(time.time()))
    return _version


async def trigger_storefront_revalidate(
    tags: tuple[str, ...] = ("storefront",),
    *,
    paths: tuple[str, ...] = (),
) -> None:
    """POST the revalidation webhook. Best-effort, never raises."""
    bump_version()
    s = get_settings()
    url = (getattr(s, "storefront_revalidate_url", "") or "").strip()
    secret = (getattr(s, "storefront_revalidate_secret", "") or "").strip()
    if not url:
        # Storefront URL not configured (e.g. local dev without a Next
        # server) — version bump alone is enough; storefront will pick
        # up the change on the next 60s SWR tick.
        return
    payload: dict[str, Any] = {
        "tags": list(tags),
        "paths": list(paths),
        "ts": int(time.time()),
    }
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    headers: dict[str, str] = {"content-type": "application/json"}
    if secret:
        sig = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        headers["x-hypershop-signature"] = sig
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            res = await client.post(url, content=body, headers=headers)
        _log.info(
            "storefront.revalidate status=%s tags=%s paths=%s",
            res.status_code, list(tags), list(paths),
        )
    except Exception as exc:  # noqa: BLE001
        _log.warning("storefront.revalidate.failed err=%s", exc)
