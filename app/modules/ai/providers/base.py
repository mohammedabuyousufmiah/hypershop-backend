"""Abstract :class:`AIProvider` port + per-capability request/response DTOs.

Concrete adapters in sibling modules implement this interface. The
service layer never branches on provider identity — capability methods
are uniform.

Each capability method:

- Takes a strongly-typed request DTO.
- Returns a strongly-typed response DTO carrying the AI's structured
  output PLUS a ``confidence`` (0.0-1.0) and the raw provider response
  for the audit trail.
- Must be idempotent at the provider level OR carry a request_id so
  retries don't duplicate billable calls.
- Must return within ``timeout_seconds``; the service layer wraps in
  :class:`asyncio.TimeoutError` → ``ServiceUnavailableError``.

Rate-limit / quota errors should surface as
``app.core.errors.RateLimitedError`` so the API returns 429 cleanly.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class _AIDto(BaseModel):
    model_config = ConfigDict(
        extra="forbid", str_strip_whitespace=True,
    )


# ---------------- OCR ----------------


class OcrLineItem(_AIDto):
    medicine_name: str
    dosage: str | None = None
    frequency: str | None = None
    duration: str | None = None
    notes: str | None = None
    confidence: float = Field(..., ge=0.0, le=1.0)


class OcrRequest(_AIDto):
    image_bytes: bytes
    image_mime: str = Field(..., min_length=3, max_length=64)
    hint: str | None = Field(default=None, max_length=512)
    request_id: str | None = None


class OcrResponse(_AIDto):
    doctor_name: str | None = None
    issued_on: date | None = None
    patient_name: str | None = None
    diagnosis: str | None = None
    items: list[OcrLineItem] = Field(default_factory=list)
    confidence: float = Field(..., ge=0.0, le=1.0)
    raw_text: str | None = None
    raw_response: dict[str, Any] = Field(default_factory=dict)
    provider: str
    model: str | None = None
    cost_units: Decimal | None = None  # provider's billing unit (tokens, pages, …)


# ---------------- Suggest medicines ----------------


class SuggestMedicinesRequest(_AIDto):
    symptoms: str = Field(..., min_length=1, max_length=2048)
    patient_age_years: int | None = Field(default=None, ge=0, le=150)
    patient_sex: str | None = Field(
        default=None, pattern=r"^(male|female|other)$",
    )
    catalog_filter_generic: str | None = None
    rx_only: bool | None = None
    request_id: str | None = None


class SuggestedMedicine(_AIDto):
    variant_id: UUID | None = None  # null = AI suggested an item not in catalog
    suggested_generic: str
    suggested_strength: str | None = None
    rationale: str | None = None
    requires_prescription: bool
    confidence: float = Field(..., ge=0.0, le=1.0)


class SuggestMedicinesResponse(_AIDto):
    suggestions: list[SuggestedMedicine] = Field(default_factory=list)
    confidence: float = Field(..., ge=0.0, le=1.0)
    raw_response: dict[str, Any] = Field(default_factory=dict)
    provider: str
    model: str | None = None
    cost_units: Decimal | None = None


# ---------------- Stock prediction ----------------


class StockPredictionRequest(_AIDto):
    variant_id: UUID
    horizon_days: int = Field(default=30, ge=1, le=365)
    history_days: int = Field(default=180, ge=7, le=730)
    request_id: str | None = None


class StockPredictionResponse(_AIDto):
    variant_id: UUID
    horizon_days: int
    predicted_units_consumed: int
    predicted_depletion_date: date | None
    recommended_reorder_units: int
    confidence: float = Field(..., ge=0.0, le=1.0)
    rationale: str | None = None
    raw_response: dict[str, Any] = Field(default_factory=dict)
    provider: str
    model: str | None = None
    cost_units: Decimal | None = None


# ---------------- Fraud detection ----------------


class FraudSignal(_AIDto):
    code: str  # e.g. "address_mismatch", "rapid_repeat_orders"
    severity: str = Field(..., pattern=r"^(low|medium|high)$")
    detail: str | None = None


class FraudDetectionRequest(_AIDto):
    order_id: UUID
    request_id: str | None = None


class FraudDetectionResponse(_AIDto):
    order_id: UUID
    risk_score: int = Field(..., ge=0, le=100)
    recommendation: str = Field(
        ..., pattern=r"^(allow|review|block)$",
    )
    signals: list[FraudSignal] = Field(default_factory=list)
    confidence: float = Field(..., ge=0.0, le=1.0)
    raw_response: dict[str, Any] = Field(default_factory=dict)
    provider: str
    model: str | None = None
    cost_units: Decimal | None = None


# ---------------- Provider port ----------------


class AIProvider(ABC):
    """Capability port. Adapters implement each method against their SDK.

    Capability gating: an adapter that doesn't implement a capability
    should override the method to raise ``NotImplementedError`` with a
    clear message, NOT return a stubbed response.
    """

    name: str = "abstract"

    @abstractmethod
    async def ocr_prescription(self, req: OcrRequest) -> OcrResponse: ...

    @abstractmethod
    async def suggest_medicines(
        self, req: SuggestMedicinesRequest,
    ) -> SuggestMedicinesResponse: ...

    @abstractmethod
    async def predict_stock(
        self, req: StockPredictionRequest,
    ) -> StockPredictionResponse: ...

    @abstractmethod
    async def detect_fraud(
        self, req: FraudDetectionRequest,
    ) -> FraudDetectionResponse: ...
