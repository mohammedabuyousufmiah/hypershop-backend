"""Env-driven AI provider binding.

Reads:
    AI_PROVIDER          primary, one of {openai|anthropic|gemini|none}
    AI_BACKUP_PROVIDERS  comma-sep list of backups in failover order
    OPENAI_API_KEY       (when openai is in the chain)
    ANTHROPIC_API_KEY    (when anthropic is in the chain)
    GEMINI_API_KEY       (when gemini is in the chain)
    OPENAI_MODEL_DEFAULT, ANTHROPIC_MODEL_DEFAULT, GEMINI_MODEL_DEFAULT
    OPENAI_BASE_URL,     ANTHROPIC_BASE_URL,     GEMINI_BASE_URL

Examples:
    AI_PROVIDER=openai
    AI_BACKUP_PROVIDERS=anthropic,gemini

If the primary is ``none`` (or unset), the binding stays
:class:`NotConfiguredProvider` and every call returns 502 with a clear
message — no backup chain is consulted.

A bad provider name in the chain (e.g. ``AI_PROVIDER=unicorn``) is
logged at WARNING and falls through to NotConfigured for the primary.
Unknown backup names are skipped.
"""

from __future__ import annotations

from app.core.errors import IntegrationError
from app.core.logging import get_logger
from app.modules.ai.providers.base import AIProvider
from app.modules.ai.providers.fallback import FallbackAIProvider
from app.modules.ai.providers.not_configured import NotConfiguredProvider
from app.modules.ai.providers.registry import bind_provider

_logger = get_logger("hypershop.ai.factory")


def _secret(value: object) -> str:
    """Unwrap a Pydantic SecretStr (or pass through a plain str)."""
    if value is None:
        return ""
    get = getattr(value, "get_secret_value", None)
    if callable(get):
        return str(get() or "")
    return str(value)


def _build_one(kind: str) -> AIProvider | None:
    """Construct one named adapter, or None if unknown / unconfigured."""
    from app.core.config import get_settings

    s = get_settings()
    kind = (kind or "").lower().strip()

    if kind in ("", "none", "not_configured"):
        return None

    if kind == "openai":
        try:
            from app.modules.ai.providers.openai import OpenAIAdapter
            return OpenAIAdapter(
                api_key=_secret(getattr(s, "openai_api_key", None)),
                base_url=getattr(s, "openai_base_url", None) or None,
                default_model=getattr(s, "openai_model_default", None) or None,
            )
        except IntegrationError as e:
            _logger.warning(
                "ai_provider_skipped",
                provider="openai", reason=str(e),
            )
            return None

    if kind == "anthropic":
        try:
            from app.modules.ai.providers.anthropic import AnthropicAdapter
            return AnthropicAdapter(
                api_key=_secret(getattr(s, "anthropic_api_key", None)),
                base_url=getattr(s, "anthropic_base_url", None) or None,
                default_model=getattr(s, "anthropic_model_default", None) or None,
            )
        except IntegrationError as e:
            _logger.warning(
                "ai_provider_skipped",
                provider="anthropic", reason=str(e),
            )
            return None

    if kind == "gemini":
        try:
            from app.modules.ai.providers.gemini import GeminiAdapter
            return GeminiAdapter(
                api_key=_secret(getattr(s, "gemini_api_key", None)),
                base_url=getattr(s, "gemini_base_url", None) or None,
                default_model=getattr(s, "gemini_model_default", None) or None,
            )
        except IntegrationError as e:
            _logger.warning(
                "ai_provider_skipped",
                provider="gemini", reason=str(e),
            )
            return None

    _logger.warning("ai_provider_unknown_kind", kind=kind)
    return None


def provider_from_settings() -> AIProvider:
    """Construct the configured provider chain.

    - Primary missing or none → NotConfiguredProvider (no backups consulted).
    - Primary configured + no backups → primary directly.
    - Primary + backups configured → :class:`FallbackAIProvider` wrapper.
    """
    from app.core.config import get_settings

    s = get_settings()
    primary_kind = getattr(s, "ai_provider", None) or "none"
    primary = _build_one(primary_kind)
    if primary is None:
        return NotConfiguredProvider()

    backup_csv = getattr(s, "ai_backup_providers", "") or ""
    backup_kinds = [b.strip() for b in backup_csv.split(",") if b.strip()]
    backups: list[AIProvider] = []
    for kind in backup_kinds:
        adapter = _build_one(kind)
        if adapter is not None:
            backups.append(adapter)

    if not backups:
        return primary
    return FallbackAIProvider(primary=primary, backups=backups)


def bind_from_settings() -> AIProvider:
    """Construct + bind in one shot. Returns the bound provider so the
    startup hook can log its name (e.g. "openai+anthropic+gemini").
    """
    p = provider_from_settings()
    bind_provider(p)
    return p
