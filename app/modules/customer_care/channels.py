"""Channel adapter framework — sprint 7.

5 channels with consistent shape:
- ``send_*(to, body, ...)``  → returns provider response or ``None``
- All degrade gracefully when creds missing (log-only)
- Inbound webhook handlers live in ``api/sprint7.py``

Channels:
- email      — SMTP outbound, generic-provider webhook inbound
- sms        — BulkSMSBD (BD) preferred, Twilio fallback, webhook inbound
- messenger  — Meta Graph API (FB page), webhook inbound
- instagram  — Meta Graph API (IG business), webhook inbound
- webchat    — in-process queue, polled by the customer's browser

The legacy CC ``IncomingMessage`` Protocol from v1.6.4 isn't used by
the Hypershop integration; ingestion now goes directly through the
webhook handlers + service.append_message.
"""
from __future__ import annotations

import asyncio
import smtplib
from collections import defaultdict, deque
from email.message import EmailMessage
from typing import Any

import httpx

from app.core.logging import get_logger
from app.modules.customer_care.config import settings

_log = get_logger("hypershop.customer_care.channels")


def _json_or_text(r: "httpx.Response") -> dict[str, Any]:
    ct = r.headers.get("content-type", "")
    if ct.startswith("application/json"):
        try:
            return r.json()
        except Exception:  # noqa: BLE001
            pass
    return {"raw": r.text}


def _xml_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _csms_id() -> str:
    """SSL Wireless requires a unique client message id (<= 20 chars)."""
    import uuid

    return uuid.uuid4().hex[:20]


# ================================================================ EMAIL
async def send_email(
    *,
    to_address: str,
    subject: str,
    body_text: str,
    body_html: str | None = None,
) -> bool:
    """SMTP outbound. Returns True on success, False on failure /
    missing config. Runs the blocking ``smtplib`` call in a thread."""
    cfg = settings()
    if not (cfg.smtp_host and cfg.smtp_from_address):
        _log.info(
            "email_send_skipped_no_creds",
            to=to_address, subject_preview=subject[:60],
        )
        return False

    def _blocking_send() -> bool:
        try:
            msg = EmailMessage()
            msg["From"] = cfg.smtp_from_address
            msg["To"] = to_address
            msg["Subject"] = subject[:120]
            msg.set_content(body_text)
            if body_html:
                msg.add_alternative(body_html, subtype="html")
            with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=15) as s:
                if cfg.smtp_use_tls:
                    s.starttls()
                if cfg.smtp_username and cfg.smtp_password:
                    s.login(cfg.smtp_username, cfg.smtp_password)
                s.send_message(msg)
            return True
        except Exception as e:  # noqa: BLE001
            _log.warning("email_send_failed", error=str(e), to=to_address)
            return False

    return await asyncio.to_thread(_blocking_send)


