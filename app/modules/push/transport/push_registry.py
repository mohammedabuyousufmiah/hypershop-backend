"""Process-global binding for push transports — one slot per kind.

Unlike the SMS/WhatsApp registries (single-active), push needs both
FCM (Android+Web) AND APNS (iOS) bound at once. The dispatch service
asks for the transport that matches each device row's ``kind``.
"""

from __future__ import annotations

from threading import Lock

from app.modules.push.transport.push_base import PushTransport
from app.modules.push.transport.push_not_configured import (
    NotConfiguredPushTransport,
)

_lock = Lock()
# kind ('fcm' | 'apns' | 'web') → bound transport
_active: dict[str, PushTransport] = {}


def bind_transport(kind: str, transport: PushTransport) -> None:
    with _lock:
        _active[kind] = transport


def get_transport(kind: str) -> PushTransport:
    with _lock:
        return _active.get(kind) or NotConfiguredPushTransport()  # type: ignore[return-value]


def list_bound_kinds() -> list[str]:
    with _lock:
        return sorted(_active.keys())


def reset_transport_binding() -> None:
    """Test helper — clears all bindings."""
    with _lock:
        _active.clear()
