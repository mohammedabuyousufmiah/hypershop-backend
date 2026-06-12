"""Module 20 — AI provider adapter tests.

Covers:
- Adapter constructors refuse to build with empty API keys
- Factory returns NotConfigured for `none` / unknown kinds
- Factory builds OpenAIAdapter when configured
- Fallback chain: primary fails (retryable) → backup is tried
- Fallback chain: primary fails (NOT retryable, e.g. not configured) → no failover
"""

from __future__ import annotations

from typing import Any

import pytest

from app.core.errors import (
    IntegrationError,
    RateLimitedError,
    ServiceUnavailableError,
)


pytestmark = pytest.mark.integration


# ============================================================
# Adapter constructors — fail loud without keys
# ============================================================


def test_openai_adapter_refuses_empty_key() -> None:
    from app.modules.ai.providers.openai import OpenAIAdapter

    with pytest.raises(IntegrationError) as exc:
        OpenAIAdapter(api_key="")
    assert exc.value.details["missing_setting"] == "OPENAI_API_KEY"


def test_anthropic_adapter_refuses_empty_key() -> None:
    from app.modules.ai.providers.anthropic import AnthropicAdapter

    with pytest.raises(IntegrationError):
        AnthropicAdapter(api_key="")


def test_gemini_adapter_refuses_empty_key() -> None:
    from app.modules.ai.providers.gemini import GeminiAdapter

    with pytest.raises(IntegrationError):
        GeminiAdapter(api_key="")


# ============================================================
# Factory wiring
# ============================================================


def test_factory_returns_not_configured_when_kind_is_none() -> None:
    from app.core.config import get_settings
    from app.modules.ai.providers.factory import provider_from_settings
    from app.modules.ai.providers.not_configured import NotConfiguredProvider

    s = get_settings()
    original = s.ai_provider
    s.ai_provider = "none"
    try:
        p = provider_from_settings()
        assert isinstance(p, NotConfiguredProvider)
    finally:
        s.ai_provider = original


def test_factory_unknown_kind_falls_through_to_not_configured() -> None:
    from app.core.config import get_settings
    from app.modules.ai.providers.factory import provider_from_settings
    from app.modules.ai.providers.not_configured import NotConfiguredProvider

    s = get_settings()
    original = s.ai_provider
    s.ai_provider = "unicorn"
    try:
        p = provider_from_settings()
        assert isinstance(p, NotConfiguredProvider)
    finally:
        s.ai_provider = original


def test_factory_builds_openai_adapter_when_key_present() -> None:
    from pydantic import SecretStr

    from app.core.config import get_settings
    from app.modules.ai.providers.factory import provider_from_settings
    from app.modules.ai.providers.openai import OpenAIAdapter

    s = get_settings()
    orig_provider, orig_key, orig_backup = (
        s.ai_provider, s.openai_api_key, s.ai_backup_providers,
    )
    s.ai_provider = "openai"
    s.openai_api_key = SecretStr("sk-test-fake")
    s.ai_backup_providers = ""
    try:
        p = provider_from_settings()
        assert isinstance(p, OpenAIAdapter)
        assert p.name == "openai"
    finally:
        s.ai_provider = orig_provider
        s.openai_api_key = orig_key
        s.ai_backup_providers = orig_backup


def test_factory_builds_fallback_chain_with_backups() -> None:
    from pydantic import SecretStr

    from app.core.config import get_settings
    from app.modules.ai.providers.factory import provider_from_settings
    from app.modules.ai.providers.fallback import FallbackAIProvider

    s = get_settings()
    orig = (
        s.ai_provider, s.openai_api_key, s.anthropic_api_key,
        s.gemini_api_key, s.ai_backup_providers,
    )
    s.ai_provider = "openai"
    s.openai_api_key = SecretStr("sk-1")
    s.anthropic_api_key = SecretStr("sk-2")
    s.gemini_api_key = SecretStr("g-3")
    s.ai_backup_providers = "anthropic,gemini"
    try:
        p = provider_from_settings()
        assert isinstance(p, FallbackAIProvider)
        # Composed name reveals the chain.
        assert p.name == "openai+anthropic+gemini"
    finally:
        (s.ai_provider, s.openai_api_key, s.anthropic_api_key,
         s.gemini_api_key, s.ai_backup_providers) = orig


def test_factory_skips_unconfigured_backups() -> None:
    """If a backup is named but its API key is missing, it's silently
    skipped — the primary is still returned (no fallback wrapper).
    """
    from pydantic import SecretStr

    from app.core.config import get_settings
    from app.modules.ai.providers.factory import provider_from_settings
    from app.modules.ai.providers.openai import OpenAIAdapter

    s = get_settings()
    orig = (
        s.ai_provider, s.openai_api_key, s.anthropic_api_key,
        s.ai_backup_providers,
    )
    s.ai_provider = "openai"
    s.openai_api_key = SecretStr("sk-1")
    s.anthropic_api_key = SecretStr("")  # no key → skipped
    s.ai_backup_providers = "anthropic"
    try:
        p = provider_from_settings()
        assert isinstance(p, OpenAIAdapter)  # not Fallback
    finally:
        (s.ai_provider, s.openai_api_key, s.anthropic_api_key,
         s.ai_backup_providers) = orig


