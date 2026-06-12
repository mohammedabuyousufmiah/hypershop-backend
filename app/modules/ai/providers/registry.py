"""Process-global binding for the active :class:`AIProvider`.

The application's startup hook (``main.py``) calls :func:`bind_provider`
once a real adapter is ready. Tests can rebind to an in-process fake
via the same function — but in this case the fake is constructed by
the test itself, NOT shipped as part of the service. That preserves the
"no stubs in production code" rule.
"""

from __future__ import annotations

from threading import Lock

from app.modules.ai.providers.base import AIProvider
from app.modules.ai.providers.not_configured import NotConfiguredProvider

_lock = Lock()
_active: AIProvider = NotConfiguredProvider()


def bind_provider(provider: AIProvider) -> None:
    global _active
    with _lock:
        _active = provider


def get_provider() -> AIProvider:
    return _active


def reset_provider_binding() -> None:
    """Test helper — restores the default not-configured binding."""
    global _active
    with _lock:
        _active = NotConfiguredProvider()