# ================================================================ SMS
async def send_sms(
    *,
    to_phone: str,
    body: str,
    timeout: float = 12.0,
) -> dict[str, Any] | None:
    """Generic SMS send. Tries BulkSMSBD first (BD market), falls
    back to Twilio. Returns provider response dict or ``None``.
    """
    cfg = settings()
    to_clean = to_phone.lstrip("+").strip()

    # 1) Local SIM gateway (BD physical SIM via GoIP / Android GSM gateway).
    #    Generic HTTP contract: POST {url}/sms  {to, message, [line]}  + Bearer.
    if cfg.sim_gateway_url and cfg.sim_gateway_token:
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.post(
                    cfg.sim_gateway_url.rstrip("/") + "/sms",
                    headers={"Authorization": f"Bearer {cfg.sim_gateway_token}"},
                    json={
                        "to": to_clean,
                        "message": body[:480],
                        **({"line": cfg.sim_gateway_line} if cfg.sim_gateway_line else {}),
                    },
                )
                r.raise_for_status()
                _log.info("sms_sim_gateway_sent", to=to_phone)
                return _json_or_text(r)
        except httpx.HTTPError as e:
            _log.warning("sms_sim_gateway_failed", error=str(e), to=to_phone)

    # 2) SSL Wireless — major BD SMS aggregator.
    if cfg.ssl_sms_api_token and cfg.ssl_sms_sid:
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.post(
                    cfg.ssl_sms_base_url.rstrip("/") + "/api/v3/send-sms",
                    json={
                        "api_token": cfg.ssl_sms_api_token,
                        "sid": cfg.ssl_sms_sid,
                        "msisdn": to_clean,
                        "sms": body[:480],
                        "csms_id": _csms_id(),
                    },
                )
                r.raise_for_status()
                _log.info("sms_sslwireless_sent", to=to_phone)
                return _json_or_text(r)
        except httpx.HTTPError as e:
            _log.warning("sms_sslwireless_failed", error=str(e), to=to_phone)

    # 3) BulkSMSBD — BD aggregator.
    if cfg.bulksms_bd_api_token:
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.post(
                    "https://bulksmsbd.net/api/smsapi",
                    json={
                        "api_key": cfg.bulksms_bd_api_token,
                        "senderid": cfg.bulksms_bd_sender_id or "Hypershop",
                        "number": to_clean,
                        "message": body[:160],
                    },
                )
                r.raise_for_status()
                _log.info("sms_bulksms_bd_sent", to=to_phone)
                return (
                    r.json()
                    if r.headers.get("content-type", "").startswith("application/json")
                    else {"raw": r.text}
                )
        except httpx.HTTPError as e:
            _log.warning("sms_bulksms_bd_failed", error=str(e), to=to_phone)

    if cfg.twilio_account_sid and cfg.twilio_auth_token and cfg.twilio_from_number:
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.post(
                    f"https://api.twilio.com/2010-04-01/Accounts/{cfg.twilio_account_sid}/Messages.json",
                    auth=(cfg.twilio_account_sid, cfg.twilio_auth_token),
                    data={
                        "From": cfg.twilio_from_number,
                        "To": to_phone,
                        "Body": body[:1600],
                    },
                )
                r.raise_for_status()
                _log.info("sms_twilio_sent", to=to_phone)
                return r.json()
        except httpx.HTTPError as e:
            _log.warning("sms_twilio_failed", error=str(e), to=to_phone)

    _log.info("sms_send_skipped_no_creds", to=to_phone, body_preview=body[:80])
    return None


# ================================================================ VOICE (outbound)
async def place_voice_call(
    *,
    to_phone: str,
    message: str,
    bridge_to: str | None = None,
    timeout: float = 15.0,
) -> dict[str, Any] | None:
    """Outbound voice call (click-to-call / IVR announce).

    BD-first provider chain:
      1. Local SIM gateway (GoIP / Android GSM gateway) — dials out on a
         physical Bangladeshi SIM (Grameenphone / Robi / Banglalink /
         Teletalk). Generic HTTP contract: POST {url}/call {to, message, [line]}.
      2. Twilio Programmable Voice — optional INTERNATIONAL fallback only.
    Degrades to log-only when nothing is configured. Returns the provider
    response dict or ``None``.
    """
    cfg = settings()
    to_clean = to_phone.lstrip("+").strip()

    # 1) Local SIM gateway click-to-call (Bangladeshi SIM).
    if cfg.sim_gateway_url and cfg.sim_gateway_token:
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.post(
                    cfg.sim_gateway_url.rstrip("/") + "/call",
                    headers={"Authorization": f"Bearer {cfg.sim_gateway_token}"},
                    json={
                        "to": to_clean,
                        "message": message[:480],
                        # When set, the gateway bridges the agent leg (to) to
                        # the customer leg (bridge) — local SIM click-to-call.
                        **({"bridge": bridge_to.lstrip("+").strip()} if bridge_to else {}),
                        **({"line": cfg.sim_gateway_line} if cfg.sim_gateway_line else {}),
                    },
                )
                r.raise_for_status()
                _log.info("voice_sim_gateway_call", to=to_phone, bridge=bridge_to)
                return _json_or_text(r)
        except httpx.HTTPError as e:
            _log.warning("voice_sim_gateway_failed", error=str(e), to=to_phone)

    # 2) Twilio Programmable Voice — optional international fallback.
    if cfg.twilio_account_sid and cfg.twilio_auth_token and cfg.twilio_from_number:
        try:
            twiml = f"<Response><Say>{_xml_escape(message[:400])}</Say></Response>"
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.post(
                    f"https://api.twilio.com/2010-04-01/Accounts/{cfg.twilio_account_sid}/Calls.json",
                    auth=(cfg.twilio_account_sid, cfg.twilio_auth_token),
                    data={"From": cfg.twilio_from_number, "To": to_phone, "Twiml": twiml},
                )
                r.raise_for_status()
                _log.info("voice_twilio_call", to=to_phone)
                return r.json()
        except httpx.HTTPError as e:
            _log.warning("voice_twilio_failed", error=str(e), to=to_phone)

    _log.info("voice_call_skipped_no_creds", to=to_phone, message_preview=message[:80])
    return None


