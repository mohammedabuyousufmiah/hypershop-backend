"""Test-only fake AI provider.

Lives under ``tests/`` (NOT under ``providers/``) so it cannot be
imported by production code paths. Tests bind it explicitly with
:func:`bind_provider`. The fixture in :mod:`conftest` resets the
binding back to ``NotConfiguredProvider`` after every test, so a fake
binding never leaks across tests.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.modules.ai.providers.base import (
    AIProvider,
    FraudDetectionRequest,
    FraudDetectionResponse,
    FraudSignal,
    OcrLineItem,
    OcrRequest,
    OcrResponse,
    StockPredictionRequest,
    StockPredictionResponse,
    SuggestedMedicine,
    SuggestMedicinesRequest,
    SuggestMedicinesResponse,
)


class FakeAIProvider(AIProvider):
    """Returns deterministic structured output. Never used in production
    code. The shipped :class:`NotConfiguredProvider` is the only default
    binding.
    """

    name = "fake_test_provider"

    async def ocr_prescription(self, req: OcrRequest) -> OcrResponse:
        return OcrResponse(
            doctor_name="Dr Fake",
            issued_on=date.today(),
            patient_name="Test Patient",
            diagnosis="cough",
            items=[
                OcrLineItem(
                    medicine_name="Napa",
                    dosage="500mg",
                    frequency="1+0+1",
                    duration="5 days",
                    confidence=0.92,
                ),
            ],
            confidence=0.91,
            raw_text="prescription text",
            raw_response={"fake": True},
            provider=self.name,
            model="fake-ocr-1",
            cost_units=Decimal("0.001"),
        )

    async def suggest_medicines(
        self, req: SuggestMedicinesRequest,
    ) -> SuggestMedicinesResponse:
        return SuggestMedicinesResponse(
            suggestions=[
                SuggestedMedicine(
                    suggested_generic="paracetamol",
                    suggested_strength="500mg",
                    rationale="symptomatic relief",
                    requires_prescription=False,
                    confidence=0.88,
                ),
            ],
            confidence=0.85,
            raw_response={"fake": True},
            provider=self.name,
            model="fake-suggest-1",
            cost_units=Decimal("0.0005"),
        )

    async def predict_stock(
        self, req: StockPredictionRequest,
    ) -> StockPredictionResponse:
        return StockPredictionResponse(
            variant_id=req.variant_id,
            horizon_days=req.horizon_days,
            predicted_units_consumed=42,
            predicted_depletion_date=date.today(),
            recommended_reorder_units=100,
            confidence=0.7,
            rationale="seasonal trend",
            raw_response={"fake": True},
            provider=self.name,
            model="fake-stock-1",
            cost_units=Decimal("0.0001"),
        )

    async def detect_fraud(
        self, req: FraudDetectionRequest,
    ) -> FraudDetectionResponse:
        return FraudDetectionResponse(
            order_id=req.order_id,
            risk_score=72,
            recommendation="review",
            signals=[
                FraudSignal(
                    code="rapid_repeat_orders",
                    severity="medium",
                    detail="3 orders in 5 minutes",
                ),
            ],
            confidence=0.82,
            raw_response={"fake": True},
            provider=self.name,
            model="fake-fraud-1",
            cost_units=Decimal("0.0002"),
        )
