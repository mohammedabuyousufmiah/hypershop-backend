"""Payment provider port + adapter selection.

Re-exports the registry helpers so other modules write
``from app.modules.payments.providers import get_provider``
instead of reaching into ``.registry``.
"""

from __future__ import annotations

from app.modules.payments.providers.factory import bind_from_settings
from app.modules.payments.providers.registry import (
    bind_provider,
    get_provider,
    reset_provider_binding,
)

__all__ = [
    "bind_from_settings",
    "bind_provider",
    "get_provider",
    "reset_provider_binding",
]
