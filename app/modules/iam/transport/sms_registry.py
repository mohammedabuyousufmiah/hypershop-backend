"""Process-global binding for the active :class:`SmsTransport`.

Mirrors the AI / formulary / payments registry pattern. The handler in
``app.modules.iam.handlers`` calls ``get_transport()`` per OTP dispatch
without caring which adapter is bound — switch providers via env +
restart.
"""

from __future__ import annotations

from threading import Lock

from app.modules.iam.transport.sms_base import SmsTransport
from app.modules.iam.transport.sms_not_configured import (
    NotConfiguredSmsTransport,
)

_lock = Lock()
_active: SmsTransport = NotConfiguredSmsTransport()  # type: ignore[assignment]


def bind_transport(transport: SmsTransport) -> None:
    global _active
    with _lock:
        _active = transport


def get_transport() -> SmsTransport:
    return _active


def reset_transport_binding() -> None:
    """Test helper — restores the default not-configured binding."""
    global _active
    with _lock:
        _active = NotConfiguredSmsTransport()  # type: ignore[assignment]
