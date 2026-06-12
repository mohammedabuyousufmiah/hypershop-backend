"""Process-global binding for payment providers.

Unlike AI/formulary which only need ONE active provider, payments
allows multiple bound at once (one per gateway name). This is because
a captured Bkash intent must be refunded via Bkash, even if the
operator later switches the *default* provider to SSLCommerz. We never
"migrate" an intent's provider mid-life.
"""

from __future__ import annotations

from threading import Lock

from app.modules.payments.providers.base import PaymentProvider
from app.modules.payments.providers.not_configured import (
    NotConfiguredPaymentProvider,
)

_lock = Lock()
# provider_name → adapter instance
_active: dict[str, PaymentProvider] = {}
# Default provider name (the one used when initiating a fresh payment
# without specifying a provider). Falls back to NotConfigured.
_default: str | None = None


def bind_provider(name: str, provider: PaymentProvider) -> None:
    """Register ``provider`` under ``name``. Subsequent ``get_provider(name)``
    returns this instance."""
    with _lock:
        _active[name] = provider


def set_default_provider(name: str) -> None:
    """Set the provider returned by ``get_provider()`` (no name)."""
    global _default
    with _lock:
        _default = name


def get_provider(name: str | None = None) -> PaymentProvider:
    """Lookup a bound provider. If ``name`` is None, returns the default
    (or NotConfigured if no default set). If a named provider is missing,
    returns NotConfigured — never raises, since the service layer turns
    NotConfigured calls into 502.
    """
    with _lock:
        if name is None:
            name = _default
        if name is None:
            return NotConfiguredPaymentProvider()
        return _active.get(name) or NotConfiguredPaymentProvider()


def list_bound_providers() -> list[str]:
    with _lock:
        return sorted(_active.keys())


def get_default_name() -> str | None:
    with _lock:
        return _default


def reset_provider_binding() -> None:
    """Test helper — clears all bindings."""
    global _default
    with _lock:
        _active.clear()
        _default = None
