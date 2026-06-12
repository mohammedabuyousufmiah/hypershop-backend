"""Banglalink HUB SIP-trunk voice adapter.

Bangladesh-local telephony provider chosen 2026-05-16 for inbound
voice-call routing. Direct telco SIP trunk; pricing ~৳0.50/min for
inbound. No vendor SDK — we talk HTTP for webhook ingest + REST for
call-control, and the agent softphone runs SIP-over-WebRTC against
Banglalink's SBC.

Config (env):
    BL_HUB_BASE_URL            — REST API root, e.g. https://hub.banglalink.net/api/v1
    BL_HUB_API_KEY             — bearer token used on outbound REST calls
    BL_HUB_WEBHOOK_SECRET      — shared HMAC secret for inbound webhook signature
    BL_HUB_SIP_DOMAIN          — SIP realm, e.g. sip.hub.banglalink.net (for transfer URIs)

When any of ``BL_HUB_API_KEY`` / ``BL_HUB_WEBHOOK_SECRET`` is missing
the adapter is **disabled**: webhook signature verification will refuse
everything (failing closed), and outbound call-control raises
``AdapterNotConfiguredError``. This lets the rest of the system run in
dev without the BL credentials.

Payload schema assumptions — TUNE TO MATCH BL HUB'S ACTUAL CONTRACT:
    Header:  X-BL-Signature  →  hex(hmac_sha256(secret, raw_body))
    Body JSON example::

        {
          "event": "ringing",
          "call_sid": "BL-CALL-2026051601234",
          "from": "+8801911740672",
          "to":   "+8809678123456",
          "trunk": "TRK-001",
          "timestamp": "2026-05-16T10:00:00Z"
        }

    Event names mapped:
      "ringing" / "alerting"     → ringing
      "answered" / "connected"   → answered
      "ended" / "completed"      → ended
      "missed" / "no_answer"     → missed

The mapping table is in ``_EVENT_MAP`` below — extend as new event names
appear in BL's webhook stream.
"""
from __future__ import annotations

import hashlib
import hmac
import os
from typing import Any

import httpx

from app.core.logging import get_logger
from app.modules.customer_care.external_adapters.base import (
    AdapterNotConfiguredError,
    CallControlResult,
    InboundCallEvent,
)

_log = get_logger("hypershop.customer_care.banglalink_hub")

_EVENT_MAP: dict[str, str] = {
    "ringing": "ringing",
    "alerting": "ringing",
    "incoming": "ringing",
    "answered": "answered",
    "connected": "answered",
    "ended": "ended",
    "completed": "ended",
    "disconnected": "ended",
    "missed": "missed",
    "no_answer": "missed",
    "timeout": "missed",
}


