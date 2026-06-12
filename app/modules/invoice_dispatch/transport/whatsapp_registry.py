"""Process-global binding for the active :class:`WhatsAppTransport`.

Same shape as the SMS / payments / formulary registries.
"""

from __future__ import annotations

from threading import Lock

from app.modules.invoice_dispatch.transport.whatsapp_base import WhatsAppTransport
from app.modules.invoice_dispatch.transport.whatsapp_not_configured import (
    NotConfiguredWhatsAppTransport,
)

_lock = Lock()
_active: WhatsAppTransport = NotConfiguredWhatsAppTransport()  # type: ignore[assignment]


def bind_transport(transport: WhatsAppTransport) -> None:
    global _active
    with _lock:
        _active = transport


def get_transport() -> WhatsAppTransport:
    return _active


def reset_transport_binding() -> None:
    """Test helper — restores the default not-configured binding."""
    global _active
    with _lock:
        _active = NotConfiguredWhatsAppTransport()  # type: ignore[assignment]