def bd_providers_status() -> dict[str, dict[str, Any]]:
    """Connection state of the Bangladeshi-first SMS/voice providers, for the
    Voice AI admin page. Pure config read — no network calls."""
    cfg = settings()
    def conn(ok: bool, on: str, off: str) -> dict[str, Any]:
        return {"connected": ok, "detail": on if ok else off}
    return {
        "sim_gateway": conn(
            bool(cfg.sim_gateway_url and cfg.sim_gateway_token),
            "local BD SIM (GoIP/GSM gateway) — SMS + voice",
            "not configured",
        ),
        "ssl_wireless": conn(
            bool(cfg.ssl_sms_api_token and cfg.ssl_sms_sid),
            "SSL Wireless (BD SMS aggregator)",
            "not configured",
        ),
        "bulksms_bd": conn(
            bool(cfg.bulksms_bd_api_token),
            "BulkSMSBD (BD SMS aggregator)",
            "not configured",
        ),
        "twilio": conn(
            bool(cfg.twilio_account_sid and cfg.twilio_auth_token),
            "Twilio (international fallback)",
            "not configured",
        ),
    }


# ================================================================ MESSENGER
async def send_messenger(
    *,
    to_psid: str,
    body: str,
    timeout: float = 12.0,
) -> dict[str, Any] | None:
    """Send to a Messenger page recipient by PSID (page-scoped user id)."""
    cfg = settings()
    if not (cfg.messenger_page_access_token and cfg.messenger_page_id):
        _log.info("messenger_send_skipped_no_creds", to=to_psid)
        return None
    url = f"https://graph.facebook.com/v20.0/{cfg.messenger_page_id}/messages"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(
                url,
                params={"access_token": cfg.messenger_page_access_token},
                json={
                    "recipient": {"id": to_psid},
                    "messaging_type": "RESPONSE",
                    "message": {"text": body[:2000]},
                },
            )
            r.raise_for_status()
            _log.info("messenger_send_success", to=to_psid)
            return r.json()
    except httpx.HTTPError as e:
        body_text = ""
        if isinstance(e, httpx.HTTPStatusError):
            body_text = (e.response.text or "")[:400]
        _log.warning("messenger_send_failed", error=str(e), response_body=body_text)
        return None


# ================================================================ INSTAGRAM DM
async def send_instagram_dm(
    *,
    to_ig_user_id: str,
    body: str,
    timeout: float = 12.0,
) -> dict[str, Any] | None:
    """Send an Instagram DM via the Messaging API."""
    cfg = settings()
    if not (cfg.instagram_page_access_token and cfg.instagram_account_id):
        _log.info("instagram_send_skipped_no_creds", to=to_ig_user_id)
        return None
    url = f"https://graph.facebook.com/v20.0/{cfg.instagram_account_id}/messages"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(
                url,
                params={"access_token": cfg.instagram_page_access_token},
                json={
                    "recipient": {"id": to_ig_user_id},
                    "message": {"text": body[:1000]},
                },
            )
            r.raise_for_status()
            _log.info("instagram_send_success", to=to_ig_user_id)
            return r.json()
    except httpx.HTTPError as e:
        body_text = ""
        if isinstance(e, httpx.HTTPStatusError):
            body_text = (e.response.text or "")[:400]
        _log.warning("instagram_send_failed", error=str(e), response_body=body_text)
        return None


# ================================================================ WEBCHAT (in-process)
# Per-session inbox queues for the storefront widget. The customer's
# browser polls /webchat/{session_id}/poll; this fills the queue.
_webchat_queues: dict[str, deque] = defaultdict(lambda: deque(maxlen=200))


def webchat_push(session_id: str, payload: dict) -> None:
    """Enqueue a message bound for the customer browser."""
    _webchat_queues[session_id].append(payload)


def webchat_drain(session_id: str, max_n: int = 20) -> list[dict]:
    """Customer browser polls — returns and clears pending messages."""
    q = _webchat_queues.get(session_id)
    if not q:
        return []
    out: list[dict] = []
    while q and len(out) < max_n:
        out.append(q.popleft())
    return out


def webchat_session_count() -> int:
    return len(_webchat_queues)
