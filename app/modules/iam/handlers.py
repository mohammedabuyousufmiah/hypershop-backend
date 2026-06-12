"""Outbox handlers for IAM-emitted events.

The IAM service writes outbox messages inside its DB transaction. The ARQ
worker invokes ``app.core.events.dispatcher.dispatch_once`` which routes by
``OutboxMessage.type`` to handlers registered here.

Importing this module has the side-effect of registering handlers — the
worker imports it at startup; the tests import it explicitly per-test.
"""

from __future__ import annotations

from app.core.config import get_settings
from app.core.events.dispatcher import register_handler
from app.core.events.models import OutboxMessage
from app.core.logging import get_logger
from app.modules.iam.transport.email_log import LogOnlyEmailTransport
from app.modules.iam.transport.email_smtp import SmtpEmailTransport

_logger = get_logger("hypershop.iam.handlers")
# Pick log-only transport in dev when SMTP env is empty; bind real SMTP in prod.
_transport = SmtpEmailTransport() if (get_settings().smtp_host or "").strip() else LogOnlyEmailTransport()


# ---------- event types ----------
EVT_OTP_EMAIL_SEND = "iam.otp.email.send"
EVT_OTP_SMS_SEND = "iam.otp.sms.send"
EVT_PASSWORD_RESET_EMAIL_SEND = "iam.password_reset.email.send"
EVT_PASSWORD_CHANGED_EMAIL_SEND = "iam.password_changed.email.send"


_OTP_TEMPLATES = {
    "email_verify": (
        "Verify your Hypershop email",
        "Welcome to Hypershop.\n\nYour verification code is: {code}\n\n"
        "It expires in {minutes} minutes. "
        "If you didn't create this account, you can ignore this message.",
    ),
    "login": (
        "Your Hypershop login code",
        "Use this code to log in: {code}\n\nIt expires in {minutes} minutes.\n"
        "If this wasn't you, change your password immediately.",
    ),
    "password_reset": (
        "Hypershop password reset code",
        "Use this code to reset your password: {code}\n\nIt expires in {minutes} minutes.",
    ),
}

# SMS bodies — single line, ≤160 chars to stay in one segment, no PII
# beyond the OTP code itself. {code} substituted at dispatch time.
_OTP_SMS_TEMPLATES = {
    "email_verify": (
        "Hypershop verification code: {code} "
        "(expires in {minutes} min). Don't share this code."
    ),
    "login": (
        "Hypershop login code: {code} "
        "(expires in {minutes} min). Don't share this code."
    ),
    "password_reset": (
        "Hypershop password reset code: {code} "
        "(expires in {minutes} min). Don't share this code."
    ),
}


async def _handle_otp_email(message: OutboxMessage) -> None:
    payload = message.payload
    purpose = str(payload["purpose"])
    code = str(payload["code"])
    email = str(payload["email"])
    ttl_seconds = int(payload.get("ttl_seconds", 600))

    template = _OTP_TEMPLATES.get(purpose)
    if template is None:
        # Unknown purpose — surface as failure so the dispatcher dead-letters.
        raise ValueError(f"unknown OTP purpose '{purpose}'")
    subject, body = template
    await _transport.send(
        to=email,
        subject=subject,
        text_body=body.format(code=code, minutes=ttl_seconds // 60),
    )
    _logger.info("otp_email_sent", purpose=purpose, email_domain=email.split("@", 1)[-1])


async def _handle_otp_sms(message: OutboxMessage) -> None:
    """Dispatch a one-time passcode via WhatsApp first, falling back to
    SMS for BD numbers.

    Routing (delegated to :mod:`app.modules.invoice_dispatch.service`):
      +880... (Bangladesh) → WhatsApp → on fail → BD SMS aggregator
      anything else        → WhatsApp ONLY (no expensive intl SMS)

    Payload schema (unchanged from earlier shape — IAM service still
    emits ``EVT_OTP_SMS_SEND`` because that's where its current callers
    live; the event NAME is now misleading but the SHAPE is the contract):
      {
        "purpose": "email_verify" | "login" | "password_reset",
        "code":    "<plaintext>",
        "phone":   "+8801XXXXXXXXX"  (E.164),
        "ttl_seconds": 600
      }

    A `ServiceUnavailableError` from the dispatcher (no channel reached
    the recipient) propagates to the outbox dispatcher which schedules
    retry — never silent drops.
    """
    from app.modules.invoice_dispatch.service import dispatch_otp

    payload = message.payload
    purpose = str(payload["purpose"])
    code = str(payload["code"])
    phone = str(payload["phone"])
    ttl_seconds = int(payload.get("ttl_seconds", 600))

    if purpose not in _OTP_SMS_TEMPLATES:
        raise ValueError(f"unknown OTP purpose '{purpose}' for OTP dispatch")

    result = await dispatch_otp(
        phone=phone,
        code=code,
        purpose=purpose,
        ttl_seconds=ttl_seconds,
    )
    _logger.info(
        "otp_dispatched",
        purpose=purpose,
        via=result.via,
        delivered=result.delivered,
        to_prefix=phone[:6],
        error_code=result.error_code,
    )


async def _handle_password_reset_email(message: OutboxMessage) -> None:
    payload = message.payload
    email = str(payload["email"])
    token = str(payload["token"])
    ttl_seconds = int(payload.get("ttl_seconds", 3600))
    reset_url_base = str(payload.get("reset_url_base", "https://hypershop.local/reset"))
    link = f"{reset_url_base}?token={token}"

    body = (
        "Someone requested a password reset for your Hypershop account.\n\n"
        f"Reset link: {link}\n\n"
        f"This link expires in {ttl_seconds // 60} minutes. "
        "If you didn't request this, you can safely ignore this email."
    )
    await _transport.send(
        to=email,
        subject="Reset your Hypershop password",
        text_body=body,
    )
    _logger.info("password_reset_email_sent", email_domain=email.split("@", 1)[-1])


async def _handle_password_changed_email(message: OutboxMessage) -> None:
    payload = message.payload
    email = str(payload["email"])
    body = (
        "Your Hypershop password was just changed.\n\n"
        "If this was you, no action is needed.\n"
        "If it wasn't, contact support immediately and reset your password."
    )
    await _transport.send(
        to=email,
        subject="Your Hypershop password was changed",
        text_body=body,
    )
    _logger.info("password_changed_email_sent", email_domain=email.split("@", 1)[-1])


def register_iam_handlers() -> None:
    """Idempotent registration. Safe to call multiple times (tests do)."""
    import contextlib

    for ev, fn in (
        (EVT_OTP_EMAIL_SEND, _handle_otp_email),
        (EVT_OTP_SMS_SEND, _handle_otp_sms),
        (EVT_PASSWORD_RESET_EMAIL_SEND, _handle_password_reset_email),
        (EVT_PASSWORD_CHANGED_EMAIL_SEND, _handle_password_changed_email),
    ):
        with contextlib.suppress(ValueError):
            register_handler(ev, fn)


register_iam_handlers()
