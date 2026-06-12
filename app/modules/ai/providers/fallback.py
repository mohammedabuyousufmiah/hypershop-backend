"""Fallback chain wrapper.

Wraps a primary :class:`AIProvider` plus an ordered list of backups.
On a retryable failure (``IntegrationError``, ``ServiceUnavailableError``,
``RateLimitedError``) the wrapper tries the next provider in the chain.
The original ``AIProposalKind`` is preserved so the AI service still
records the call against the right capability.

What counts as retryable:
  - ``ServiceUnavailableError``  (5xx, timeouts)
  - ``RateLimitedError``          (429s)
  - ``IntegrationError`` whose ``code != 'integration_error'`` (i.e.
    not the "not configured" sentinel)

Why **not** retry on the not-configured error: failing over from a
not-configured primary to a real backup would silently change the
provider for every call. Better to surface the misconfiguration once
than to disguise it.
"""

from __future__ import annotations

from typing import Awaitable, Callable, TypeVar

from app.core.errors import (
    IntegrationError,
    RateLimitedError,
    ServiceUnavailableError,
)
from app.core.logging import get_logger
from app.modules.ai.providers.base import (
    AIProvider,
    FraudDetectionRequest,
    FraudDetectionResponse,
    OcrRequest,
    OcrResponse,
    StockPredictionRequest,
    StockPredictionResponse,
    SuggestMedicinesRequest,
    SuggestMedicinesResponse,
)

_logger = get_logger("hypershop.ai.fallback")

T = TypeVar("T")


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, (ServiceUnavailableError, RateLimitedError)):
        return True
    if isinstance(exc, IntegrationError):
        # Don't retry the explicit "not configured" sentinel — operator
        # misconfig should be visible, not papered over.
        details = getattr(exc, "details", {}) or {}
        if details.get("missing_setting"):
            return False
        return True
    return False


class FallbackAIProvider(AIProvider):
    name = "fallback"

    def __init__(
        self, *,
        primary: AIProvider,
        backups: list[AIProvider],
    ) -> None:
        self._primary = primary
        self._backups = backups
        self.name = "+".join(
            [primary.name, *(b.name for b in backups)],
        )

    async def _try_chain(
        self, capability: str,
        call: Callable[[AIProvider], Awaitable[T]],
    ) -> T:
        chain: list[AIProvider] = [self._primary, *self._backups]
        last_exc: Exception | None = None
        for idx, provider in enumerate(chain):
            try:
                return await call(provider)
            except Exception as exc:
                last_exc = exc
                if not _is_retryable(exc):
                    raise
                _logger.warning(
                    "ai_fallback_skip",
                    capability=capability,
                    attempted=provider.name,
                    error_type=type(exc).__name__,
                    error=str(exc)[:200],
                    next_in_chain=(
                        chain[idx + 1].name if idx + 1 < len(chain) else None
                    ),
                )
        # Exhausted the chain. Re-raise the last error so the AI service
        # records the failure with the most informative cause.
        assert last_exc is not None
        raise last_exc

    # ------------------------------------------------------------------
    # Capability proxies
    # ------------------------------------------------------------------

    async def ocr_prescription(self, req: OcrRequest) -> OcrResponse:
        return await self._try_chain(
            "ocr_prescription", lambda p: p.ocr_prescription(req),
        )

    async def suggest_medicines(
        self, req: SuggestMedicinesRequest,
    ) -> SuggestMedicinesResponse:
        return await self._try_chain(
            "suggest_medicines", lambda p: p.suggest_medicines(req),
        )

    async def predict_stock(
        self, req: StockPredictionRequest,
    ) -> StockPredictionResponse:
        return await self._try_chain(
            "predict_stock", lambda p: p.predict_stock(req),
        )

    async def detect_fraud(
        self, req: FraudDetectionRequest,
    ) -> FraudDetectionResponse:
        return await self._try_chain(
            "detect_fraud", lambda p: p.detect_fraud(req),
        )
