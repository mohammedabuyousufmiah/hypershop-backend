"""AI provider port + binding.

The service layer talks to :class:`AIProvider` exclusively. Concrete
adapters (OpenAI, Anthropic, Azure, Vertex) implement the port. The
binding for "which provider is active" comes from settings via
:func:`get_provider`.

**No fake/stub adapter ships with the service.** When no provider is
configured, :class:`NotConfiguredProvider` is bound; every call raises
``IntegrationError`` (502) with an explicit message naming the missing
config key. This is deliberate — we never want the service to silently
return invented OCR text or a fake fraud score that downstream code
could mistake for real signal.
"""

from __future__ import annotations

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
from app.modules.ai.providers.factory import (
    bind_from_settings,
    provider_from_settings,
)
from app.modules.ai.providers.fallback import FallbackAIProvider
from app.modules.ai.providers.not_configured import NotConfiguredProvider
from app.modules.ai.providers.registry import (
    bind_provider,
    get_provider,
    reset_provider_binding,
)

__all__ = [
    "AIProvider",
    "FallbackAIProvider",
    "FraudDetectionRequest",
    "FraudDetectionResponse",
    "NotConfiguredProvider",
    "OcrRequest",
    "OcrResponse",
    "StockPredictionRequest",
    "StockPredictionResponse",
    "SuggestMedicinesRequest",
    "SuggestMedicinesResponse",
    "bind_from_settings",
    "bind_provider",
    "get_provider",
    "provider_from_settings",
    "reset_provider_binding",
]
