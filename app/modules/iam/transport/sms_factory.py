"""Env-driven binding for the SMS transport.

Called once at app startup (see ``app.main`` lifespan). Reads
``settings.sms_provider`` and binds the matching adapter, or leaves the
default ``NotConfiguredSmsTransport`` in place.

Supported provider names (case-insensitive):
  - ``bulksmsbd``    → BulkSmsBdTransport (BD-native)
  - ``ssl_wireless`` → SslWirelessTransport (BD enterprise)
  - ``twilio``       → TwilioTransport (international fallback)
  - ``none``         → NotConfiguredSmsTransport (default)
"""

from __future__ import annotations

from app.core.logging import get_logger
from app.modules.iam.transport.sms_base import SmsTransport
from app.modules.iam.transport.sms_not_configured import (
    NotConfiguredSmsTransport,
)
from app.modules.iam.transport.sms_registry import bind_transport

_logger = get_logger("hypershop.iam.sms.factory")

_KIND_BULKSMSBD = "bulksmsbd"
_KIND_SSL_WIRELESS = "ssl_wireless"
_KIND_TWILIO = "twilio"
_KIND_LOG = "log"  # dev-only — see sms_log.py header for gate


def _secret(value: object) -> str:
    if value is None:
        return ""
    get = getattr(value, "get_secret_value", None)
    if callable(get):
        return str(get() or "")
    return str(value)


def transport_from_settings() -> SmsTransport:
    """Construct + return the configured transport (caller binds)."""
    from app.core.config import get_settings
    from app.core.errors import IntegrationError

    s = get_settings()
    kind = (getattr(s, "sms_provider", None) or "none").lower()
    if kind in ("", "none", "not_configured"):
        return NotConfiguredSmsTransport()  # type: ignore[return-value]

    if kind == _KIND_LOG:
        # Hard production guard — never log raw OTPs on a real deploy.
        env = (getattr(s, "environment", "") or "").lower()
        if env == "production":
            _logger.warning("sms_log_transport_refused_in_production", environment=env)
            return NotConfiguredSmsTransport()  # type: ignore[return-value]
        from app.modules.iam.transport.sms_log import LogOnlySmsTransport
        _logger.info("sms_log_transport_bound", environment=env)
        return LogOnlySmsTransport()  # type: ignore[return-value]

    try:
        if kind == _KIND_BULKSMSBD:
            from app.modules.iam.transport.sms_bulksmsbd import BulkSmsBdTransport
            return BulkSmsBdTransport(  # type: ignore[return-value]
                api_key=_secret(getattr(s, "bulksmsbd_api_key", None)),
                sender_id=_secret(getattr(s, "bulksmsbd_sender_id", None)),
                base_url=getattr(s, "bulksmsbd_base_url", None) or None,
            )
        if kind == _KIND_SSL_WIRELESS:
            from app.modules.iam.transport.sms_ssl_wireless import SslWirelessTransport
            return SslWirelessTransport(  # type: ignore[return-value]
                api_token=_secret(getattr(s, "ssl_wireless_api_token", None)),
                sid=_secret(getattr(s, "ssl_wireless_sid", None)),
                base_url=getattr(s, "ssl_wireless_base_url", None) or None,
            )
        if kind == _KIND_TWILIO:
            from app.modules.iam.transport.sms_twilio import TwilioTransport
            return TwilioTransport(  # type: ignore[return-value]
                account_sid=_secret(getattr(s, "twilio_account_sid", None)),
                auth_token=_secret(getattr(s, "twilio_auth_token", None)),
                from_number=_secret(getattr(s, "twilio_from_number", None)),
                base_url=getattr(s, "twilio_base_url", None) or None,
            )
    except IntegrationError as e:
        _logger.warning(
            "sms_provider_skipped",
            kind=kind, reason=str(e),
        )
        return NotConfiguredSmsTransport()  # type: ignore[return-value]

    _logger.warning("sms_provider_unknown_kind", kind=kind)
    return NotConfiguredSmsTransport()  # type: ignore[return-value]


def bind_from_settings() -> SmsTransport:
    """Construct + bind in one shot. Returns the bound transport so the
    caller can log its name.
    """
    t = transport_from_settings()
    bind_transport(t)
    return t
