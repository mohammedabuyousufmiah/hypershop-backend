"""Env-driven binding for the search reranker.

Supported provider names (case-insensitive):
  - ``external_ml`` → ExternalMlReranker (BYO HTTP endpoint)
  - ``none``       → NotConfiguredReranker (graceful no-op,
                     keeps local rank)
"""

from __future__ import annotations

from app.core.logging import get_logger
from app.modules.search.providers.base import RerankerProvider
from app.modules.search.providers.not_configured import NotConfiguredReranker
from app.modules.search.providers.registry import bind_reranker

_logger = get_logger("hypershop.search.reranker.factory")


def _secret(value: object) -> str:
    if value is None:
        return ""
    get = getattr(value, "get_secret_value", None)
    if callable(get):
        return str(get() or "")
    return str(value)


def reranker_from_settings() -> RerankerProvider:
    from app.core.config import get_settings
    from app.core.errors import IntegrationError

    s = get_settings()
    kind = (getattr(s, "search_rerank_provider", None) or "none").lower()
    if kind in ("", "none", "not_configured"):
        return NotConfiguredReranker()
    if kind == "external_ml":
        api_url = getattr(s, "search_rerank_api_url", None) or ""
        if not api_url:
            _logger.warning("search_reranker_skipped_missing_url")
            return NotConfiguredReranker()
        try:
            from app.modules.search.providers.external_ml import ExternalMlReranker
            return ExternalMlReranker(
                api_url=api_url,
                api_token=_secret(getattr(s, "search_rerank_api_token", None)),
                auth_header=getattr(s, "search_rerank_api_auth_header", None) or "Authorization",
                auth_scheme=getattr(s, "search_rerank_api_auth_scheme", None) or "Bearer",
                method=getattr(s, "search_rerank_api_method", None) or "POST",
                static_headers_json=_secret(
                    getattr(s, "search_rerank_api_static_headers_json", None),
                ) or None,
                timeout_s=float(getattr(s, "search_rerank_timeout_s", 8.0)),
            )
        except IntegrationError as e:
            _logger.warning("search_reranker_bind_failed", reason=str(e))
            return NotConfiguredReranker()
    _logger.warning("search_reranker_unknown_kind", kind=kind)
    return NotConfiguredReranker()


def bind_from_settings() -> RerankerProvider:
    p = reranker_from_settings()
    bind_reranker(p)
    return p
