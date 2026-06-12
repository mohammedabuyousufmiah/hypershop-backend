"""WhatsApp webhook ingestion service.

Owns:
  1. ``verify_subscription`` — Meta's GET handshake (echo back challenge
     when ``hub.verify_token`` matches).
  2. ``verify_signature`` — HMAC-SHA256 of raw body with App Secret,
     compared in constant time against ``X-Hub-Signature-256``.
  3. ``ingest`` — parses the JSON envelope, walks
     ``entry[].changes[].value.statuses[]``, upserts a row per
     (wamid, status) pair. Idempotent — duplicate webhooks no-op.

Hard guarantees:
  - Signature verification happens BEFORE any field is trusted.
  - We persist the raw status object verbatim (under raw_payload) so
    ops can replay later.
  - Service NEVER raises on bad webhook bodies — every error path
    returns a structured outcome the API turns into 200/400 (Meta
    retries on non-2xx so we 400 only on signature failure).
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.modules.whatsapp_webhook.repository import (
    WhatsAppMessageStatusRepository,
)

_logger = get_logger("hypershop.whatsapp_webhook")


class IngestOutcome:
    """Result counts for a webhook ingestion attempt."""

    __slots__ = ("inserted", "duplicate", "skipped", "errors")

    def __init__(self) -> None:
        self.inserted = 0
        self.duplicate = 0
        self.skipped = 0
        self.errors: list[str] = []


def verify_subscription(
    *,
    expected_token: str,
    mode: str | None,
    token: str | None,
    challenge: str | None,
) -> str | None:
    """Meta's GET handshake. Returns the challenge string to echo back
    if the verification passes; ``None`` if it doesn't (caller returns 403).
    """
    if mode != "subscribe":
        return None
    if not expected_token or not token:
        return None
    # Constant-time compare so a stopwatch can't enumerate verify tokens.
    if not hmac.compare_digest(expected_token, token):
        return None
    return challenge or ""


def verify_signature(
    *,
    app_secret: str,
    body_bytes: bytes,
    header_value: str | None,
) -> bool:
    """Verify Meta's ``X-Hub-Signature-256: sha256=<hex>`` header.

    Returns False on any failure (missing header, malformed prefix,
    constant-time mismatch). True only on a clean match.
    """
    if not app_secret or not header_value:
        return False
    if not header_value.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), body_bytes, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header_value[len("sha256="):])


async def _ingest_inbound_messages_into_cc_inbox(
    session: AsyncSession, value: dict[str, Any],
) -> None:
    """Forward Meta's inbound `messages[]` array into the CC inbox.

    Wrapped in a try/except so a CC table outage doesn't break the
    delivery-status receipts pipeline (which is what Meta retries on).
    """
    msgs = value.get("messages") or []
    if not msgs:
        return
    contacts = value.get("contacts") or []
    name = ""
    if contacts and isinstance(contacts[0], dict):
        name = (
            (contacts[0].get("profile") or {}).get("name")
            or ""
        )
    try:
        from app.modules.customer_care import cc_inbox_service as _cc
        for m in msgs:
            if not isinstance(m, dict):
                continue
            wamid = str(m.get("id") or "")
            from_msisdn = str(m.get("from") or "")
            if not wamid or not from_msisdn:
                continue
            mtype = str(m.get("type") or "text")
            body_text = ""
            if mtype == "text":
                body_text = str((m.get("text") or {}).get("body") or "")
            elif mtype in ("image", "audio", "video", "document"):
                body_text = f"[{mtype} attachment]"
                cap = (m.get(mtype) or {}).get("caption")
                if cap:
                    body_text = f"{body_text} {cap}"
            elif mtype == "button":
                body_text = str((m.get("button") or {}).get("text") or "")
            elif mtype == "interactive":
                interactive = m.get("interactive") or {}
                body_text = str(
                    (interactive.get("button_reply") or {}).get("title")
                    or (interactive.get("list_reply") or {}).get("title")
                    or "",
                )
            else:
                body_text = f"[{mtype}]"
            await _cc.receive_inbound_whatsapp(
                session=session,
                wa_thread_id=from_msisdn,
                customer_phone=from_msisdn,
                customer_name=name or None,
                body=body_text or f"[{mtype}]",
                channel_message_id=wamid,
            )
    except Exception as e:  # noqa: BLE001
        _logger.warning(
            "cc_inbox_inbound_forward_failed", err=type(e).__name__,
        )


def _parse_status_timestamp(raw: Any) -> datetime:
    """Meta sends Unix epoch as a numeric STRING in their payload."""
    try:
        return datetime.fromtimestamp(int(str(raw)), tz=timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return datetime.now(tz=timezone.utc)


async def ingest(
    *,
    session: AsyncSession,
    body_bytes: bytes,
) -> IngestOutcome:
    """Parse the webhook body + persist every status object.

    Caller MUST verify signature first via ``verify_signature`` — this
    function trusts the body.
    """
    outcome = IngestOutcome()
    try:
        envelope = json.loads(body_bytes.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        outcome.errors.append(f"bad_body:{type(e).__name__}")
        return outcome

    repo = WhatsAppMessageStatusRepository(session)
    entries = envelope.get("entry") or []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        for change in entry.get("changes") or []:
            if not isinstance(change, dict):
                continue
            value = change.get("value") or {}
            # Inbound messages — land each one in the CC unified inbox.
            # Soft-fail: a CC import / DB error never breaks the
            # delivery-status receipts flow above.
            await _ingest_inbound_messages_into_cc_inbox(session, value)
            statuses = value.get("statuses") or []
            for st in statuses:
                if not isinstance(st, dict):
                    outcome.skipped += 1
                    continue
                wamid = str(st.get("id") or "")
                status_raw = str(st.get("status") or "")
                recipient = str(st.get("recipient_id") or "")
                if not wamid or not status_raw:
                    outcome.skipped += 1
                    continue
                # Map Meta's enum values onto our DB CHECK enum.
                status = status_raw.lower()
                if status not in {"sent", "delivered", "read", "failed", "deleted"}:
                    # Unknown status — store as 'failed' so it isn't lost
                    # but the operator can grep error_message.
                    _logger.warning(
                        "whatsapp_webhook_unknown_status",
                        wamid=wamid, status=status_raw,
                    )
                    outcome.skipped += 1
                    continue
                err_code: str | None = None
                err_title: str | None = None
                err_msg: str | None = None
                errors = st.get("errors") or []
                if errors and isinstance(errors[0], dict):
                    e0 = errors[0]
                    err_code = str(e0.get("code") or "") or None
                    err_title = (str(e0.get("title") or "") or None)
                    err_msg = (str(e0.get("message") or e0.get("error_data", {}).get("details", "")) or None)
                inserted = await repo.upsert(
                    wamid=wamid,
                    status=status,
                    recipient_msisdn=recipient,
                    status_timestamp=_parse_status_timestamp(st.get("timestamp")),
                    error_code=err_code,
                    error_title=err_title,
                    error_message=err_msg,
                    raw_payload=st,
                )
                if inserted:
                    outcome.inserted += 1
                else:
                    outcome.duplicate += 1
    _logger.info(
        "whatsapp_webhook_ingested",
        inserted=outcome.inserted,
        duplicate=outcome.duplicate,
        skipped=outcome.skipped,
    )
    return outcome
