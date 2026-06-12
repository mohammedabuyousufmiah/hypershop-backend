"""Process-global binding for the active RerankerProvider.

Same pattern as the AI / formulary / SMS / WhatsApp registries.
"""

from __future__ import annotations

from threading import Lock

from app.modules.search.providers.base import RerankerProvider
from app.modules.search.providers.not_configured import NotConfiguredReranker

_lock = Lock()
_active: RerankerProvider = NotConfiguredReranker()


def bind_reranker(provider: RerankerProvider) -> None:
    global _active
    with _lock:
        _active = provider


def get_reranker() -> RerankerProvider:
    return _active


def reset_reranker_binding() -> None:
    """Test helper — restores the no-op default."""
    global _active
    with _lock:
        _active = NotConfiguredReranker()