class BanglalinkHubAdapter:
    name = "banglalink_hub"

    def __init__(self) -> None:
        self._base_url = os.environ.get("BL_HUB_BASE_URL", "").rstrip("/")
        self._api_key = os.environ.get("BL_HUB_API_KEY", "")
        self._secret = os.environ.get("BL_HUB_WEBHOOK_SECRET", "")
        self._sip_domain = os.environ.get("BL_HUB_SIP_DOMAIN", "")

    @property
    def enabled(self) -> bool:
        return bool(self._api_key and self._secret and self._base_url)

    # ─── Inbound webhook ───────────────────────────────────────────
    def verify_webhook_signature(
        self, *, raw_body: bytes, headers: dict[str, str],
    ) -> tuple[bool, str | None]:
        """HMAC-SHA256 of raw body with the shared secret, hex-encoded.

        Header name is case-insensitive; FastAPI's ``Request.headers``
        already normalises but we re-normalise here defensively.
        """
        if not self._secret:
            return False, "adapter_not_configured"
        norm = {k.lower(): v for k, v in headers.items()}
        sig = norm.get("x-bl-signature", "")
        if not sig:
            return False, "missing_x_bl_signature_header"
        expected = hmac.new(
            self._secret.encode(), raw_body, hashlib.sha256,
        ).hexdigest()
        # constant-time compare to avoid signature-timing leaks
        if not hmac.compare_digest(sig.lower(), expected.lower()):
            return False, "signature_mismatch"
        return True, None

    def parse_inbound_event(self, payload: dict[str, Any]) -> InboundCallEvent:
        """Map BL Hub payload → InboundCallEvent.

        Raises ValueError on missing/unknown event_type so the caller
        can 400 the webhook (provider should never send garbage; if
        they do, we want a loud failure rather than silent ingest).
        """
        raw_event = str(payload.get("event") or "").lower()
        mapped = _EVENT_MAP.get(raw_event)
        if not mapped:
            raise ValueError(f"unsupported_event:{raw_event!r}")
        call_sid = str(payload.get("call_sid") or "")
        from_phone = str(payload.get("from") or "")
        if not call_sid or not from_phone:
            raise ValueError("missing_call_sid_or_from")
        return InboundCallEvent(
            provider=self.name,
            provider_call_id=call_sid,
            event_type=mapped,
            from_phone=from_phone,
            to_number=str(payload.get("to") or "") or None,
            raw=payload,
        )

    # ─── Outbound REST call-control ────────────────────────────────
    def _auth_headers(self) -> dict[str, str]:
        if not self._api_key:
            raise AdapterNotConfiguredError("BL_HUB_API_KEY not set")
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    async def transfer_call(
        self, *, provider_call_id: str, target_sip_uri: str,
    ) -> CallControlResult:
        """POST /calls/{id}/transfer  body={target: <sip_uri>}

        Assumed endpoint shape — confirm with BL's REST contract.
        """
        if not self.enabled:
            return CallControlResult(ok=False, error="adapter_not_configured")
        url = f"{self._base_url}/calls/{provider_call_id}/transfer"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.post(
                    url, headers=self._auth_headers(),
                    json={"target": target_sip_uri},
                )
                ok = 200 <= r.status_code < 300
                return CallControlResult(
                    ok=ok,
                    error=None if ok else f"http_{r.status_code}:{r.text[:200]}",
                    raw=r.json() if r.headers.get("content-type", "").startswith("application/json") else {},
                )
        except httpx.HTTPError as e:
            _log.warning("bl_hub_transfer_failed", call_id=provider_call_id, error=str(e))
            return CallControlResult(ok=False, error=f"network_error:{e}")

    async def hangup_call(self, *, provider_call_id: str) -> CallControlResult:
        """POST /calls/{id}/hangup"""
        if not self.enabled:
            return CallControlResult(ok=False, error="adapter_not_configured")
        url = f"{self._base_url}/calls/{provider_call_id}/hangup"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.post(url, headers=self._auth_headers())
                ok = 200 <= r.status_code < 300
                return CallControlResult(
                    ok=ok,
                    error=None if ok else f"http_{r.status_code}:{r.text[:200]}",
                )
        except httpx.HTTPError as e:
            _log.warning("bl_hub_hangup_failed", call_id=provider_call_id, error=str(e))
            return CallControlResult(ok=False, error=f"network_error:{e}")

    # ─── Helpers for FE softphone bootstrap ────────────────────────
    def softphone_sip_uri(self, agent_extension: str) -> str:
        """Build a SIP URI the softphone JS client registers with.

        The agent's WebRTC client uses this to register against
        Banglalink's SBC for inbound bridge + outbound INVITE.
        """
        if not self._sip_domain:
            raise AdapterNotConfiguredError("BL_HUB_SIP_DOMAIN not set")
        return f"sip:{agent_extension}@{self._sip_domain}"


# Module-level singleton; cheap to construct, no I/O at init.
_default = BanglalinkHubAdapter()


def get_adapter() -> BanglalinkHubAdapter:
    return _default
