"""``NotConfiguredProvider`` — the default binding when no AI vendor
credentials are present.

Every capability raises :class:`IntegrationError` with a message naming
the missing setting and the env var to populate. **This is intentional
and is NOT a fake/stub** — silently returning invented OCR text or a
made-up fraud score would let bugs ship with confidence values that
look real. Failing loud forces the operator to bind a real provider
before the AI surface can be used.

To wire a real provider, implement :class:`AIProvider` (e.g. an OpenAI
adapter) and call :func:`bind_provider` at application startup once the
vendor SDK + API key are in place.
"""

from __future__ import annotations

from app.core.errors import IntegrationError
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


def _not_configured(capability: str) -> "IntegrationError":
    return IntegrationError(
        message=(
            f"AI capability '{capability}' is not configured. Bind a real "
            "provider via app.modules.ai.providers.bind_provider() during "
            "application startup, and set AI_PROVIDER + the provider's "
            "credentials in the environment."
        ),
        details={"capability": capability, "missing_setting": "AI_PROVIDER"},
    )


class NotConfiguredProvider(AIProvider):
    name = "not_configured"

    async def ocr_prescription(self, req: OcrRequest) -> OcrResponse:
        raise _not_configured("ocr_prescription")

    async def suggest_medicines(
        self, req: SuggestMedicinesRequest,
    ) -> SuggestMedicinesResponse:
        raise _not_configured("suggest_medicines")

    async def predict_stock(
        self, req: StockPredictionRequest,
    ) -> StockPredictionResponse:
        raise _not_configured("predict_stock")

    async def detect_fraud(
        self, req: FraudDetectionRequest,
    ) -> FraudDetectionResponse:
        raise _not_configured("detect_fraud")
