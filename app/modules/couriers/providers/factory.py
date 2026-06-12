"""Courier provider registry.

For M2.A skeleton, every code resolves to a NotConfiguredCourierProvider.
M2.B provider modules will call ``register_provider`` at startup once
real adapters are available.
"""
from __future__ import annotations

from app.modules.couriers.providers.base import CourierProvider
from app.modules.couriers.providers.not_configured import (
    NotConfiguredCourierProvider,
)

# Cache: code -> bound provider (lazy).
_PROVIDERS: dict[str, CourierProvider] = {}


def get_provider(code: str) -> CourierProvider:
    """Returns NotConfigured for any code that has no active
    credentials. Real provider classes will register themselves in M2.B."""
    if code in _PROVIDERS:
        return _PROVIDERS[code]
    _PROVIDERS[code] = NotConfiguredCourierProvider(code)
    return _PROVIDERS[code]


def register_provider(code: str, instance: CourierProvider) -> None:
    """Used by M2.B provider modules at startup to swap in the real
    impl once credentials are loaded."""
    _PROVIDERS[code] = instance


def unregister_provider(code: str) -> None:
    """Drop a binding (used by tests + admin disable-flow)."""
    _PROVIDERS.pop(code, None)