# ============================================================
# Fallback chain behaviour
# ============================================================


class _FailingProvider:
    """Test double that raises a configurable exception on every call."""

    def __init__(self, *, name: str, exc: Exception) -> None:
        self.name = name
        self._exc = exc

    async def ocr_prescription(self, req: Any) -> Any:
        raise self._exc

    async def suggest_medicines(self, req: Any) -> Any:
        raise self._exc

    async def predict_stock(self, req: Any) -> Any:
        raise self._exc

    async def detect_fraud(self, req: Any) -> Any:
        raise self._exc


class _OkProvider:
    """Test double that always returns a SuggestMedicinesResponse."""

    name = "ok"

    async def ocr_prescription(self, req: Any) -> Any:  # pragma: no cover
        raise NotImplementedError

    async def suggest_medicines(self, req: Any) -> Any:
        from app.modules.ai.providers.base import (
            SuggestedMedicine,
            SuggestMedicinesResponse,
        )
        return SuggestMedicinesResponse(
            suggestions=[SuggestedMedicine(
                suggested_generic="x", requires_prescription=False,
                confidence=0.9,
            )],
            confidence=0.9, raw_response={"ok": True},
            provider="ok",
        )

    async def predict_stock(self, req: Any) -> Any:  # pragma: no cover
        raise NotImplementedError

    async def detect_fraud(self, req: Any) -> Any:  # pragma: no cover
        raise NotImplementedError


@pytest.mark.asyncio
async def test_fallback_retries_on_service_unavailable() -> None:
    from app.modules.ai.providers.base import SuggestMedicinesRequest
    from app.modules.ai.providers.fallback import FallbackAIProvider

    primary = _FailingProvider(
        name="primary", exc=ServiceUnavailableError("upstream 503"),
    )
    backup = _OkProvider()
    chain = FallbackAIProvider(primary=primary, backups=[backup])
    resp = await chain.suggest_medicines(
        SuggestMedicinesRequest(symptoms="x"),
    )
    assert resp.provider == "ok"


@pytest.mark.asyncio
async def test_fallback_retries_on_rate_limit() -> None:
    from app.modules.ai.providers.base import SuggestMedicinesRequest
    from app.modules.ai.providers.fallback import FallbackAIProvider

    primary = _FailingProvider(
        name="primary", exc=RateLimitedError("429 rate limit"),
    )
    chain = FallbackAIProvider(primary=primary, backups=[_OkProvider()])
    resp = await chain.suggest_medicines(
        SuggestMedicinesRequest(symptoms="x"),
    )
    assert resp.provider == "ok"


@pytest.mark.asyncio
async def test_fallback_does_not_retry_on_not_configured_marker() -> None:
    """A NotConfigured-style IntegrationError carries
    ``details.missing_setting`` and must NOT trigger failover —
    operator misconfig should be visible, not papered over.
    """
    from app.modules.ai.providers.base import SuggestMedicinesRequest
    from app.modules.ai.providers.fallback import FallbackAIProvider

    primary = _FailingProvider(
        name="primary",
        exc=IntegrationError(
            "not configured",
            details={"missing_setting": "OPENAI_API_KEY"},
        ),
    )
    backup = _OkProvider()
    chain = FallbackAIProvider(primary=primary, backups=[backup])
    with pytest.raises(IntegrationError) as exc:
        await chain.suggest_medicines(
            SuggestMedicinesRequest(symptoms="x"),
        )
    assert exc.value.details["missing_setting"] == "OPENAI_API_KEY"


@pytest.mark.asyncio
async def test_fallback_chain_exhausted_raises_last_error() -> None:
    from app.modules.ai.providers.base import SuggestMedicinesRequest
    from app.modules.ai.providers.fallback import FallbackAIProvider

    primary = _FailingProvider(
        name="primary", exc=ServiceUnavailableError("primary down"),
    )
    backup1 = _FailingProvider(
        name="b1", exc=ServiceUnavailableError("backup1 down"),
    )
    backup2 = _FailingProvider(
        name="b2", exc=ServiceUnavailableError("backup2 down"),
    )
    chain = FallbackAIProvider(primary=primary, backups=[backup1, backup2])
    with pytest.raises(ServiceUnavailableError) as exc:
        await chain.suggest_medicines(
            SuggestMedicinesRequest(symptoms="x"),
        )
    # The LAST error in the chain is what surfaces.
    assert "backup2" in str(exc.value)
