"""Env-driven binding for the WhatsApp transport.

Supported provider names (case-insensitive):
  - ``meta_cloud`` → MetaCloudWhatsAppTransport (Meta's official Cloud API,
    https://graph.facebook.com/.../{PHONE_NUMBER_ID}/messages)
  - ``none``       → NotConfiguredWhatsAppTransport (graceful no-op,
    routes to SMS fallback at the dispatcher layer)
"""

from __future__ import annotations

from app.core.logging import get_logger
from app.modules.invoice_dispatch.transport.whatsapp_base import WhatsAppTransport
from app.modules.invoice_dispatch.transport.whatsapp_not_configured import (
    NotConfiguredWhatsAppTransport,
)
from app.modules.invoice_dispatch.transport.whatsapp_registry import bind_transport

_logger = get_logger("hypershop.invoice_dispatch.whatsapp.factory")

_KIND_META_CLOUD = "meta_cloud"
_KIND_LOG = "log"  # dev-only — see whatsapp_log.py header for gate


def _secret(value: object) -> str:
    if value is None:
        return ""
    get = getattr(value, "get_secret_value", None)
    if callable(get):
        return str(get() or "")
    return str(value)


def transport_from_settings() -> WhatsAppTransport:
    from app.core.config import get_settings

    s = get_settings()
    kind = (getattr(s, "whatsapp_provider", None) or "none").lower()
    if kind in ("", "none", "not_configured"):
        return NotConfiguredWhatsAppTransport()  # type: ignore[return-value]

    if kind == _KIND_LOG:
        env = (getattr(s, "environment", "") or "").lower()
        if env == "production":
            _logger.warning("whatsapp_log_transport_refused_in_production", environment=env)
            return NotConfiguredWhatsAppTransport()  # type: ignore[return-value]
        from app.modules.invoice_dispatch.transport.whatsapp_log import (
            LogOnlyWhatsAppTransport,
        )
        _logger.info("whatsapp_log_transport_bound", environment=env)
        return LogOnlyWhatsAppTransport()  # type: ignore[return-value]

    if kind == _KIND_META_CLOUD:
        access_token = _secret(getattr(s, "meta_whatsapp_access_token", None))
        phone_number_id = _secret(getattr(s, "meta_whatsapp_phone_number_id", None))
        api_version = getattr(s, "meta_whatsapp_api_version", None) or "v21.0"
        if not access_token or not phone_number_id:
            _logger.warning(
                "whatsapp_meta_cloud_skipped_missing_creds",
                has_access_token=bool(access_token),
                has_phone_number_id=bool(phone_number_id),
            )
            return NotConfiguredWhatsAppTransport()  # type: ignore[return-value]
        from app.modules.invoice_dispatch.transport.whatsapp_meta_cloud import (
            MetaCloudWhatsAppTransport,
        )
        return MetaCloudWhatsAppTransport(  # type: ignore[return-value]
            access_token=access_token,
            phone_number_id=phone_number_id,
            api_version=api_version,
        )

    _logger.warning("whatsapp_provider_unknown_kind", kind=kind)
    return NotConfiguredWhatsAppTransport()  # type: ignore[return-value]


def bind_from_settings() -> WhatsAppTransport:
    """Construct + bind in one shot. Returns the bound transport so the
    caller can log its name.
    """
    t = transport_from_settings()
    bind_transport(t)
    return t
