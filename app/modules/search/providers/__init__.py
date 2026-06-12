"""Re-exports — other modules should write
``from app.modules.search.providers import get_reranker``
not reach into ``.registry``.
"""

from __future__ import annotations

from app.modules.search.providers.factory import bind_from_settings
from app.modules.search.providers.registry import (
    bind_reranker,
    get_reranker,
    reset_reranker_binding,
)

__all__ = [
    "bind_from_settings",
    "bind_reranker",
    "get_reranker",
    "reset_reranker_binding",
]
